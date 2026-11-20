"""W5 切割质检：只读打分与批量报告。Qt-free，CI 五包可跑。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from cut_quality import batch_report, score_cut, score_file  # noqa: E402


def _img(rgb_value: int, alpha_box=(20, 20, 80, 80), size=100):
    rgb = np.full((size, size, 3), rgb_value, np.uint8)
    a = np.zeros((size, size), np.uint8)
    x0, y0, x1, y1 = alpha_box
    a[y0:y1, x0:x1] = 255
    return rgb, a


def test_clean_cut_scores_good():
    rgb, a = _img(120)
    got = score_cut(rgb, a)
    assert got["level"] == "good"
    assert got["white_halo"] == 0.0
    assert got["shadow"] == 0.0
    assert not got["notes"]


def test_white_halo_is_detected():
    _, a = _img(120)
    rgb = np.full((100, 100, 3), 120, np.uint8)
    rgb[a > 0] = 255
    got = score_cut(rgb, a)
    assert got["white_halo"] > 0.9
    assert any("白边" in n for n in got["notes"])
    assert got["level"] != "good"


def test_shadow_outside_mask_is_detected():
    rgb, a = _img(120)
    grow = cv2.dilate(a, np.ones((5, 5), np.uint8)) > 0
    ring = grow & (a == 0)
    rgb[ring] = 8
    got = score_cut(rgb, a)
    assert got["shadow"] > 0.5
    assert any("阴影" in n for n in got["notes"])


def test_missing_alpha_is_reported_not_guessed(tmp_path):
    p = tmp_path / "plain.png"
    cv2.imwrite(str(p), np.full((20, 20, 3), 200, np.uint8))
    assert score_file(p)["error"] == "missing_alpha"


def test_batch_report_is_read_only(tmp_path):
    rgb, a = _img(120)
    clean = np.dstack([rgb[..., ::-1], a])          # BGR + alpha
    cv2.imwrite(str(tmp_path / "a.png"), clean)
    cv2.imwrite(str(tmp_path / "b.png"),
                np.full((10, 10, 3), 9, np.uint8))
    sub = tmp_path / "nested"
    sub.mkdir()
    cv2.imwrite(str(sub / "c.png"), clean)
    before = {p.name: p.read_bytes() for p in tmp_path.rglob("*")
              if p.is_file()}

    flat = batch_report(tmp_path)
    assert flat["summary"]["count"] == 2
    assert flat["summary"]["errors"] == 1
    deep = batch_report(tmp_path, recursive=True)
    assert deep["summary"]["count"] == 3
    assert deep["summary"]["scored"] >= 2

    after = {p.name: p.read_bytes() for p in tmp_path.rglob("*")
             if p.is_file()}
    assert before == after
    json.dumps(deep)                                 # 可序列化
