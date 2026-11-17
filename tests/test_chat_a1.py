"""A1 AI 接入回归：Qt-free 纯逻辑 + provider + arbiter 边界。

CI 只装五包（没有 PyQt6），所以本文件不 import
PyQt6 / chat_worker / ui_app / pet_window / upgrade_wizard；
界面部分用离屏人工冒烟（见交付说明）。
"""

from __future__ import annotations

import ast
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import arbiter                                        # noqa: E402
import chat_contract as cc                            # noqa: E402
import chat_provider as cp                            # noqa: E402
from expr_state import STATES                         # noqa: E402


def _cfg(**chat):
    base = {
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "model": "test-model",
        "key_env": "A1_TEST_KEY",
        "timeout_ms": 1000,
        "max_tokens": 8,
        "json_mode": "object",
    }
    base.update(chat)
    return {
        "chat": base,
        "provider": "p",
        "profiles": {"p": {"surface": "openai_chat", "json_mode": "object",
                           "stream": "delta", "auth": "bearer"}},
    }


class _Resp:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_example_has_no_model_id_host_or_key():
    raw = (ROOT / "desktop_pet" / "agent.json.example").read_text(encoding="utf-8")
    cfg = json.loads(raw)
    assert cfg["chat"]["enabled"] is False
    assert cfg["chat"]["thinking"] is False
    assert cfg["chat"]["model"].startswith("<")
    assert cfg["chat"]["base_url"].startswith("<")
    low = raw.lower()
    assert "http://" not in low and "https://" not in low
    assert "sk-" not in low
    assert cfg["chat"]["key_env"] == "DESKTOP_PET_API_KEY"


def test_gitignore_blocks_local_config():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("desktop_pet/agent.json", "*.local.json", ".env"):
        assert pat in text


def test_config_missing_file_defaults_to_disabled(tmp_path):
    cfg = cc.load_agent_config(tmp_path / "missing.json")
    assert cc.chat_enabled(cfg) is False
    assert cfg["chat"]["enabled"] is False


def test_disabled_chat_makes_zero_network_calls(monkeypatch):
    called = []

    def boom(*args, **kwargs):
        called.append(1)
        raise AssertionError("disabled chat must not touch network")

    monkeypatch.setattr(cp.urllib.request, "urlopen", boom)
    cfg = _cfg(enabled=False)
    with pytest.raises(RuntimeError):
        list(cp.stream_chat(cfg, "sys", []))
    assert called == []


