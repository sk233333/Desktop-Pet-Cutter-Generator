"""B-1：配置期微笑几何数据槽（expr.smile_geometry）。

Qt-free：只依赖 numpy / cv2 / pytest，干净 venv 五包可跑。
断言口径：只产数据、不改 base / mask / STATES / 别名表、词表逐项不变。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from completers import smile_geometry                        # noqa: E402
from completers.registry import entries as completer_entries  # noqa: E402

from manifest_io import read_manifest, validate_manifest      # noqa: E402
from upgrade_core import (allowed_proposal, commit_changes,   # noqa: E402
                          load_sku, scan_skus)

W, H = 140, 120


def _sku(d: Path, name: str, stem: str) -> Path:
    d.mkdir()
    base = np.full((H, W, 3), 240, np.uint8)
    base[30:90, 50:100] = (210, 190, 175)
    cv2.imwrite(str(d / f"{stem}.png"), base)
    mask = np.zeros((H, W), np.uint8)
    mask[34:46, 32:48] = 255
    mask[34:46, 72:88] = 255
    cv2.imwrite(str(d / "pet_eyes.png"), mask)
    manifest = {
        "schema_version": 2,
        "sku": name,
        "items": [{
            "stem": stem, "width": W, "height": H,
            "eyes": [
                {"cx": 40, "cy": 40, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
                {"cx": 80, "cy": 42, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
            ],
            "parts": [
                {"id": "eye_1", "eye_index": 0, "group": "g1",
                 "mask": "pet_eyes.png", "channel": "R"},
                {"id": "eye_2", "eye_index": 1, "group": "g1",
                 "mask": "pet_eyes.png", "channel": "G"},
            ],
        }],
    }
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _info(d: Path):
    return next(i for i in scan_skus(d.parent) if i.name == d.name)


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_b1_registered_as_fourth_completer():
    ids = [e.id for e in completer_entries()]
    assert "expr.smile_geometry" in ids
    assert len(ids) == 4


def test_b1_propose_preview_commit_verify_never_writes(tmp_path):
    d = _sku(tmp_path / "demo", "demo", "pet")
    before = _snapshot(d)
    info = _info(d)
    cands = smile_geometry.propose(info, info.manifest)
    assert [c.kind for c in cands] == ["smile_geometry"]
    assert cands[0].payload["preset"] == "medium"

    prev = smile_geometry.preview(cands[0], engine=None)
    assert prev.base.shape == (H, W, 3)
    assert prev.overlay.shape == (H, W, 4)

    cs = smile_geometry.commit(cands[0])
    rep = smile_geometry.verify(cs, d, None)
    assert rep.ok, rep.text
    assert _snapshot(d) == before


def test_b1_overlay_is_independent_and_slot_has_no_consumer(tmp_path):
    d = _sku(tmp_path / "demo", "demo", "pet")
    base_before = (d / "pet.png").read_bytes()
    mask_before = (d / "pet_eyes.png").read_bytes()
    info = _info(d)
    cand = smile_geometry.propose(info, info.manifest)[0]
    cs = smile_geometry.commit(cand)

    assert cs.paths == ["pet_smile.png"]
    assert cs.manifest_patch["items/0/smile_geometry"]["runtime_consumer"] is None

    res = commit_changes(info, cs)
    assert res.report.ok, res.report.text
    assert (d / "pet.png").read_bytes() == base_before
    assert (d / "pet_eyes.png").read_bytes() == mask_before

    img = cv2.imread(str(d / "pet_smile.png"), cv2.IMREAD_UNCHANGED)
    assert img.shape == (H, W, 4) and (img[..., 3] > 0).any()

    data = read_manifest(d / "manifest.json")
    ok, msg = validate_manifest(data)
    assert ok, msg
    slot = data["items"][0]["smile_geometry"]
    assert slot == cs.manifest_patch["items/0/smile_geometry"]
    assert slot["overlay"] == "pet_smile.png"
    assert slot["schema"] == smile_geometry.SCHEMA


def test_b1_data_slot_does_not_touch_vocabulary(tmp_path):
    """B-1 的不变式：数据槽不改向导提案；状态表口径由 B-2 节点单独钉。"""
    d = _sku(tmp_path / "demo", "demo", "pet")
    info = _info(d)
    before = allowed_proposal(info.manifest)
    cand = smile_geometry.propose(info, info.manifest)[0]
    smile_geometry.commit(cand)

    assert allowed_proposal(info.manifest) == before
    assert "smile" not in before["allowed"]
    assert before["runtime_consumer"] is None

    src = (ROOT / "desktop_pet" / "completers" / "smile_geometry.py").read_text(
        encoding="utf-8")
    assert "STATES =" not in src
    assert "from upgrade_core" not in src
    assert "allowed_proposal" not in src
    assert "from anim" not in src


def test_b1_preset_clamp_and_unknown_falls_back():
    medium = smile_geometry._params("medium")
    assert smile_geometry._params("unknown") == medium
    assert medium == smile_geometry._params(None)

    hi = smile_geometry._sanitize({"lid_drop": 9, "lid_sag": -1,
                                   "arc_ratio": 5, "blush_alpha": 999})
    assert hi["lid_drop"] == 0.45 and hi["lid_sag"] == 0.0
    assert hi["arc_ratio"] == 1.0 and hi["blush_alpha"] == 160

    lo = smile_geometry._sanitize({"lid_drop": -3, "lid_sag": -3,
                                   "arc_ratio": -3, "blush_alpha": -3})
    assert lo == {"lid_drop": 0.0, "lid_sag": 0.0,
                  "arc_ratio": 0.0, "blush_alpha": 0.0}


def test_b1_geometry_stays_inside_canvas():
    eyes = [{"cx": 5, "cy": 5, "rx": 40, "ry": 40, "dark": []}]
    g = smile_geometry._geometry(eyes, 30, 30,
                                 smile_geometry._params("strong"))
    for e in g["eyes"]:
        assert 0 <= e["cx"] < 30 and 0 <= e["cy"] < 30
        assert 0 <= e["lid_y"] < 30
    m = g["mouth_arc"]
    assert 0 <= m["cx"] < 30 and 0 <= m["cy"] < 30
    assert m["half"] > 0 and m["height"] > 0
    for b in g["blush"]:
        assert 0 <= b["cx"] < 30 and 0 <= b["cy"] < 30
        assert b["rx"] > 0 and b["ry"] > 0


def test_b2_smile_overlay_omits_arc_when_mouth_dir(tmp_path):
    """B-2：已有 mouth_dir 时生成侧不画嘴角弧，避免运行时双嘴。"""
    d = _sku(tmp_path / "demo", "demo", "pet")
    mp = d / "manifest.json"
    data = json.loads(mp.read_text(encoding="utf-8"))
    data["items"][0]["mouth_dir"] = "pet_mouth_smile.png"
    mp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    info = _info(d)
    cand = smile_geometry.propose(info, info.manifest)[0]
    assert cand.payload["has_mouth"] is True
    cs = smile_geometry.commit(cand)
    slot = cs.manifest_patch["items/0/smile_geometry"]
    assert slot["mouth_arc"] is None

    res = commit_changes(info, cs)
    assert res.report.ok, res.report.text
    out = read_manifest(mp)
    ok, msg = validate_manifest(out)
    assert ok, msg
    assert out["items"][0]["smile_geometry"]["mouth_arc"] is None
    assert out["items"][0]["mouth_dir"] == "pet_mouth_smile.png"

    img = cv2.imread(str(d / "pet_smile.png"), cv2.IMREAD_UNCHANGED)
    assert img.shape == (H, W, 4) and (img[..., 3] > 0).any()


def test_b1_two_skus_do_not_cross(tmp_path):
    da = _sku(tmp_path / "a", "a", "a_pet")
    db = _sku(tmp_path / "b", "b", "b_pet")
    for d in (da, db):
        info = _info(d)
        cand = smile_geometry.propose(info, info.manifest)[0]
        res = commit_changes(info, smile_geometry.commit(cand))
        assert res.report.ok, res.report.text

    sa = read_manifest(da / "manifest.json")["items"][0]["smile_geometry"]
    sb = read_manifest(db / "manifest.json")["items"][0]["smile_geometry"]
    assert sa["overlay"] == "a_pet_smile.png"
    assert sb["overlay"] == "b_pet_smile.png"
    assert (da / sa["overlay"]).exists() and (db / sb["overlay"]).exists()
    assert not (da / sb["overlay"]).exists()
    assert not (db / sa["overlay"]).exists()
