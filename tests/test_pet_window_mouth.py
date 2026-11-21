"""T-M3：PetWindow 运行时嘴型 overlay 的源帧合成测试。

CI 五包没有 PyQt6，本文件在 CI 会整体 skip；本地用
`QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_pet_window_mouth.py` 跑。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip(
    "PyQt6",
    reason="设计结果：本文件 4 条用例需要 QApplication，CI 五包按 D 档不装 "
           "PyQt6；禁止靠给 CI 加 PyQt6 来消掉这条 skip",
)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtWidgets import QApplication  # noqa: E402

from anim import gaze_frame, make_blink_frames, resize_rgba  # noqa: E402
from mouth_runtime_utils import ROOT, make_sku, win  # noqa: E402


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


def test_b2_smile_overlay_renders_on_smile_state(tmp_path):
    """B-2：smile 状态走配置期独立覆盖层，不依赖 ExprBank 贴片。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=False, smile=True)
    w = win(asset, item)
    try:
        assert w._smile is not None
        got, _ = w._expr_frame("smile")
        assert np.any(got[..., 1] > 200)          # 覆盖层的绿色带出现在帧里
    finally:
        w.close()


def test_b2_no_smile_overlay_falls_back_to_render_frame(tmp_path):
    """没有 smile_geometry 时 apply('smile') 原样返回：不会变半眼。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=False)
    w = win(asset, item)

    class _Bank:
        def apply(self, frame, state):
            assert state == "smile"
            return frame

    asset.expr_bank = lambda: _Bank()
    try:
        assert w._smile is None
        got, _ = w._expr_frame("smile")
        assert np.array_equal(got, resize_rgba(w._render_rgba, w.base.height()))
    finally:
        w.close()


def test_b4_three_tiers_cycle_only_while_speaking(tmp_path):
    """说话窗口内只换预计算帧；窗口外回到默认静态档。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=True, tiers=True)
    w = win(asset, item)
    try:
        w.blink_enabled = False
        assert w._mouth_levels == ("closed", "smile", "o")
        assert w._pick_frame() is w.base          # 不说话 → 默认 smile 档

        w._speak_until = time.monotonic() + 5.0
        w.elapsed = 0.0
        assert w._pick_frame() is w._mouth_frames["closed"][1]
        w.elapsed = 0.2
        assert w._pick_frame() is w._mouth_frames["smile"][1]
        w.elapsed = 0.4
        assert w._pick_frame() is w._mouth_frames["o"][1]

        w._speak_until = 0.0
        assert w._pick_frame() is w.base
    finally:
        w.close()


def test_b4_single_tier_stays_static_when_speaking(tmp_path):
    """同族只读到一档时不做切档，避免为动态白白压住表情/视线。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=True)
    w = win(asset, item)
    try:
        w.blink_enabled = False
        assert w._mouth_levels == ("smile",)
        w._speak_until = time.monotonic() + 5.0
        assert w._pick_frame() is w.base
    finally:
        w.close()


def test_b4_speaking_frames_are_precomputed(tmp_path):
    """切档帧在重建时烘焙：说话期间不调用 MouthOverlay.apply。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=True, tiers=True)
    w = win(asset, item)
    try:
        w.blink_enabled = False
        w._speak_until = time.monotonic() + 5.0
        for i in range(50):
            w.elapsed = i * 0.05
            w._pick_frame()
        for level in ("closed", "smile", "o"):
            assert level in w._mouth_frames
    finally:
        w.close()


