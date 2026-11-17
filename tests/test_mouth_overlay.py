"""嘴型 overlay 纯逻辑回归（T-M1）：加载 / 路径守卫 / bbox 合成。

CI 只装五包（没有 PyQt6），本文件不 import pet_window / ui_app。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import mouth_overlay  # noqa: E402
from mouth_overlay import MouthOverlay  # noqa: E402

SIZE = (20, 20)  # (h, w)


def _save(path: Path, rgba: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)


def _base(rgb=(40, 50, 60), alpha=255, size=SIZE) -> np.ndarray:
    h, w = size
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = alpha
    return arr


def _overlay() -> np.ndarray:
    arr = np.zeros((20, 20, 4), np.uint8)
    arr[6:10, 5:9] = (255, 0, 0, 255)     # 不透明红
    arr[12:16, 5:9] = (0, 200, 0, 128)    # 半透明绿
    return arr


def test_load_valid_and_bbox(tmp_path):
    _save(tmp_path / "valid.png", _overlay())
    ov = MouthOverlay.load(tmp_path, "valid.png", SIZE)
    assert ov is not None
    assert ov.size == SIZE
    assert ov.bbox == (6, 16, 5, 9)


def test_load_missing_returns_none(tmp_path):
    assert MouthOverlay.load(tmp_path, "missing-a.png", SIZE) is None


def test_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "corrupt-a.png"
    p.write_bytes(b"not a png")
    assert MouthOverlay.load(tmp_path, "corrupt-a.png", SIZE) is None


def test_load_size_mismatch_returns_none(tmp_path):
    _save(tmp_path / "small-a.png", np.zeros((10, 10, 4), np.uint8))
    assert MouthOverlay.load(tmp_path, "small-a.png", SIZE) is None


def test_load_all_transparent_returns_none(tmp_path):
    _save(tmp_path / "clear-a.png", np.zeros((20, 20, 4), np.uint8))
    assert MouthOverlay.load(tmp_path, "clear-a.png", SIZE) is None


def test_load_rejects_parent_and_absolute(tmp_path):
    sku = tmp_path / "sku"
    sku.mkdir()
    _save(tmp_path / "outside-a.png", _overlay())
    assert MouthOverlay.load(sku, "../outside-a.png", SIZE) is None
    assert MouthOverlay.load(sku, str(tmp_path / "outside-a.png"), SIZE) is None


def test_load_ignores_non_string_name(tmp_path):
    assert MouthOverlay.load(tmp_path, None, SIZE) is None
    assert MouthOverlay.load(tmp_path, 123, SIZE) is None


def test_apply_outside_bbox_bit_identical(tmp_path):
    _save(tmp_path / "valid-b.png", _overlay())
    ov = MouthOverlay.load(tmp_path, "valid-b.png", SIZE)
    base = _base()
    out = ov.apply(base)
    y0, y1, x0, x1 = ov.bbox
    mask = np.ones(base.shape[:2], bool)
    mask[y0:y1, x0:x1] = False
    assert np.array_equal(out[mask], base[mask])
    assert np.array_equal(out[..., 3], base[..., 3])


def test_apply_inside_matches_alpha_over(tmp_path):
    overlay = _overlay()
    _save(tmp_path / "valid-c.png", overlay)
    ov = MouthOverlay.load(tmp_path, "valid-c.png", SIZE)
    base = _base()
    out = ov.apply(base)
    y0, y1, x0, x1 = ov.bbox
    a = overlay[y0:y1, x0:x1, 3:4].astype(np.float32) / 255.0
    exp = base[y0:y1, x0:x1, :3].astype(np.float32) * (1.0 - a) \
        + overlay[y0:y1, x0:x1, :3].astype(np.float32) * a
    assert np.allclose(out[y0:y1, x0:x1, :3].astype(np.float32), exp, atol=1.0)
    assert np.array_equal(out[..., 3], base[..., 3])
    assert tuple(out[7, 6, :3]) == (255, 0, 0)


def test_apply_does_not_mutate_input(tmp_path):
    _save(tmp_path / "valid-d.png", _overlay())
    ov = MouthOverlay.load(tmp_path, "valid-d.png", SIZE)
    base = _base()
    before = base.copy()
    ov.apply(base)
    assert np.array_equal(base, before)


def test_apply_rgb_frame_is_noop(tmp_path):
    _save(tmp_path / "valid-e.png", _overlay())
    ov = MouthOverlay.load(tmp_path, "valid-e.png", SIZE)
    base = _base()[..., :3].copy()
    out = ov.apply(base)
    assert out is base
    assert tuple(out[7, 6]) == tuple(base[7, 6])


def test_apply_wrong_size_is_noop(tmp_path):
    _save(tmp_path / "valid-f.png", _overlay())
    ov = MouthOverlay.load(tmp_path, "valid-f.png", SIZE)
    small = _base(size=(10, 10))
    assert ov.apply(small) is small


def test_warn_once(tmp_path, capsys):
    name = "warn-once-a.png"
    assert MouthOverlay.load(tmp_path, name, SIZE) is None
    assert MouthOverlay.load(tmp_path, name, SIZE) is None
    assert capsys.readouterr().err.count(name) == 1


def test_warn_two_skus_same_name(tmp_path, capsys):
    a = tmp_path / "sku-a"
    b = tmp_path / "sku-b"
    a.mkdir()
    b.mkdir()
    name = "shared-missing-a.png"
    assert MouthOverlay.load(a, name, SIZE) is None
    assert MouthOverlay.load(b, name, SIZE) is None
    assert capsys.readouterr().err.count(name) == 2


def test_warn_module_exists():
    assert hasattr(mouth_overlay, "MouthOverlay")
