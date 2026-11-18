"""T-M3：PetWindow 运行时嘴型 overlay 的源帧合成测试。

CI 五包没有 PyQt6，本文件在 CI 会整体 skip；本地用
`QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_pet_window_mouth.py` 跑。
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

from anim import gaze_frame, make_blink_frames, resize_rgba  # noqa: E402
from mouth_runtime_utils import make_sku, win  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_tm3_no_overlay_four_paths_match_asset(tmp_path):
    _, asset, _, _ = make_sku(tmp_path, mouth=False)
    w = win(asset)
    try:
        assert w._mouth is None
        assert w._render_rgba is asset.rgba

        # base
        assert np.array_equal(
            w.base_rgba, resize_rgba(asset.rgba, w.base.height()))

        # blink：与 PetAsset.blink_frames() 逐元素一致
        expect = make_blink_frames(
            asset.rgba,
            asset.eyes if asset.active_eyes is None
            else [asset.eyes[i] for i in asset.active_eyes])
        assert len(w.raw_blink) == len(expect)
        assert all(np.array_equal(a, b) for a, b in zip(w.raw_blink, expect))

        # gaze：与直接从 asset.rgba 生成再缩放逐元素一致
        pairs = w._gaze_geo_pairs()
        if pairs:
            exp = resize_rgba(
                gaze_frame(asset.rgba, [e for e, _ in pairs], 1, 0,
                           geos=[g for _, g in pairs]),
                w.base.height())
            assert np.array_equal(w._gaze_rgba(1, 0), exp)

        # expr：没有 bank 时跳过，有 bank 时必须与 asset.rgba 结果一致
        bank = asset.expr_bank()
        if bank is not None:
            exp = resize_rgba(bank.apply(asset.rgba, "half"), w.base.height())
            got, _ = w._expr_frame("half")
            assert np.array_equal(got, exp)

        assert w._pick_frame() is not None
    finally:
        w.close()


def test_tm3_overlay_before_scale_and_bbox(tmp_path):
    _, asset, item, bbox = make_sku(tmp_path, mouth=True)
    before = asset.rgba.copy()
    w = win(asset, item)
    try:
        assert w._mouth is not None
        assert np.array_equal(before, asset.rgba)          # 原图未被原地修改
        assert not np.array_equal(w._render_rgba, asset.rgba)

        x0, x1, y0, y1 = bbox
        assert np.any(np.all(w._render_rgba[y0:y1, x0:x1, :3] == (255, 0, 0),
                             axis=-1))
        # 缩放后仍能检出覆盖层颜色：证明合成发生在缩放前
        assert np.any(w.base_rgba[..., 0] > 200)

        # bbox 外逐比特不变，alpha 通道整体不变
        mask = np.ones(w._render_rgba.shape[:2], bool)
        mask[y0:y1, x0:x1] = False
        assert np.array_equal(w._render_rgba[mask], asset.rgba[mask])
        assert np.array_equal(w._render_rgba[..., 3], asset.rgba[..., 3])
        assert np.array_equal(w.base_rgba[..., 3],
                              resize_rgba(asset.rgba, w.base.height())[..., 3])

        # 不原地改缓存
        base_copy = w.base_rgba.copy()
        for _ in range(5):
            assert w._pick_frame() is not None
        assert np.array_equal(w.base_rgba, base_copy)
        assert np.array_equal(w._render_rgba[mask], asset.rgba[mask])
    finally:
        w.close()


def test_tm3_expr_uses_render_source(tmp_path):
    _, asset, item, _ = make_sku(tmp_path, mouth=True)
    seen = []

    class _Bank:
        def apply(self, frame, state):
            seen.append(frame)
            return frame.copy()

    asset.expr_bank = lambda: _Bank()
    w = win(asset, item)
    try:
        w._expr_frame("half")
        assert seen and seen[0] is w._render_rgba
    finally:
        w.close()
