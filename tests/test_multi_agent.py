"""V0.3：配置期多 Agent 的串行扇出与冲突闸门。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import multi_agent as ma  # noqa: E402


def test_v03_fan_out_truncates_and_returns_only_reports_and_conflicts():
    seen = []

    def runner(prompt):
        seen.append(prompt)
        return {"text": f"answer:{prompt}", "emotion": "smile",
                "extra": "ignored"}

    out = ma.fan_out(["a", "b", "c", "d"], runner, max_agents=2)

    assert seen == ["a", "b"]
    assert set(out) == {"reports", "conflicts"}
    assert out["conflicts"] == []
    assert out["reports"] == [
        {"prompt": "a", "text": "answer:a", "emotion": "smile"},
        {"prompt": "b", "text": "answer:b", "emotion": "smile"},
    ]


def test_v03_debate_gate_flags_conflicting_texts_without_merging():
    reports = [
        {"topic": "mood", "text": " shy "},
        {"topic": "mood", "text": "angry"},
    ]

    out = ma.debate_gate(reports)

    assert out == {
        "conflicts": [{"topic": "mood", "variants": ["shy", "angry"]}],
        "ok": False,
    }


def test_v03_debate_gate_accepts_consistent_texts():
    reports = [
        {"topic": "mood", "text": " shy "},
        {"topic": "mood", "text": "shy "},
    ]

    assert ma.debate_gate(reports) == {"conflicts": [], "ok": True}


def test_v03_debate_gate_uses_prompt_as_topic_fallback():
    reports = [
        {"prompt": "greet", "text": "喵"},
        {"prompt": "greet", "text": "汪"},
    ]

    assert ma.debate_gate(reports)["conflicts"] == [
        {"topic": "greet", "variants": ["喵", "汪"]},
    ]


def test_v03_multi_agent_source_is_process_free():
    src = (ROOT / "desktop_pet" / "multi_agent.py").read_text(encoding="utf-8")

    for token in ("mailbox", "queue", "threading", "asyncio", "socket",
                  "arbiter", "pet_window", "manifest_io"):
        assert token not in src


def test_v03_multi_agent_source_does_not_write_files():
    src = (ROOT / "desktop_pet" / "multi_agent.py").read_text(encoding="utf-8")

    for token in ("open(", ".write_text", ".write_bytes", "pathlib", "Path("):
        assert token not in src
