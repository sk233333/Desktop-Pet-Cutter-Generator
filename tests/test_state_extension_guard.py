"""⑦ 授权已用：核完六处后判定本次不新增状态；只钉住现有契约。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_motion_contract import EXPRESSION_STATES  # noqa: E402
from chat_contract import derive_allowed  # noqa: E402
from expr_state import STATES, ExprState  # noqa: E402

SEVEN = ("normal", "blink", "half", "surprise", "wink_l", "wink_r", "smile")


def test_v03_states_remain_seven():
    assert STATES == SEVEN


def test_v03_overlay_keys_are_not_states():
    assert set(EXPRESSION_STATES) == {"joy", "sleepy", "angry", "sad", "shy"}
    assert set(EXPRESSION_STATES).isdisjoint(STATES)


def test_v03_allowed_emotions_exclude_overlay_keys():
    allowed = derive_allowed("demo", STATES, {"bounce": {}}, None)["emotion"]
    assert set(allowed) == {"normal", "half", "surprise", "wink_l",
                            "wink_r", "smile"}
    assert not (set(allowed) & set(EXPRESSION_STATES))


def test_v03_unknown_state_falls_back_and_smile_is_not_alias():
    st = ExprState()
    assert st.from_agent("not_a_state") == "normal"
    assert st.from_agent("smile") == "smile"
    # 历史语义 alias 仍指向真实状态，不新增状态名
    assert st.from_agent("shy") == "half"
    assert st.from_agent("angry") == "half"
