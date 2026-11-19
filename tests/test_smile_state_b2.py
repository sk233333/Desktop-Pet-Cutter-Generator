"""B-2：smile 独立状态的状态表、词表与失效回退（Qt-free）。

R2-A 先钉「已登记 / 消费未接线」的中间态；R2-B 接线后同一批节点翻转为
「词表含 smile」，并新增 PetWindow 消费源码断言。这里不 import PyQt6。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from chat_contract import clamp, derive_allowed  # noqa: E402
from expr_state import STATES, ExprBank, ExprState  # noqa: E402

GOLDEN = ("normal", "blink", "half", "surprise", "wink_l", "wink_r", "smile")


def test_b2_state_table_golden():
    assert STATES == GOLDEN


def test_b2_alias_removed_but_other_aliases_kept():
    src = inspect.getsource(ExprState.from_agent)
    assert "'smile': 'half'" not in src
    assert ExprState().from_agent("smile") == "smile"
    assert ExprState().from_agent("shy") == "half"
    assert ExprState().from_agent("happy") == "surprise"


def test_b2_unknown_emotion_still_falls_back_to_normal():
    assert ExprState().set("not_a_state") == "normal"


def test_b2_apply_smile_without_patch_is_identity():
    """ExprBank 不烘焙 smile 贴片：无 patch 时原样返回，不会变半眼。"""
    frame = np.zeros((6, 6, 4), np.uint8)
    assert ExprBank().apply(frame, "smile") is frame


def test_b2_wired_smile_in_vocabulary_and_passes_clamp():
    """R2-B：消费已接线 → 词表含 smile，clamp 放行而不是归 normal。"""
    allowed = derive_allowed("sku", STATES, {}, None)
    assert "smile" in allowed["emotion"]
    out = clamp({"text": "好", "emotion": "smile", "action": "none"}, allowed)
    assert out["emotion"] == "smile"


def test_b2_legacy_smile_strings_do_not_crash():
    """旧 effects / agent 字符串：不崩溃，按当前词表裁剪。"""
    allowed = derive_allowed("sku", STATES, {}, {"enabled": ["smile", "half"]})
    assert "smile" in allowed["emotion"]
    assert "half" in allowed["emotion"]


def test_b2_petwindow_has_smile_overlay_branch():
    """Qt-free 源码断言：PetWindow 读 smile_geometry 并在 smile 分支合成。"""
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert "def _load_smile_overlay" in src
    assert 'item.get("smile_geometry")' in src
    assert 'slot.get("overlay")' in src
    start = src.index("def _expr_frame")
    end = src.index("def _gaze_eyes")
    body = src[start:end]
    assert 'state == "smile"' in body
    assert body.index('state == "smile"') < body.index("expr_bank().apply")
    assert "self._smile.apply(self._render_rgba)" in body
