"""AI 候选 → ChangeSet 适配层回归（Qt-free，不联网、不写坏 assets）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import upgrade_core                                    # noqa: E402
from ai_generate_adapter import (build_changeset, commit_selected,  # noqa: E402
                                 new_sku_info, reject_existing_base,
                                 safe_stem)
from ai_generate_contract import AIGenCandidate, AIGenError  # noqa: E402
from completers.base import (ChangeSet, PathGuardError,  # noqa: E402
                             guard_paths)
from manifest_io import read_manifest, validate_manifest  # noqa: E402
from upgrade_core import scan_skus                        # noqa: E402

H, W = 40, 48
SRC_RGB = (200, 120, 80)


@pytest.fixture
def assets_root(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    return root


def _rgb(h=H, w=W) -> np.ndarray:
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = SRC_RGB
    return rgb


def _cand(h=H, w=W, fill=1.0, cid="c1") -> AIGenCandidate:
    alpha = np.zeros((h, w), np.float32)
    alpha[h // 4: h * 3 // 4, w // 4: w * 3 // 4] = fill
    return AIGenCandidate(id=cid, alpha=alpha,
                          box=[w // 4, h // 4, w // 2, h // 2], confidence=0.8)


def test_aig_commit_is_changeset_only(assets_root):
    cs = build_changeset(sku="pet", stem="pet", rgb=_rgb(),
                         selected=[_cand()], provider="fake", uploaded=False)
    assert isinstance(cs, ChangeSet)
    assert set(cs.files) == {"pet.png", "pet_ai_preview.png"}
    item = cs.manifest_patch["/items"][0]
    assert item["ai_provenance"]["source"] == "ai_generate"
    assert item["ai_provenance"]["uploaded"] is False
    assert list(assets_root.iterdir()) == [], "adapter 不得写盘"


def test_aig_same_gate_as_manual(monkeypatch, assets_root):
    calls = []
    real = upgrade_core.commit_changes

    def spy(info, changeset, dry_run=False):
        calls.append(info.name)
        return real(info, changeset, dry_run=dry_run)

    monkeypatch.setattr(upgrade_core, "commit_changes", spy)
    info = new_sku_info("pet", assets_root)
    cs = build_changeset(sku="pet", stem="pet", rgb=_rgb(),
                         selected=[_cand()], provider="fake")
    res = commit_selected(info, cs)
    assert calls == ["pet"], "AI 路径必须调用 upgrade_core.commit_changes"
    assert res.report.ok


def test_aig_path_guard_rejects_parent_absolute_manifest(assets_root):
    for bad in ("../evil", "/abs", "a/b", "..", ""):
        with pytest.raises(AIGenError):
            safe_stem(bad)
        with pytest.raises(AIGenError):
            build_changeset(sku=bad, stem="pet", rgb=_rgb(), selected=[_cand()])

    with pytest.raises(PathGuardError):
        guard_paths(assets_root, ["manifest.json"])

    info = new_sku_info("pet", assets_root)
    cs = ChangeSet(sku="pet", completer="ai_generate", candidate_id="x",
                   files={"../escape.png": b"x"})
    res = commit_selected(info, cs)
    assert not res.report.ok and "拒绝落盘" in res.report.text


def test_aig_existing_base_not_overwritten(assets_root):
    info = new_sku_info("pet", assets_root)
    info.dir.mkdir(parents=True)
    (info.dir / "pet.png").write_bytes(b"original")
    with pytest.raises(AIGenError):
        reject_existing_base(info, "pet")
    assert (info.dir / "pet.png").read_bytes() == b"original"


def test_aig_selected_candidates_validate_and_readback(assets_root):
    info = new_sku_info("pet", assets_root)
    cs = build_changeset(sku="pet", stem="pet", rgb=_rgb(),
                         selected=[_cand()], provider="fake", uploaded=True)
    res = commit_selected(info, cs)
    assert res.report.ok, res.report.text
    assert set(res.written) == {"pet.png", "pet_ai_preview.png"}

    data = read_manifest(info.manifest_path)
    ok, msg = validate_manifest(data)
    assert ok, msg
    assert data["schema_version"] == 2
    assert data["sku"] == "pet"

    prov = data["items"][0]["ai_provenance"]
    assert prov["source"] == "ai_generate"
    assert prov["uploaded"] is True
    assert prov["candidates"][0]["id"] == "c1"

    # base RGBA：RGB 不得红蓝互换，alpha 保留软边
    rgba = np.array(Image.open(info.dir / "pet.png").convert("RGBA"))
    assert tuple(rgba[H // 2, W // 2, :3]) == SRC_RGB
    assert rgba[..., 3].max() == 255
    assert int(rgba[0, 0, 3]) == 0, "候选外的 alpha 必须保持透明"


def test_aig_v1_sku_migrates_and_keeps_fields(assets_root):
    d = assets_root / "demo"
    d.mkdir()
    v1 = {
        "schema": "pet-asset/1",
        "sku": "demo",
        "items": [{
            "stem": "pet", "width": W, "height": H,
            "eyes": [{"cx": 40, "cy": 40, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
                     {"cx": 80, "cy": 42, "rx": 8, "ry": 6, "dark": [20, 20, 25]}],
            "eyes_mask": ["pet_masks.png"],
            "hair": [],
            "effects": {"enabled": ["wink"]},
        }],
    }
    (d / "manifest.json").write_text(json.dumps(v1, ensure_ascii=False),
                                     encoding="utf-8")
    info = scan_skus(assets_root)[0]
    assert info.schema_version == 1

    cs = ChangeSet(sku="demo", completer="ai_generate", candidate_id="x",
                   manifest_patch={"/items/0/ai_provenance": {
                       "source": "ai_generate", "provider": "fake",
                       "uploaded": False, "candidates": []}})
    res = upgrade_core.commit_changes(info, cs)
    assert res.report.ok and res.migrated, res.report.text

    data = read_manifest(d / "manifest.json")
    ok, msg = validate_manifest(data)
    assert ok, msg
    item = data["items"][0]
    assert item["ai_provenance"]["source"] == "ai_generate"
    assert item["hair"] == []
    assert item["effects"] == {"enabled": ["wink"]}
    assert item["eyes"][0]["cx"] == 40
    assert [p["channel"] for p in item["parts"]] == ["R", "G"]


def test_aig_failure_leaves_no_half_sku(monkeypatch, assets_root):
    info = new_sku_info("pet", assets_root)
    cs = build_changeset(sku="pet", stem="pet", rgb=_rgb(),
                         selected=[_cand()], provider="fake")

    real = upgrade_core._atomic_write
    state = {"n": 0}

    def flaky(path, data):
        state["n"] += 1
        if state["n"] >= 2:
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(upgrade_core, "_atomic_write", flaky)
    res = commit_selected(info, cs)
    assert not res.report.ok and "回滚" in res.report.text
    assert not info.dir.exists(), "失败后不得留下半份 SKU 目录"
    assert list(assets_root.iterdir()) == []
