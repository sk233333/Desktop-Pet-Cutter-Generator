"""整帧重绘中间帧：纯函数 + manifest 可选字段形状。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frame_redraw import (MAX_FRAMES, frame_budget_ok,  # noqa: E402
                          interleave)
from manifest_io import read_manifest, validate_manifest  # noqa: E402


def _rgba(color, alpha=255, h=8, w=8):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = color
    a[..., 3] = alpha
    return a


def test_v03_interleave_endpoints_and_shape():
    a, b = _rgba((255, 0, 0)), _rgba((0, 0, 255))
    frames = interleave(a, b, 3)
    assert len(frames) == 3
    assert np.array_equal(frames[0], a)
    assert np.array_equal(frames[-1], b)
    mid = frames[1]
    assert int(mid[0, 0, 0]) > 100 and int(mid[0, 0, 2]) > 100
    assert not np.array_equal(a, b)          # 入参未被原地修改


def test_v03_interleave_alpha_uses_max():
    a, b = _rgba((0, 0, 0), 0), _rgba((0, 0, 0), 255)
    mid = interleave(a, b, 3)[1]
    assert (mid[..., 3] == 255).all()


def test_v03_interleave_count_bounds():
    a = _rgba((0, 0, 0))
    assert len(interleave(a, a, 1)) == 1
    for bad in (0, MAX_FRAMES + 1, -1):
        with pytest.raises(ValueError):
            interleave(a, a, bad)


def test_v03_interleave_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        interleave(_rgba((0, 0, 0)), _rgba((0, 0, 0), h=9), 2)
    with pytest.raises(ValueError):
        interleave(np.zeros((8, 8, 3), np.uint8), np.zeros((8, 8, 4), np.uint8), 2)


def test_v03_frame_budget():
    ok = [_rgba((0, 0, 0), h=64, w=64)]
    assert frame_budget_ok(ok)
    big = [np.zeros((1024, 1024, 4), np.uint8)]
    assert not frame_budget_ok(big)


def _manifest(tmp: Path, frames) -> Path:
    d = tmp / "demo"
    d.mkdir(parents=True)
    mp = d / "manifest.json"
    mp.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "pet", "parts": [], "expr_frames": frames}]}), encoding="utf-8")
    return mp


def test_v03_expr_frames_validate_accepts_good(tmp_path):
    mp = _manifest(tmp_path, {"smile": [{"file": "pet_a.png", "hold_ms": 120},
                                       {"file": "pet_b.png"}]})
    ok, msg = validate_manifest(read_manifest(mp))
    assert ok, msg


def test_v03_expr_frames_validate_rejects_bad(tmp_path):
    bad = [
        {"smile": []},
        {"smile": [{"file": "../pet.png"}]},
        {"smile": [{"file": "pet.png", "hold_ms": 1}]},
        {"smile": [{"file": "pet.png"}] * 9},
    ]
    for frames in bad:
        mp = _manifest(tmp_path / str(abs(hash(str(frames)))), frames)
        ok, msg = validate_manifest(read_manifest(mp))
        assert not ok and "expr_frames" in msg
