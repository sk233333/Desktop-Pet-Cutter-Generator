"""V0.3：L2 自主循环纯逻辑层的闸门与字段裁剪。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import agent_core as ac  # noqa: E402


class _IncrementingClock:
    """每次读取前进 fixed 秒，避免依赖真实时间。"""

    def __init__(self, step: float = 0.6):
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


def test_v03_tool_whitelist_is_closed():
    assert ac.TOOL_WHITELIST == ("get_health", "get_allowed",
                                 "get_manifest_item", "propose_candidate",
                                 "notify_bubble")


def test_v03_loop_stops_at_max_steps_and_counts_calls():
    calls = []

    def step(index):
        calls.append(index)
        return {"tool": "get_health", "text": f"step-{index}"}

    result = ac.run_loop(step, max_steps=3)

    assert calls == [0, 1, 2]
    assert result.stopped_reason == "max_steps"
    assert len(result.steps) == 3
    assert result.text == "step-2"


def test_v03_loop_stops_on_timeout_with_incrementing_clock():
    calls = []

    def step(index):
        calls.append(index)
        return {"tool": "get_health", "text": "late"}

    result = ac.run_loop(step, max_steps=4, timeout_ms=1000,
                         clock=_IncrementingClock(0.6))

    assert calls == [0]
    assert result.stopped_reason == "timeout"
    assert result.steps == []
    assert result.text == ""


def test_v03_loop_stops_on_tool_outside_whitelist():
    calls = []

    def step(index):
        calls.append(index)
        return {"tool": "shell_exec", "text": "不该被执行"}

    result = ac.run_loop(step, max_steps=4)

    assert calls == [0]
    assert result.stopped_reason == "tool_not_allowed"
    assert [s.tool for s in result.steps] == ["shell_exec"]
    assert result.text == ""


def test_v03_loop_stops_when_step_has_no_new_info():
    calls = []

    def none_step(index):
        calls.append(index)
        return None

    assert ac.run_loop(none_step, max_steps=4).stopped_reason == "no_new_info"
    assert calls == [0]

    calls.clear()
    assert ac.run_loop(lambda i: (calls.append(i), {})[1],
                       max_steps=4).stopped_reason == "no_new_info"
    assert calls == [0]


def test_v03_normalize_drops_unknown_fields_and_caps_text():
    out = ac.normalize_result({
        "text": "x" * 500,
        "remember": "y" * 500,
        "emotion": "smile",
        "action": "none",
        "unknown": "secret",
    })

    assert set(out) == {"text", "remember", "emotion", "action"}
    assert out["text"] == "x" * 400
    assert out["remember"] == "y" * 400
    assert "unknown" not in out


def test_v03_loop_merges_only_allowed_fields():
    def step(index):
        return {
            "tool": "get_allowed",
            "text": "ok",
            "change_set": {"emotion": ["smile"]},
            "secret": "drop-me",
        }

    result = ac.run_loop(step, max_steps=1)

    assert result.text == "ok"
    assert result.change_set == {"emotion": ["smile"]}
    assert not hasattr(result, "secret")
    assert result.stopped_reason == "max_steps"


@pytest.mark.parametrize("bad", [0, -1, 9])
def test_v03_loop_rejects_invalid_max_steps(bad):
    with pytest.raises(ValueError):
        ac.run_loop(lambda i: {}, max_steps=bad)


def test_v03_agent_core_source_stays_pure():
    src = (ROOT / "desktop_pet" / "agent_core.py").read_text(encoding="utf-8")

    assert "import PyQt6" not in src
    assert "import arbiter" not in src
    assert "commit_changes" not in src
