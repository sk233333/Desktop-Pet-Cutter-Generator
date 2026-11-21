"""v0.3.1（R3）：多模型路由 / 重试开关 / 费用类别 / 可选钥匙串 + 接线守卫。

全部 Qt-free；不连网络、不起真实子进程（opener / runner 注入）。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import agent_bridge                                       # noqa: E402
import agent_core as ac                                   # noqa: E402
import chat_contract as cc                                # noqa: E402
import chat_provider as cp                                # noqa: E402
import cloud_office as co                                 # noqa: E402
import model_router as mr                                 # noqa: E402
import secrets_store as ss                                # noqa: E402
import usage_meter as um                                  # noqa: E402


# ---------------------------------------------------------------- 多模型路由
def test_v031_routing_default_off_keeps_legacy():
    cfg = {"chat": {"model": "legacy-model"}, "provider": "legacy-p"}
    assert mr.routing_enabled(cfg) is False
    assert mr.resolve_route(cfg) == {"provider": "legacy-p",
                                     "model": "legacy-model",
                                     "routed": False}


def test_v031_routing_selects_task_and_empty_falls_back():
    cfg = {
        "chat": {"model": "m0"},
        "provider": "p0",
        "routing": {
            "enabled": True,
            "tasks": {
                "chat": {"provider": "", "model": ""},
                "vision": {"provider": "p-fast", "model": "m-fast"},
                "reasoning": {"provider": "p-think", "model": ""},
            },
        },
    }
    assert mr.resolve_route(cfg, "vision") == {
        "provider": "p-fast", "model": "m-fast", "routed": True}
    assert mr.resolve_route(cfg, "reasoning") == {
        "provider": "p-think", "model": "m0", "routed": True}
    assert mr.resolve_route(cfg, "chat") == {
        "provider": "p0", "model": "m0", "routed": False}
    assert mr.resolve_route(cfg, "unknown") == {
        "provider": "p0", "model": "m0", "routed": False}


def test_v031_provider_uses_routed_model(monkeypatch):
    monkeypatch.setenv("R3_KEY", "dummy")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}
                              ).encode("utf-8")

    def fake(req, timeout):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(cp.urllib.request, "urlopen", fake)
    cfg = {
        "chat": {"enabled": True, "base_url": "https://x.invalid/v1",
                 "model": "m0", "key_env": "R3_KEY", "timeout_ms": 1000},
        "provider": "p0",
        "profiles": {
            "p0": {"surface": "openai_chat", "stream": "none", "auth": "bearer"},
            "pfast": {"surface": "openai_chat", "stream": "none",
                      "auth": "bearer"},
        },
        "routing": {"enabled": True,
                    "tasks": {"vision": {"provider": "pfast",
                                         "model": "m-vision"}}},
    }
    chunks = list(cp.stream_chat(cfg, "sys", [{"role": "user", "content": "hi"}],
                                 task="vision"))
    assert chunks == ["ok"]
    assert seen["body"]["model"] == "m-vision"


# ---------------------------------------------------------------- 重试 / 流式
def test_v031_retry_default_off_and_capped():
    assert cc.retry_attempts({}) == 1
    assert cc.retry_attempts({"chat": {}}) == 1
    assert cc.retry_attempts({"chat": {"retry": 0}}) == 1
    assert cc.retry_attempts({"chat": {"retry": 1}}) == 2
    assert cc.retry_attempts({"chat": {"retry": 9}}) == 3
    assert cc.retry_attempts({"chat": {"retry": "bad"}}) == 1


def test_v031_emitted_text_is_never_retried():
    src = (ROOT / "desktop_pet" / "chat_worker.py").read_text(encoding="utf-8")
    assert "if emitted[0] or attempt == attempts - 1" in src
    assert "retry_attempts(self.cfg)" in src
    # 超时仍由 provider 的 urllib timeout 硬停，不在 worker 里无限等
    provider = (ROOT / "desktop_pet" / "chat_provider.py").read_text(
        encoding="utf-8")
    assert "timeout=timeout" in provider


# ---------------------------------------------------------------- 费用类别
def test_v031_usage_meter_only_categories():
    meter = um.UsageMeter()
    meter.record("chat", "a" * 10, "b" * 4)
    meter.record("vision", "c" * 3, "")
    assert meter.lines() == [
        "chat in≈5 tok out≈2 tok",
        "vision in≈2 tok out≈0 tok",
    ]
    joined = "\n".join(meter.lines())
    assert "aaaa" not in joined and "bbb" not in joined


def test_v031_usage_log_defaults_off():
    assert um.meter_from_config({}) is None
    assert um.meter_from_config({"chat": {}}) is None
    assert um.meter_from_config({"chat": {"usage_log": False}}) is None
    assert isinstance(um.meter_from_config({"chat": {"usage_log": True}}),
                      um.UsageMeter)
    assert um.estimate_tokens("") == 0
    assert um.estimate_tokens("a b") == 1


# ---------------------------------------------------------------- 钥匙串
def test_v031_key_env_wins_over_file(monkeypatch, tmp_path):
    monkeypatch.setenv("R3_KEY", "env-key")
    p = tmp_path / "key.txt"
    p.write_text("file-key", encoding="utf-8")
    os.chmod(p, 0o600)
    cfg = {"chat": {"key_env": "R3_KEY", "key_file": str(p)}}
    assert ss.resolve_key(cfg) == "env-key"


def test_v031_key_file_must_be_0600(monkeypatch, tmp_path):
    monkeypatch.delenv("R3_KEY", raising=False)
    p = tmp_path / "key.txt"
    p.write_text("file-key", encoding="utf-8")
    os.chmod(p, 0o644)
    cfg = {"chat": {"key_env": "R3_KEY", "key_file": str(p)}}
    if os.name == "posix":
        assert ss.resolve_key(cfg) == ""
        os.chmod(p, 0o600)
    assert ss.resolve_key(cfg) == "file-key"


def test_v031_keyring_is_optional_and_off_by_default(monkeypatch):
    monkeypatch.delenv("R3_KEY", raising=False)
    called = []

    mod = types.ModuleType("keyring")
    mod.get_password = lambda service, name: (
        called.append((service, name)) or "ring-key")
    monkeypatch.setitem(sys.modules, "keyring", mod)

    assert ss.resolve_key({"chat": {"key_env": "R3_KEY"}}) == ""
    assert called == []

    assert ss.resolve_key({"chat": {"key_env": "R3_KEY",
                                    "keyring": True}}) == "ring-key"
    assert called == [("desktop_pet", "R3_KEY")]


# ---------------------------------------------------------------- L2 接线
def test_v031_loop_config_default_off_and_bounded():
    assert ac.loop_config({})["enabled"] is False
    assert ac.loop_config({"agent": {"enabled": False,
                                     "max_steps": 4}})["enabled"] is False
    assert ac.loop_config({"agent": {"enabled": True,
                                     "max_steps": 1}})["enabled"] is False
    on = ac.loop_config({"agent": {"enabled": True, "max_steps": 3,
                                   "timeout_ms": 1000}})
    assert on == {"enabled": True, "max_steps": 3, "timeout_ms": 1000.0}
    assert ac.loop_config({"agent": {"enabled": True,
                                     "max_steps": 99}})["max_steps"] == 8


def test_v031_worker_wires_run_loop_only_when_enabled():
    src = (ROOT / "desktop_pet" / "chat_worker.py").read_text(encoding="utf-8")
    assert "loop_config(self.cfg)" in src
    assert "run_loop(step" in src
    assert "if loop[\"enabled\"]:" in src


def test_v031_agent_bridge_passes_report_and_change_set():
    cfg = {"chat": {"enabled": True},
           "provider": "p", "profiles": {"p": {"surface": "agent_stdio"}},
           "agent": {"enabled": True, "command": "fake-agent",
                     "timeout_ms": 500}}
    raw = json.dumps({"report": {"continue": True},
                      "change_set": {"emotion": ["smile"]}})
    got = agent_bridge.respond(cfg, {}, runner=lambda *a: raw)
    assert got["report"] == {"continue": True}
    assert got["change_set"] == {"emotion": ["smile"]}


def test_v031_parse_step_reply_allows_candidate_only_steps():
    assert ac.parse_step_reply(json.dumps({"report": {"continue": True}})) == {
        "report": {"continue": True}}
    assert ac.parse_step_reply(json.dumps({"change_set": {"x": 1},
                                           "text": "ok"})) == {
        "text": "ok", "change_set": {"x": 1}}
    assert ac.parse_step_reply("not json") == {}
    assert ac.parse_step_reply(json.dumps({"unknown": 1})) == {}


# ---------------------------------------------------------------- 云办公 UI
def test_v031_cloud_poster_never_runs_when_disabled(monkeypatch):
    monkeypatch.delenv("R3_OFFICE", raising=False)
    calls = []
    poster = co.http_poster({}, opener=lambda *a, **k: calls.append(1))
    got = co.send_event({}, {"kind": "manual", "text": "x"}, poster=poster)
    assert got == {"delivered": False, "reason": "disabled"}
    assert calls == []


def test_v031_cloud_poster_sends_minimal_payload_when_ready(monkeypatch):
    monkeypatch.setenv("R3_OFFICE", "secret")
    cfg = {"enabled": True, "consent": True,
           "base_url": "https://office.invalid/api",
           "key_env": "R3_OFFICE"}
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    poster = co.http_poster(cfg, opener=opener)
    got = co.send_event(
        cfg, {"kind": "manual", "text": "hi", "emotion": "sleepy",
              "url": "https://secret.invalid", "action": "send_mail"},
        poster=poster)
    assert got == {"delivered": True, "reason": "ok"}
    assert seen["url"] == "https://office.invalid/api"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {"kind": "manual", "text": "hi",
                            "emotion": "sleepy"}


def test_v031_cloud_menu_calls_send_event_with_ready_check():
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert "发送办公事件…" in src
    assert "_open_cloud_event" in src
    assert "cloud_ready(cfg)" in src
    assert "http_poster(cfg)" in src


# ---------------------------------------------------------------- 界面保存按钮
def test_v031_annotate_save_button_uses_single_write_path():
    src = (ROOT / "desktop_pet" / "ui_app.py").read_text(encoding="utf-8")
    assert "保存草案到 manifest" in src
    assert "from annotation_draft import save_draft" in src
    assert "save_draft(info, idx, report)" in src


# ---------------------------------------------------------------- 配置默认值
def test_v031_example_new_switches_all_off():
    cfg = json.loads((ROOT / "desktop_pet" / "agent.json.example").read_text(
        encoding="utf-8"))
    assert cfg["chat"]["retry"] == 0
    assert cfg["chat"]["usage_log"] is False
    assert cfg["chat"]["keyring"] is False
    assert cfg["chat"]["key_file"] == ""
    assert cfg["routing"]["enabled"] is False
    assert cfg["cloud"]["enabled"] is False


def test_v031_load_agent_config_keeps_new_sections(tmp_path):
    p = tmp_path / "agent.json"
    p.write_text(json.dumps({"routing": {"enabled": False},
                             "cloud": {"enabled": False},
                             "multi_agent": {"enabled": False}}),
                 encoding="utf-8")
    cfg = cc.load_agent_config(p)
    assert cfg["routing"] == {"enabled": False}
    assert cfg["cloud"] == {"enabled": False}
    assert cfg["multi_agent"] == {"enabled": False}
