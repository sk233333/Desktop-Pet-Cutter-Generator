"""B-4 嘴型切档的 Qt-free 部分：三档发现、缺档静默、切档时钟。

不 import PyQt6；CI 五包会真跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mouth_runtime import (MOUTH_LEVELS, cycle_level, load_mouth_set,
                           should_cycle, variant_names)  # noqa: E402


def _write(path: Path, size=(8, 8), color=(255, 0, 0, 255)):
    ov = np.zeros((size[1], size[0], 4), np.uint8)
    ov[2:4, 2:4] = color
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(ov, "RGBA").save(path)


def test_variant_names_expands_three_levels():
    names, default = variant_names("sub/pet_mouth_smile.png")
    assert default == "smile"
    assert names == {
        "closed": "sub/pet_mouth_closed.png",
        "smile": "sub/pet_mouth_smile.png",
        "o": "sub/pet_mouth_o.png",
    }


def test_variant_names_keeps_unknown_convention_static():
    assert variant_names("mouth.png") == ({}, None)
    assert variant_names("pet_mouth_angry.png") == ({}, None)
    for bad in ("", "   ", None, 123):
        assert variant_names(bad) == ({}, None)


def test_load_mouth_set_reads_existing_tiers_silently(tmp_path, capsys):
    for level in ("closed", "smile"):
        _write(tmp_path / f"pet_mouth_{level}.png")
    out, default = load_mouth_set(tmp_path, "pet_mouth_smile.png", (8, 8))
    assert default == "smile"
    assert tuple(out) == ("closed", "smile")       # 缺 o 档静默跳过
    assert "覆盖层不存在" not in capsys.readouterr().err


def test_load_mouth_set_default_missing_falls_back(tmp_path):
    _write(tmp_path / "pet_mouth_closed.png")
    out, default = load_mouth_set(tmp_path, "pet_mouth_smile.png", (8, 8))
    assert out == {} and default is None


def test_load_mouth_set_rejects_outside_path(tmp_path):
    out, default = load_mouth_set(tmp_path, "../pet_mouth_smile.png", (8, 8))
    assert out == {} and default is None


def test_load_mouth_set_path_guard_not_bypassed_by_level(tmp_path):
    """同族路径同样整体过守卫：子目录里的 .. 不能读到 SKU 外。"""
    _write(tmp_path / "pet_mouth_smile.png")
    out, default = load_mouth_set(tmp_path, "sub/../pet_mouth_smile.png",
                                  (8, 8))
    assert default is None and out == {}


def test_cycle_level_is_deterministic_and_bounded():
    levels = MOUTH_LEVELS
    assert cycle_level((), 1.0) is None
    assert cycle_level(("smile",), 9.9) == "smile"
    assert cycle_level(levels, 0.0) == "closed"
    assert cycle_level(levels, 0.16) == "smile"
    assert cycle_level(levels, 0.32) == "o"
    assert cycle_level(levels, 0.48) == "closed"
    assert cycle_level(levels, -5) == "closed"
    assert cycle_level(levels, "bad") == "closed"
    assert cycle_level(levels, 0.3, interval=0) == "smile"


def test_should_cycle_requires_speaking_and_two_levels():
    assert should_cycle(MOUTH_LEVELS, True) is True
    assert should_cycle(("smile",), True) is False
    assert should_cycle(MOUTH_LEVELS, False) is False
    assert should_cycle((), True) is False
