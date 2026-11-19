"""T-M4 运行时集成断言的 CI 版（Qt-free）。

为什么存在：T-M3/T-M4 的像素与文件断言原先只落在需要 PyQt6 的 GUI 文件里，
CI 五包会整体 skip，等于「接线之后四路取帧仍零回归」这层集成网在 CI 从未真跑。
这里用与 `PetWindow` 同一套纯函数（`anim` / `expr_state` / `mouth_overlay` /
`manifest_io`）复刻四路派生，把不依赖 QApplication 的断言下沉到 CI。

C-M1 比较层级：所有「逐比特」比较都在**缩放前的素材分辨率源帧**上；显示尺寸
只用于确认识别得到覆盖层颜色。`resize_rgba` 会重采样整帧，不要拿显示尺寸去做
逐比特断言。

`PetWindow` 的真实接线仍由 `tests/test_pet_window_mouth.py` 在本地 offscreen 覆盖；
本文件额外用源码级断言钉住「apply 只在 __init__ 一次、四路都从 _render_rgba 派生」。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from anim import (Eye, PetAsset, gaze_frame, make_blink_frames,  # noqa: E402
                  resize_rgba)
from expr_state import STATES, ExprBank, ExprState  # noqa: E402
from manifest_io import read_manifest  # noqa: E402
from mouth_overlay import (MouthOverlay, match_items,  # noqa: E402
                           mouth_name)

H, W = 80, 64           # 素材分辨率
HEIGHT = 40             # 显示高度（小于素材 → 触发缩放）
EYES = [(22, 24), (42, 24)]


def _base_rgba() -> np.ndarray:
    """合成一张可检测的素材：浅色身体 + 两只深色眼睛。"""
    arr = np.zeros((H, W, 4), np.uint8)
    arr[8:72, 8:56] = (232, 222, 212, 255)
    yy, xx = np.ogrid[:H, :W]
    for cx, cy in EYES:
        m = ((xx - cx) ** 2 / 5.0 ** 2 + (yy - cy) ** 2 / 6.0 ** 2) <= 1
        arr[m] = (18, 18, 18, 255)
    return arr


def _sockets() -> dict[int, np.ndarray]:
    yy, xx = np.ogrid[:H, :W]
    return {i: ((xx - cx) ** 2 / 7.0 ** 2 + (yy - cy) ** 2 / 8.0 ** 2) <= 1
            for i, (cx, cy) in enumerate(EYES)}


def _make_sku(tmp: Path, *, mouth: bool, bad: str | None = None):
    """临时 SKU：pet.png + manifest.json + 可选覆盖层。

    覆盖层落在嘴部（y=56..62），与眼睛（y≈18..30）不重叠。
    """
    tmp.mkdir(parents=True)
    base = _base_rgba()
    Image.fromarray(base, "RGBA").save(tmp / "pet.png")
    item = {"stem": "pet", "files": ["pet.png"]}
    bbox = None
    if mouth:
        name = "pet_mouth_smile.png"
        if bad == "size":
            ov = np.zeros((10, 10, 4), np.uint8)
            ov[..., 3] = 255
            Image.fromarray(ov, "RGBA").save(tmp / name)
        elif bad == "corrupt":
            (tmp / name).write_bytes(b"not a png")
        elif bad == "missing":
            pass
        elif bad == "transparent":
            Image.fromarray(np.zeros((H, W, 4), np.uint8), "RGBA").save(
                tmp / name)
        else:
            ov = np.zeros((H, W, 4), np.uint8)
            x0, x1 = 28, 36
            y0, y1 = 56, 62
            ov[y0:y1, x0:x1] = (255, 0, 0, 255)
            Image.fromarray(ov, "RGBA").save(tmp / name)
            bbox = (x0, x1, y0, y1)
        item["mouth_dir"] = name
    (tmp / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "items": [item]}), encoding="utf-8")

    asset = PetAsset(rgba=np.array(Image.open(tmp / "pet.png").convert("RGBA")),
                     eyes=[Eye(cx, cy, 5, 6) for cx, cy in EYES],
                     eyes_trusted=True, source=tmp / "pet.png")
    asset.eye_sockets = _sockets()
    return tmp, asset, item, bbox


def _load_overlay_like_petwindow(sku: Path, asset: PetAsset):
    """复刻 `PetWindow._load_mouth_overlay` 的只读路径（C-M3 用）。"""
    man = sku / "manifest.json"
    if not man.is_file():
        return None
    data = read_manifest(man)
    hits, _ = match_items(data.get("items") or [], asset.source)
    if len(hits) != 1:
        return None
    name = mouth_name(hits[0], asset.source.stem)
    if not name:
        return None
    return MouthOverlay.load(sku, name, asset.rgba.shape[:2])


def _render_for(sku: Path, asset: PetAsset) -> np.ndarray:
    ov = _load_overlay_like_petwindow(sku, asset)
    return asset.rgba if ov is None else ov.apply(asset.rgba)


def _four_paths(render: np.ndarray, asset: PetAsset, height: int = HEIGHT):
    """与 `PetWindow` 同口径的四路派生（base / blink / expr / gaze）。"""
    geos = asset.gaze_geos()
    pairs = [(e, g) for e, g in zip(asset.eyes, geos) if g is not None]
    return {
        "base": resize_rgba(render, height),
        "blink": make_blink_frames(render, asset.eyes),
        "gaze": resize_rgba(
            gaze_frame(render, [e for e, _ in pairs], 2, 1,
                       geos=[g for _, g in pairs]), height),
        "expr": resize_rgba(asset.expr_bank().apply(render, "half"), height),
    }


def _assert_paths_equal(a: dict, b: dict):
    assert set(a) == set(b)
    for key in a:
        av, bv = a[key], b[key]
        if isinstance(av, list):
            assert len(av) == len(bv)
            assert all(np.array_equal(x, y) for x, y in zip(av, bv)), key
        else:
            assert np.array_equal(av, bv), key


# ---------------------------------------------------------------- C-M1

def test_cm1_no_overlay_four_paths_bit_identical(tmp_path):
    """无 mouth_dir：四路派生与「只用 asset.rgba」的 golden 逐字节一致。"""
    sku, asset, _, _ = _make_sku(tmp_path / "sku", mouth=False)
    render = _render_for(sku, asset)
    assert render is asset.rgba                      # 无 overlay 时零拷贝
    _assert_paths_equal(_four_paths(render, asset),
                        _four_paths(asset.rgba, asset))


def test_cm1_render_source_wiring():
    """源码级：apply 只在 __init__ 一次；四路都从 _render_rgba 派生。"""
    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert src.count("self._mouth.apply(") == 1
    apply_at = src.index("self._mouth.apply(")
    init = src.rindex("    def __init__(", 0, apply_at)
    init_end = src.index("\n    def ", apply_at)
    assert "self._mouth.apply(" in src[init:init_end]

    assert "to_pixmap(self._render_rgba" in src              # base
    assert "resize_rgba(self._render_rgba" in src            # base_rgba
    assert "self._blink_frames_from(self._render_rgba)" in src  # blink
    assert "expr_bank().apply(self._render_rgba" in src      # expr
    assert "gaze_frame(self._render_rgba" in src             # gaze

    # 原图不被原地修改（T-M1 的 frame.copy() 在接线后仍成立）
    assert "asset.rgba =" not in src
    assert "asset.rgba[" not in src


# ---------------------------------------------------------------- C-M3a~d

@pytest.mark.parametrize("bad", ["size", "corrupt", "missing", "transparent"])
def test_cm3_bad_overlay_falls_back(tmp_path, bad):
    """四类失败各自独立：加载返回 None，四路等于无 overlay golden。"""
    sku, asset, _, _ = _make_sku(tmp_path / bad, mouth=True, bad=bad)
    assert _load_overlay_like_petwindow(sku, asset) is None
    _assert_paths_equal(_four_paths(_render_for(sku, asset), asset),
                        _four_paths(asset.rgba, asset))


# ---------------------------------------------------------------- C-M5

def test_cm5_apply_200_frames_zero_writes(tmp_path):
    """200 次 apply：manifest / base / overlay 字节与 mtime 不变。"""
    sku, asset, _, _ = _make_sku(tmp_path / "sku", mouth=True)
    paths = [sku / "manifest.json", sku / "pet.png",
             sku / "pet_mouth_smile.png"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    arbiter_before = (ROOT / "desktop_pet" / "arbiter.py").read_bytes()

    ov = _load_overlay_like_petwindow(sku, asset)
    assert ov is not None
    for _ in range(200):
        out = ov.apply(asset.rgba)
        assert out.shape == asset.rgba.shape

    assert (ROOT / "desktop_pet" / "arbiter.py").read_bytes() == arbiter_before
    after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    assert before == after


# ---------------------------------------------------------------- C-M6

def test_cm6_protected_modules_untouched():
    """anim / expr_state / arbiter 不感知嘴型；表情贴片不读覆盖层。"""
    for name in ("anim.py", "expr_state.py", "arbiter.py", "manifest_io.py"):
        src = (ROOT / "desktop_pet" / name).read_text(encoding="utf-8")
        assert "mouth_overlay" not in src, name
        assert "MouthOverlay" not in src, name
    anim = (ROOT / "desktop_pet" / "anim.py").read_text(encoding="utf-8")
    start = anim.index("def build_expr_bank(")
    end = anim.index("\ndef ", start + 10)
    assert "mouth" not in anim[start:end].lower()


# ---------------------------------------------------------------- C-M7

def test_cm7_state_table_golden_and_apply_signature():
    # joy / sleepy 不注册 STATES，只作覆盖层；apply 签名不变。
    assert STATES == ("normal", "blink", "half", "surprise",
                      "wink_l", "wink_r", "smile")
    assert list(inspect.signature(ExprBank.apply).parameters) == [
        "self", "frame", "state"]
    assert "'smile': 'half'" not in inspect.getsource(ExprState.from_agent)


# ---------------------------------------------------------------- C-M8

def test_cm8_two_skus_independent(tmp_path):
    sku_a, a1, _, bbox = _make_sku(tmp_path / "a", mouth=True)
    sku_b, a2, _, _ = _make_sku(tmp_path / "b", mouth=False)

    render_a = _render_for(sku_a, a1)
    render_b = _render_for(sku_b, a2)
    assert render_b is a2.rgba
    x0, x1, y0, y1 = bbox
    assert np.any(np.all(render_a[y0:y1, x0:x1, :3] == (255, 0, 0), axis=-1))
    assert np.array_equal(render_b, a2.rgba)


# ---------------------------------------------------------------- 补断言

def test_mouth_bbox_survives_expr_and_blink(tmp_path):
    """表情 / 眨眼帧的嘴部 bbox 与 base 一致（防二次叠加或被擦除）。"""
    sku, asset, _, bbox = _make_sku(tmp_path / "sku", mouth=True)
    render = _render_for(sku, asset)
    x0, x1, y0, y1 = bbox
    base_patch = render[y0:y1, x0:x1].copy()

    blink = make_blink_frames(render, asset.eyes)[-1]
    assert np.array_equal(blink[y0:y1, x0:x1], base_patch)

    expr = asset.expr_bank().apply(render, "half")
    assert np.array_equal(expr[y0:y1, x0:x1], base_patch)
