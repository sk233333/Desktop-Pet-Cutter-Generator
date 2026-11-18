"""T-M4：运行时四类失败、双 SKU、200 帧零写盘与状态表不变。

与 test_pet_window_mouth.py 一样，CI 五包无 PyQt6 时整体 skip。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtWidgets import QApplication  # noqa: E402

from anim import resize_rgba  # noqa: E402
from expr_state import STATES, ExprBank, ExprState  # noqa: E402
from mouth_runtime_utils import ROOT, make_sku, win  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _assert_runtime_fallback(tmp_path: Path, bad: str):
    _, asset, _, _ = make_sku(tmp_path, mouth=True, bad=bad)
    w = win(asset)          # 不传 item，走 manifest 重读
    try:
        assert w._mouth is None
        assert w._render_rgba is asset.rgba
        assert np.array_equal(w.base_rgba, resize_rgba(asset.rgba, w.base.height()))
        assert w._pick_frame() is not None
    finally:
        w.close()


def test_tm4_bad_size_falls_back(tmp_path):
    _assert_runtime_fallback(tmp_path, "size")


def test_tm4_corrupt_png_falls_back(tmp_path):
    _assert_runtime_fallback(tmp_path, "corrupt")


def test_tm4_missing_png_falls_back(tmp_path):
    _assert_runtime_fallback(tmp_path, "missing")


def test_tm4_fully_transparent_falls_back(tmp_path):
    _assert_runtime_fallback(tmp_path, "transparent")


def test_tm4_two_skus_independent(tmp_path):
    _, a1, item1, bbox = make_sku(tmp_path / "a", mouth=True)
    _, a2, _, _ = make_sku(tmp_path / "b", mouth=False)
    w1 = win(a1, item1)
    w2 = win(a2)
    try:
        assert w1._mouth is not None
        assert w2._mouth is None
        x0, x1, y0, y1 = bbox
        assert np.any(np.all(w1._render_rgba[y0:y1, x0:x1, :3] == (255, 0, 0),
                             axis=-1))
        assert np.array_equal(w2._render_rgba, a2.rgba)
    finally:
        w1.close()
        w2.close()


def test_tm4_zero_writes_and_200_frames(tmp_path):
    d, asset, item, _ = make_sku(tmp_path, mouth=True)
    paths = [d / "manifest.json", d / "pet.png", d / "pet_mouth_smile.png"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    arbiter_before = (ROOT / "arbiter.py").read_bytes()

    w = win(asset, item)
    try:
        for i in range(200):
            w._update_state(i * 40.0)
            assert w._pick_frame() is not None
        assert (ROOT / "arbiter.py").read_bytes() == arbiter_before
    finally:
        w.close()

    after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    assert before == after


def test_tm4_state_table_alias_signature_unchanged():
    assert STATES == ("normal", "blink", "half", "surprise",
                      "wink_l", "wink_r")
    assert "smile" not in STATES
    import inspect
    assert list(inspect.signature(ExprBank.apply).parameters) == [
        "self", "frame", "state"]
    # 别名表仍在 ExprState.from_agent，未被 T-M3 动过
    assert "'smile': 'half'" in inspect.getsource(ExprState.from_agent)