def test_missing_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("A1_TEST_KEY", raising=False)
    called = []
    monkeypatch.setattr(cp.urllib.request, "urlopen",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        list(cp.stream_chat(_cfg(), "sys", []))
    assert called == []


def test_http_error_propagates_to_worker_layer(monkeypatch):
    monkeypatch.setenv("A1_TEST_KEY", "dummy")

    def http_429(*args, **kwargs):
        raise urllib.error.HTTPError("u", 429, "too many", {}, None)

    monkeypatch.setattr(cp.urllib.request, "urlopen", http_429)
    with pytest.raises(urllib.error.HTTPError):
        list(cp.stream_chat(_cfg(), "sys", []))


def test_json_mode_object_sends_response_format_and_prompt(monkeypatch):
    monkeypatch.setenv("A1_TEST_KEY", "dummy")
    captured = {}

    def fake(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp([
            'data: {"choices":[{"delta":{"content":"{\\"text\\":\\"hi\\"}"}}]}',
            "data: [DONE]",
        ])

    monkeypatch.setattr(cp.urllib.request, "urlopen", fake)
    sys_msg = cc.build_system_prompt(
        {"name": "小灰", "persona": "话少", "style": "短"},
        {"emotion": ["normal", "half"], "action": []})
    out = "".join(cp.stream_chat(_cfg(), sys_msg, []))
    assert out == '{"text":"hi"}'
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "json" in sys_msg.lower()
    assert captured["body"]["messages"][0]["role"] == "system"


def test_json_mode_none_omits_response_format(monkeypatch):
    monkeypatch.setenv("A1_TEST_KEY", "dummy")
    captured = {}
    cfg = _cfg()
    cfg["profiles"]["p"]["json_mode"] = "none"

    def fake(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(["data: [DONE]"])

    monkeypatch.setattr(cp.urllib.request, "urlopen", fake)
    list(cp.stream_chat(cfg, "sys", []))
    assert "response_format" not in captured["body"]


def test_cumulative_stream_yields_only_new_suffix(monkeypatch):
    monkeypatch.setenv("A1_TEST_KEY", "dummy")
    cfg = _cfg()
    cfg["profiles"]["p"]["stream"] = "cumulative"

    def fake(req, timeout=None):
        return _Resp([
            'data: {"choices":[{"message":{"content":"你"}}]}',
            'data: {"choices":[{"message":{"content":"你好"}}]}',
            "data: [DONE]",
        ])

    monkeypatch.setattr(cp.urllib.request, "urlopen", fake)
    assert "".join(cp.stream_chat(cfg, "sys", [])) == "你好"


@pytest.mark.parametrize("raw", [
    "",
    "not json",
    "[1, 2]",
    '{"text": 1, "emotion": "normal", "action": "none"}',
    json.dumps({"text": "x" * 5000, "emotion": "normal", "action": "none"}),
])
def test_safe_parse_invalid_falls_back_to_default(raw):
    assert cc.safe_parse(raw) == cc.DEFAULT_REPLY
    assert cc.safe_parse(raw, None) is None


def test_safe_parse_valid_keeps_fields():
    reply = cc.safe_parse('{"text":"嗨","emotion":"half","action":"none"}')
    assert reply == {"text": "嗨", "emotion": "half", "action": "none"}


def test_partial_text_streams_only_text_field():
    assert cc.partial_text('{"text":"你') == "你"
    assert cc.partial_text('{"text":"你好","emotion":"half"}') == "你好"
    assert cc.partial_text('{"te') == ""
    assert cc.partial_text('{"text":"a\\"b') == 'a"b'
    # P0-b：抽不到 text 时不能抛异常 / 不能空白卡死
    assert cc.partial_text(None) == ""
    assert cc.partial_text("") == ""
    assert cc.partial_text("你好呀") == "你好呀"
    assert cc.partial_text("x" * 80) == "x" * cc.MAX_TEXT


def test_clamp_truncates_and_normalizes():
    allowed = {"emotion": ["normal", "half"], "action": ["wave"]}
    out = cc.clamp({"text": "字" * 40, "emotion": "surprise", "action": "dance"},
                   allowed)
    assert len(out["text"]) == cc.MAX_TEXT
    assert out["emotion"] == "normal"
    assert out["action"] == "none"
    ok = cc.clamp({"text": "好", "emotion": "half", "action": "wave"}, allowed)
    assert ok == {"text": "好", "emotion": "half", "action": "wave"}


def test_derive_allowed_excludes_blink_and_smile():
    allowed = cc.derive_allowed("bgy", STATES, {}, None)
    assert "blink" not in allowed["emotion"]
    assert "smile" not in allowed["emotion"]
    assert allowed["emotion"] == ["normal", "half", "surprise", "wink_l", "wink_r"]


def test_derive_allowed_trims_per_sku_effects():
    a = cc.derive_allowed("a", STATES, {"wave": {}}, {"enabled": ["half"]})
    b = cc.derive_allowed("b", STATES, {"wave": {}}, None)
    assert a["emotion"] == ["normal", "half"]
    assert "surprise" in b["emotion"]
    assert a["action"] == ["wave"] and b["action"] == ["wave"]


def test_build_system_prompt_contains_vocab_and_no_blink():
    prompt = cc.build_system_prompt(
        {"name": "小灰", "persona": "话少", "style": "一句"},
        {"emotion": ["normal", "half"], "action": ["wave"]})
    assert "小灰" in prompt
    # P0-a：json_object 模式的稳妥条件是小写 "json" 字样必须显式出现
    assert "json" in prompt
    assert "half" in prompt and "wave" in prompt
    assert "不会眨眼" in prompt


def test_arbiter_rejects_agent_source_and_timeline_wins():
    a = arbiter.Arbiter()
    assert a.request("agent", "expr", "half", 1000, 0) is False
    assert a.request("interaction", "expr", "half", 1000, 0) is True
    assert a.request("idle", "expr", "surprise", 1000, 0) is False
    assert a.request("interaction", "expr", "normal", 1000, 0) is False
    assert a.resolve(0)["expr"] == "half"
    a.set_timeline_blink("half", now_ms=0)
    assert a.resolve(0)["expr"] == "half"
    assert a.request("interaction", "expr", "normal", 1000, 0) is False


def test_bubble_priority_and_streaming_guard_shape():
    a = arbiter.Arbiter()
    assert a.request("interaction", "bubble", "你好", 6000, 0) is True
    assert a.request("idle", "bubble", "♪", 600, 1000) is False
    assert a.request("idle", "gaze", (1.0, 1.0), 600, 1000) is True
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert 'if eff.get("bubble") and not self._chat_streaming:' in src


def test_pet_window_has_no_legacy_state_writes():
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    for forbidden in ("sm.set(", "from_agent(", "expr_state.set(",
                      "self.expr_state", 'request("agent"'):
        assert forbidden not in src
    assert 'request("interaction", "expr"' in src
    assert 'def _bubble(self, text: str, state: str = "normal", hold_ms: int = 2000)' in src
    assert src.count("feedback.connect(self._bubble)") == 2


def test_arbiter_matrix_bytes_and_contract_isolated():
    src = (ROOT / "desktop_pet" / "arbiter.py").read_text(encoding="utf-8")
    assert 'PRIORITY = {"timeline": 3, "interaction": 2, "idle": 1}' in src
    assert 'CHANNELS = ("blink", "expr", "gaze", "body", "bubble")' in src
    assert '"agent"' not in src
    contract = (ROOT / "desktop_pet" / "chat_contract.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(contract)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert "upgrade_core" not in mods
    assert re.search(r"def derive_allowed\(", contract)


def test_test_collection_chain_has_no_qt_imports():
    src = Path(__file__).read_text(encoding="utf-8")
    for name in ("PyQt6", "ui_app", "chat_worker", "pet_window",
                 "upgrade_wizard"):
        assert f"import {name}" not in src
        assert f"from {name}" not in src
