"""v0.3.3（R3 剩余）：视觉/推理路由调用点、用量面板、密钥来源检查。

Qt-free；文件路径用 tmp_path / 环境变量，不写用户目录、不连网络。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import ai_annotate                                          # noqa: E402
import meeting as mt                                        # noqa: E402
import model_router as mr                                   # noqa: E402
import secrets_store as ss                                  # noqa: E402
import usage_meter as um                                    # noqa: E402
from ai_motion_contract import AIMotionConfig              # noqa: E402


def _routing(task_model_value="", provider=""):
    return {"enabled": True,
            "tasks": {"vision": {"provider": provider,
                                 "model": task_model_value}}}


# ---------------------------------------------------------------- 视觉路由
def test_v033_task_model_only_returns_explicit_model():
    assert mr.task_model({}, "vision") == ""
    assert mr.task_model({"routing": {"enabled": False,
                                      "tasks": {"vision": {"model": "m"}}}},
                         "vision") == ""
    assert mr.task_model({"chat": {"model": "chat-m"},
                          "routing": _routing("fast-m")}, "vision") == "fast-m"
    # 只填 provider 时不能把全局 chat.model 当成视觉模型
    assert mr.task_model({"chat": {"model": "chat-m"},
                          "routing": _routing("", provider="p-fast")},
                         "vision") == ""


def test_v033_annotate_routes_vision_model_without_mutating_base():
    base = AIMotionConfig(model="base-m")
    routed = ai_annotate.routed_config(
        base, {"routing": _routing("fast-m")})
    assert routed.model == "fast-m"
    assert base.model == "base-m"
    assert ai_annotate.routed_config(
        base, {"routing": {"enabled": False}}) is base
    assert ai_annotate.routed_config(
        base, {"chat": {"model": "chat-m"},
               "routing": _routing("", provider="p-fast")}) is base


def test_v033_annotate_worker_uses_routed_config():
    src = (ROOT / "desktop_pet" / "ui_app.py").read_text(encoding="utf-8")
    assert "routed_config(load_config())" in src


# ---------------------------------------------------------------- 推理路由
def test_v033_meeting_role_task_is_normalized():
    cfg = mt.normalize_config({"roles": [
        {"name": "a", "task": "vision"},
        {"name": "b", "task": "unknown"},
    ], "devil": {"name": "反方", "task": "reasoning"}})
    assert cfg["roles"][0]["task"] == "vision"
    assert cfg["roles"][1]["task"] == "chat"
    assert cfg["devil"]["task"] == "reasoning"


def test_v033_meeting_passes_reasoning_task_to_stream(monkeypatch):
    monkeypatch.setenv("V033_KEY", "dummy")
    base = {"chat": {"enabled": False, "base_url": "https://x.invalid",
                     "model": "m", "key_env": "V033_KEY"},
            "provider": "p", "profiles": {"p": {"auth": "bearer"}}}
    role = {"name": "动作", "sku": "a", "profile": "p",
            "task": "reasoning", "chat": {}}
    seen = {}

    def stream(cfg, sys_msg, messages, *, sku="", task="chat"):
        seen["task"] = task
        yield '{"verdict":"agree","reason":"ok"}'

    got = mt.make_ask(base, stream=stream)(role, "prompt")
    assert got["verdict"] == "agree"
    assert seen["task"] == "reasoning"


# ---------------------------------------------------------------- 用量账本
def _usage_cfg(enabled: bool) -> dict:
    return {"chat": {"usage_log": enabled}}


def test_v033_usage_default_off_writes_nothing(monkeypatch, tmp_path):
    target = tmp_path / "usage.json"
    monkeypatch.setenv("DESKTOP_PET_USAGE_PATH", str(target))
    assert um.record_usage(_usage_cfg(False), "chat", "abc", "def") == []
    assert not target.exists()


def test_v033_usage_ledger_accumulates_and_redacts(monkeypatch, tmp_path):
    target = tmp_path / "usage.json"
    monkeypatch.setenv("DESKTOP_PET_USAGE_PATH", str(target))
    lines = um.record_usage(_usage_cfg(True), "chat",
                            "secret input text", "secret output text")
    assert lines and lines[0].startswith("chat in≈")
    um.record_usage(_usage_cfg(True), "chat", "abcd", "efgh")
    again = um.ledger_lines()
    assert again and "chat" in again[0]
    raw = target.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert json.loads(raw)["version"] == 1
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_v033_usage_bad_json_resets_and_records(monkeypatch, tmp_path):
    target = tmp_path / "usage.json"
    target.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("DESKTOP_PET_USAGE_PATH", str(target))
    assert um.load_ledger().lines() == []
    lines = um.record_usage(_usage_cfg(True), "vision", "abc", "")
    assert lines == ["vision in≈2 tok out≈0 tok"]


# ---------------------------------------------------------------- 密钥来源
def test_v033_key_source_env_wins_and_hides_secret(monkeypatch):
    monkeypatch.setenv("V033_KEY", "super-secret")
    rep = ss.key_source_report({"chat": {"key_env": "V033_KEY"}})
    assert rep["source"] == "env" and rep["env_set"] is True
    assert "super-secret" not in json.dumps(rep, ensure_ascii=False)


def test_v033_key_source_file_requires_0600(monkeypatch, tmp_path):
    monkeypatch.delenv("V033_KEY", raising=False)
    key = tmp_path / "key.txt"
    key.write_text("file-secret", encoding="utf-8")
    os.chmod(key, 0o644)
    cfg = {"chat": {"key_env": "V033_KEY", "key_file": str(key)}}
    rep = ss.key_source_report(cfg)
    assert rep["source"] == "none" and rep["key_file_ok"] is False
    os.chmod(key, 0o600)
    rep = ss.key_source_report(cfg)
    assert rep["source"] == "file" and rep["key_file_ok"] is True
    assert "file-secret" not in json.dumps(rep, ensure_ascii=False)


def test_v033_key_source_none_when_all_off(monkeypatch):
    monkeypatch.delenv("V033_KEY", raising=False)
    rep = ss.key_source_report({"chat": {"key_env": "V033_KEY"}})
    assert rep["source"] == "none"
    assert rep["keyring_enabled"] is False


# ---------------------------------------------------------------- 接线守卫
def test_v033_pet_window_menus_and_handlers_are_wired():
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert "用量统计…" in src
    assert "密钥来源…" in src
    assert "def _open_usage_panel" in src
    assert "def _open_key_sources" in src
    assert "ledger_lines()" in src
    assert "key_source_report(" in src


def test_v033_chat_worker_records_usage():
    src = (ROOT / "desktop_pet" / "chat_worker.py").read_text(encoding="utf-8")
    assert "record_usage(self.cfg" in src


def test_v033_example_switches_still_off():
    cfg = json.loads((ROOT / "desktop_pet" / "agent.json.example").read_text(
        encoding="utf-8"))
    assert cfg["chat"]["usage_log"] is False
    assert cfg["routing"]["enabled"] is False
    assert cfg["chat"]["keyring"] is False
