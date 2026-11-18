"""T-M5：嘴型 overlay 不影响运行时词表（C-M13）。"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from chat_contract import derive_allowed  # noqa: E402
from expr_state import STATES  # noqa: E402


def test_derive_allowed_ignores_mouth_dir():
    out = derive_allowed("sku", STATES, {"wave": 1}, None)
    assert "smile" not in out["emotion"]
    assert "blink" not in out["emotion"]
    assert out["action"] == ["wave"]
    # 词表只从 STATES / registry / effects 反推，不读 manifest item
    assert "mouth_dir" not in inspect.getsource(derive_allowed)
