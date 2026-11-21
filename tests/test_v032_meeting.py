"""v0.3.2：配置期多角色评审（开会）纯逻辑与接线守卫。

全部 Qt-free；ask / stream / clock 均注入，不连真实 API。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import meeting as mt                                       # noqa: E402


def _cfg(**over):
    raw = {
        "enabled": True,
        "max_rounds": 2,
        "timeout_ms": 5000,
        "roles": [{"name": "视觉", "sku": "a"},
                  {"name": "动作", "sku": "b"}],
        "devil": {"name": "反方"},
    }
    raw.update(over)
    return mt.normalize_config(raw)


def _agree(role, prompt):
    return {"verdict": "agree", "reason": "ok",
            "trace": {"ttft_ms": 100, "first5s": 10, "total": 10}}


def test_v032_missing_config_is_disabled(tmp_path):
    cfg = mt.load_meeting_config(tmp_path / "missing.json")
    assert cfg["enabled"] is False
    assert mt.meeting_ready(cfg) is False
    calls = []
    got = mt.discuss({}, "plan", cfg=cfg,
                     ask=lambda role, prompt: calls.append(role) or _agree(role, prompt))
    assert got["status"] == "disabled"
    assert got["opinions"] == []
    assert calls == []


def test_v032_normalize_bounds_rounds_timeout_roles():
    raw = {"enabled": True, "max_rounds": 99, "timeout_ms": 1,
           "roles": [{"name": f"r{i}"} for i in range(9)]}
    cfg = mt.normalize_config(raw)
    assert cfg["max_rounds"] == mt.MAX_ROUNDS
    assert cfg["timeout_ms"] == 1000
    assert len(cfg["roles"]) == mt.MAX_ROLES
    assert cfg["devil"]["name"] == "反方"
    assert mt.normalize_config({"max_rounds": 0})["max_rounds"] == 1


def test_v032_all_agree_only_marks_ready_for_human_review():
    seen = []

    def ask(role, prompt):
        seen.append((role["name"], prompt))
        return _agree(role, prompt)

    got = mt.discuss({}, "方案 A", cfg=_cfg(), ask=ask)
    assert got["status"] == "ready_for_human_review"
    assert got["reason"] == "all_agree"
    assert [o["role"] for o in got["opinions"]] == ["视觉", "动作", "反方"]
    assert all(o["verdict"] == "agree" for o in got["opinions"])
    # 后面的角色能看到前面意见，但中间列表只在内存
    assert "前面意见" in seen[1][1] and "视觉" in seen[1][1]
    assert set(got) == {"status", "reason", "opinions", "trace", "roles"}


def test_v032_any_reject_becomes_conflict():
    def ask(role, prompt):
        if role["name"] == "动作":
            return {"verdict": "reject", "reason": "会绕人工确认"}
        return _agree(role, prompt)

    got = mt.discuss({}, "方案 B", cfg=_cfg(), ask=ask)
    assert got["status"] == "conflict"
    assert got["reason"] == "rejected"
    assert got["opinions"][-1]["verdict"] == "reject"
    assert "人工确认" in got["opinions"][-1]["reason"]


def test_v032_devil_is_always_asked():
    names = []

    def ask(role, prompt):
        names.append(role["name"])
        return _agree(role, prompt)

    mt.discuss({}, "方案 C", cfg=_cfg(), ask=ask)
    assert names[-1] == "反方"


def test_v032_timeout_hard_stops_before_next_request():
    tick = [0.0]

    def clock():
        tick[0] += 10.0
        return tick[0]

    calls = []
    got = mt.discuss({}, "方案 D", cfg=_cfg(timeout_ms=1000),
                     ask=lambda role, prompt: calls.append(role) or _agree(role, prompt),
                     clock=clock)
    assert got["status"] == "conflict"
    assert got["reason"] == "timeout"
    assert calls == []


def test_v032_missing_key_fails_before_stream(monkeypatch):
    monkeypatch.delenv("V032_NO_KEY", raising=False)
    base = {"chat": {"enabled": False, "key_env": "V032_NO_KEY"},
            "provider": "p", "profiles": {"p": {"auth": "bearer"}}}
    role = {"name": "视觉", "sku": "a", "profile": "p",
            "chat": {"base_url": "https://meeting.invalid/v1",
                     "model": "m", "key_env": "V032_NO_KEY"}}
    calls = []

    def stream(*a, **k):
        calls.append(1)
        yield "{}"

    ask = mt.make_ask(base, stream=stream)
    with pytest.raises(mt.MeetingError):
        ask(role, "prompt")
    assert calls == []


def test_v032_per_role_provider_and_token_trace(monkeypatch):
    monkeypatch.setenv("V032_KEY", "dummy")
    base = {"chat": {"enabled": False, "base_url": "https://base.invalid/v1",
                     "model": "base-model", "key_env": "V032_KEY"},
            "provider": "p", "profiles": {"p": {"auth": "bearer"}}}
    role = {"name": "视觉", "sku": "sku-a", "profile": "p",
            "chat": {"base_url": "https://role.invalid/v1",
                     "model": "role-model", "key_env": "V032_KEY"}}
    seen = {}

    def stream(cfg, sys_msg, messages, *, sku="", task="chat"):
        seen["model"] = cfg["chat"]["model"]
        seen["enabled"] = cfg["chat"]["enabled"]
        seen["sku"] = sku
        yield '{"verdict":"agree",'
        yield '"reason":"ok"}'

    got = mt.make_ask(base, stream=stream)(role, "prompt")
    assert got["verdict"] == "agree"
    assert got["trace"]["total"] > 0
    assert got["trace"]["first5s"] == got["trace"]["total"]
    assert seen == {"model": "role-model", "enabled": True, "sku": "sku-a"}
    # 只改本次副本，不翻转全局关闭状态
    assert base["chat"]["enabled"] is False


def test_v032_extra_fields_do_not_leak_into_opinions():
    def ask(role, prompt):
        return {"verdict": "agree", "reason": "ok", "tool": "exec",
                "url": "https://secret.invalid", "key": "sk-xxx"}

    got = mt.discuss({}, "方案 E", cfg=_cfg(), ask=ask)
    assert got["status"] == "ready_for_human_review"
    for op in got["opinions"]:
        assert set(op) == {"role", "round", "verdict", "reason"}
    text = json.dumps(got, ensure_ascii=False)
    assert "secret.invalid" not in text and "sk-xxx" not in text


def test_v032_meeting_never_writes_or_commits():
    src = (ROOT / "desktop_pet" / "meeting.py").read_text(encoding="utf-8")
    assert "commit_changes" not in src
    assert "save_draft" not in src
    assert "manifest_io" not in src


def test_v032_ui_and_overlay_are_wired():
    ui = (ROOT / "desktop_pet" / "ui_app.py").read_text(encoding="utf-8")
    assert "多角色开会评审" in ui
    assert "MeetingWorker" in ui
    assert "ready_for_human_review" in ui
    assert "_save_annotate_draft(report, dlg)" in ui

    pet = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert "def show_token_stats" in pet
    assert "（估算）" in pet
    assert "TTFT" in pet

    worker = (ROOT / "desktop_pet" / "meeting_worker.py").read_text(
        encoding="utf-8")
    assert "QThread" in worker and "discuss" in worker


def test_v032_example_is_default_off_without_ids_or_hosts():
    raw = (ROOT / "desktop_pet" / "meeting.json.example").read_text(
        encoding="utf-8")
    data = json.loads(raw)
    assert data["enabled"] is False
    assert "http://" not in raw and "https://" not in raw
    assert "sk-" not in raw
    for role in data["roles"] + [data["devil"]]:
        assert role["chat"]["base_url"] == ""
        assert role["chat"]["model"] == ""