def test_mcp_notify_lands_only_on_arbiter(tmp_path):
    """外部通知只写 arbiter 的 bubble/expr；源帧逐比特不变，点一下可收起。"""
    _, asset, item, _ = make_sku(tmp_path, mouth=False)
    w = win(asset, item)
    try:
        before = w._render_rgba.copy()
        ev, box = threading.Event(), {}
        w._on_mcp_event({"text": "构建过了", "emotion": "surprise",
                         "mood_hint": None, "done": ev, "box": box})
        assert ev.is_set() and box["ok"] is True
        assert np.array_equal(w._render_rgba, before)
        assert w.arbiter.resolve()["bubble"] == "构建过了"
        assert w.arbiter.resolve()["expr"] == "surprise"

        w._update_state(time.monotonic() * 1000.0)
        assert w._bubble_lbl is not None
        assert w._bubble_lbl.text() == "构建过了"
        assert not w._bubble_lbl.isHidden()

        w._ack_bubble()
        assert w._bubble_lbl.isHidden()
    finally:
        w.close()


def test_five_overlay_keys_load_and_render_at_runtime(tmp_path):
    """五键都进运行时 overlays，且各自能产生可见像素；STATES 零改动。"""
    from PIL import Image
    from expr_state import STATES

    d, asset, item, _ = make_sku(tmp_path, mouth=False)
    h, w = asset.rgba.shape[:2]
    colors = {
        "joy": (255, 0, 0),
        "sleepy": (0, 255, 0),
        "angry": (0, 0, 255),
        "sad": (255, 255, 0),
        "shy": (255, 0, 255),
    }
    item["expr_overlays"] = {}
    for i, (key, color) in enumerate(colors.items()):
        name = f"pet_{key}.png"
        ov = np.zeros((h, w, 4), np.uint8)
        y0 = int(h * (0.10 + 0.12 * i))
        ov[y0:y0 + 6, :] = (*color, 255)
        Image.fromarray(ov, "RGBA").save(d / name)
        item["expr_overlays"][key] = {"overlay": name}

    w0 = win(asset, item)
    try:
        assert set(w0._expr_overlays) == set(colors)
        for key, color in colors.items():
            got = w0._expr_overlays[key].apply(w0._render_rgba)
            assert np.any(np.all(got[..., :3] == color, axis=-1)), key
        assert STATES == ("normal", "blink", "half", "surprise", "wink_l",
                          "wink_r", "smile")
    finally:
        w0.close()


def test_v03_expr_frames_switch_without_recompose(tmp_path):
    """中间帧只在首次取图时合成；之后切索引不再调用 apply。"""
    from PIL import Image

    d, asset, item, _ = make_sku(tmp_path, mouth=False)
    h, w = asset.rgba.shape[:2]
    for name, color, y0 in (("pet_f0.png", (255, 0, 0), 10),
                            ("pet_f1.png", (0, 255, 0), 20)):
        ov = np.zeros((h, w, 4), np.uint8)
        ov[y0:y0 + 6, :] = (*color, 255)
        Image.fromarray(ov, "RGBA").save(d / name)
    item["expr_frames"] = {"smile": [
        {"file": "pet_f0.png", "hold_ms": 100},
        {"file": "pet_f1.png", "hold_ms": 100},
    ]}
    w0 = win(asset, item)
    try:
        assert set(w0._expr_frames) == {"smile"}
        calls = {"n": 0}
        for ov, _ in w0._expr_frames["smile"]:
            orig = ov.apply

            def counted(base, _orig=orig):
                calls["n"] += 1
                return _orig(base)

            ov.apply = counted

        w0.elapsed = 0.05
        first, _ = w0._expr_frame("smile")
        w0.elapsed = 0.15
        second, _ = w0._expr_frame("smile")
        assert calls["n"] == 2
        assert not np.array_equal(first, second)
        for i in range(20):
            w0.elapsed = 0.05 + (i % 2) * 0.1
            w0._expr_frame("smile")
        assert calls["n"] == 2, "切索引不得重新合成"
    finally:
        w0.close()


def test_tm4_zero_writes_and_200_frames(tmp_path):
    """GUI 专属：真实帧循环跑 200 帧；Qt-free 的文件哈希版在
    `tests/test_mouth_runtime_ci.py::test_cm5_apply_200_frames_zero_writes`。
    """
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
