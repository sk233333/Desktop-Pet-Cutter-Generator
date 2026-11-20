"""W6/W7：按 SKU memory、独立 Agent 窄协议、对话语义提示、可选语音。

全部 Qt-free，CI 五包可跑；不连网络、不起真实子进程（runner 注入）。
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import agent_bridge                                       # noqa: E402
import chat_contract as cc                                # noqa: E402
import chat_provider as cp                                # noqa: E402
import memory_store as ms                                 # noqa: E402
import voice_io                                           # noqa: E402


def _agent_cfg(**agent):
    base = {"enabled": True, "command": "fake-agent --stdio", "timeout_ms": 500}
    base.update(agent)
    return {"chat": {"enabled": True, "base_url": "https://x.invalid",
                     "model": "m", "key_env": "K"},
            "provider": "p",
            "profiles": {"p": {"surface": "agent_stdio"}},
            "agent": base}


# ---------------------------------------------------------------- memory
def test_memory_atomic_write_and_dedupe(tmp_path):
    first = ms.append_memory("bgy", "喜欢在下午睡觉", tmp_path)
    assert first and first["text"] == "喜欢在下午睡觉"
    p = tmp_path / "bgy.json"
    assert p.exists() and not (tmp_path / "bgy.json.tmp").exists()
    if stat.S_IMODE(p.stat().st_mode) != 0o600:
        assert stat.S_IMODE(p.stat().st_mode) in (0o600, 0o644)

    assert ms.append_memory("bgy", "喜欢在下午睡觉", tmp_path) is None
    assert [e["text"] for e in ms.load_memory("bgy", tmp_path)] == ["喜欢在下午睡觉"]


def test_memory_sku_guard_blocks_path_escape(tmp_path):
    assert ms.memory_path("../escape", tmp_path) is None
    assert ms.memory_path("a/b", tmp_path) is None
    assert ms.append_memory("../escape", "x", tmp_path) is None
    assert not any(tmp_path.iterdir())


def test_memory_trigger_needs_explicit_words():
    assert ms.extract_trigger("记住：我不吃香菜") == "我不吃香菜"
    assert ms.extract_trigger("请记住 我喜欢下雨天") == "我喜欢下雨天"
    assert ms.extract_trigger("今天天气不错") is None
    assert ms.extract_trigger("记住") is None


def test_memory_is_not_fed_into_derive_allowed():
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    call = src[src.index("derive_allowed("):src.index("derive_allowed(") + 400]
    assert "memory" not in call


# ---------------------------------------------------------------- agent
def test_agent_disabled_makes_no_process():
    cfg = _agent_cfg(enabled=False)
    called = []
    with pytest.raises(agent_bridge.AgentError):
        agent_bridge.respond(cfg, {}, runner=lambda *a: called.append(1))
    assert called == []


def test_agent_parses_narrow_protocol():
    cfg = _agent_cfg()
    seen = {}

    def runner(cmd, stdin, timeout):
        seen["cmd"], seen["stdin"], seen["timeout"] = cmd, stdin, timeout
        return json.dumps({"text": "喵", "emotion": "smile",
                           "action": "none", "mood_hint": "shy",
                           "remember": "喜欢毛线"}) + "\n"

    got = agent_bridge.respond(cfg, {"sku": "bgy"}, runner=runner)
    assert seen["cmd"] == ["fake-agent", "--stdio"]
    assert json.loads(seen["stdin"])["sku"] == "bgy"
    assert got == {"text": "喵", "emotion": "smile", "action": "none",
                   "mood_hint": "shy", "remember": "喜欢毛线"}


def test_agent_rejects_bad_output_and_timeout():
    cfg = _agent_cfg()
    with pytest.raises(agent_bridge.AgentError):
        agent_bridge.respond(cfg, {}, runner=lambda *a: "not json")
    with pytest.raises(agent_bridge.AgentError):
        agent_bridge.respond(cfg, {}, runner=lambda *a: json.dumps(
            {"mood_hint": "joy"}))

    def boom(*a):
        raise subprocess.TimeoutExpired("fake-agent", 1)

    with pytest.raises(agent_bridge.AgentError):
        agent_bridge.respond(cfg, {}, runner=boom)


def test_agent_surface_goes_through_stream_chat(monkeypatch):
    cfg = _agent_cfg()
    monkeypatch.setattr(agent_bridge, "respond",
                        lambda cfg, payload: {"text": "你好"})
    chunks = list(cp.stream_chat(cfg, "sys", [{"role": "user", "content": "hi"}],
                                 sku="bgy"))
    assert json.loads("".join(chunks)) == {"text": "你好"}


# ---------------------------------------------------------------- 语义提示
def test_clamp_keeps_closed_mood_hint_only():
    allowed = {"emotion": ["normal", "smile"], "action": []}
    got = cc.clamp({"text": "嗯", "emotion": "smile", "action": "none",
                    "mood_hint": "shy", "remember": " 喜欢毛线 "}, allowed)
    assert got["mood_hint"] == "shy"
    assert got["remember"] == "喜欢毛线"
    bad = cc.clamp({"text": "嗯", "emotion": "smile", "action": "none",
                    "mood_hint": "furious"}, allowed)
    assert "mood_hint" not in bad
    assert "furious" not in allowed["emotion"]


def test_system_prompt_includes_memory_and_closed_hints():
    text = cc.build_system_prompt({"name": "小灰"}, {"emotion": ["normal"],
                                                    "action": []},
                                  ["喜欢毛线"])
    assert "喜欢毛线" in text
    assert "mood_hint" in text and "joy" in text and "shy" in text
    assert "remember" in text


# ---------------------------------------------------------------- 语音
def test_voice_is_off_by_default():
    assert voice_io.speak({}, "你好") is False
    assert voice_io.transcribe({}, "/tmp/a.wav") is None
    assert voice_io.tts_ready({"voice": {"enabled": False, "tts": "x"}}) is False


def test_voice_uses_configured_command():
    cfg = {"voice": {"enabled": True, "tts": "say", "asr": "asr"}}
    seen = {}

    def runner(cmd, stdin, timeout):
        seen.setdefault("cmd", []).append((cmd, stdin, timeout))
        return "  识别结果 \n"

    assert voice_io.speak(cfg, "你好", runner=runner) is True
    assert voice_io.transcribe(cfg, "/tmp/a.wav", runner=runner) == "识别结果"
    assert seen["cmd"][0][0] == ["say"] and seen["cmd"][0][1] == "你好".encode()
    assert seen["cmd"][1][0] == ["asr"]
