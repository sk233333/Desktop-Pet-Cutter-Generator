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


def test_b2_unwired_smile_excluded_from_vocabulary_and_clamped():
    """R2-A 中间态：STATES 已含 smile，但消费未接线 → 词表排除、clamp 归 normal。"""
    allowed = derive_allowed("sku", STATES, {}, None)
    assert "smile" not in allowed["emotion"]
    out = clamp({"text": "好", "emotion": "smile", "action": "none"}, allowed)
    assert out["emotion"] == "normal"


def test_b2_legacy_smile_strings_do_not_crash():
    """旧 effects / agent 字符串：不崩溃，按当前词表裁剪。"""
    allowed = derive_allowed("sku", STATES, {}, {"enabled": ["smile", "half"]})
    assert "smile" not in allowed["emotion"]
    assert "half" in allowed["emotion"]
