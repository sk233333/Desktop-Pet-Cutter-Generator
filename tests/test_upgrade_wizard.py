"""升级向导首期回归：Qt-free 核心 + 三个补全器。

CI 只装五包（没有 PyQt6），所以这里不 import upgrade_wizard；
界面部分用离屏人工冒烟（见交付说明）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import upgrade_core                                   # noqa: E402
from anim import load_eye_slots                       # noqa: E402
from completers import mask_domain_hint, mask_ellipse, mouth_open  # noqa: E402
from completers.base import (Candidate, ChangeSet, PathGuardError,  # noqa: E402
                             guard_paths)
from expr_state import STATES, ExprState              # noqa: E402
from manifest_io import (active_eye_indices, read_manifest,  # noqa: E402
                         validate_manifest)
from upgrade_core import (allowed_proposal, commit_changes,  # noqa: E402
                          health_items, load_sku, scan_skus)


@pytest.fixture
def sku_dir(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    cv2.imwrite(str(d / "pet.png"), np.full((120, 140, 3), 240, np.uint8))
    manifest = {
        "schema": "pet-asset/1",
        "sku": "demo",
        "generated_at": "2026-11-17T00:00:00",
        "items": [{
            "stem": "pet", "width": 140, "height": 120,
            "eyes": [
                {"cx": 40, "cy": 40, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
                {"cx": 80, "cy": 42, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
                {"cx": 30, "cy": 100, "rx": 10, "ry": 9, "dark": [20, 20, 25]},
            ],
            "hair": [],
            "effects": {"enabled": ["wink", "half"]},
        }],
    }
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _info(d: Path):
    infos = scan_skus(d.parent)
    assert len(infos) == 1
    return infos[0]


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- 扫描 / 体检
def test_scan_and_sku_mismatch_fails_without_fixup(sku_dir):
    info = _info(sku_dir)
    assert info.schema_version == 1
    assert info.consistent

    data = json.loads((sku_dir / "manifest.json").read_text(encoding="utf-8"))
    data["sku"] = "other"
    (sku_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    info = load_sku(info)
    assert not info.consistent
    rows = health_items(info)
    assert any(r["id"] == "sku" and r["status"] == "blocked" for r in rows)
    # 不静默纠偏：文件里的 sku 仍是 other
    assert json.loads((sku_dir / "manifest.json").read_text())["sku"] == "other"


def test_health_lists_smile_as_v011_and_issue_entries(sku_dir):
    rows = health_items(_info(sku_dir))
    smile = [r for r in rows if r["id"] == "smile"]
    assert smile and smile[0]["status"] == "v0.1.1"
    assert not smile[0]["selectable"]
    assert any(r["id"] == "issue2" for r in rows)
    assert any(r["id"].startswith("mask:") for r in rows)


def test_real_assets_scan_is_consistent():
    infos = scan_skus()
    assert infos, "assets/*/manifest.json 至少要能扫到一个"
    assert all(i.consistent for i in infos)


# ---------------------------------------------------------------- C-U1 / C-U13
def test_propose_preview_commit_verify_never_writes(sku_dir):
    before = _snapshot(sku_dir)
    info = _info(sku_dir)
    for mod in (mask_ellipse, mask_domain_hint, mouth_open):
        for cand in mod.propose(info, info.manifest):
            mod.preview(cand, engine=None)
            if cand.kind == "mask":
                cand.payload["selected"] = [0, 1]
            cs = mod.commit(cand)
            rep = mod.verify(cs, sku_dir, None)
            assert rep.ok, rep.text
    assert _snapshot(sku_dir) == before


def test_path_guard_rejects_parent_absolute_and_manifest(sku_dir):
    for bad in ("../escape.png", "/tmp/escape.png", "manifest.json"):
        with pytest.raises(PathGuardError):
            guard_paths(sku_dir, [bad])
    cs = ChangeSet(sku="demo", completer="x", candidate_id="x",
                   files={"../escape.png": b"x"})
    res = commit_changes(_info(sku_dir), cs)
    assert not res.report.ok
    assert not (sku_dir.parent / "escape.png").exists()


# ---------------------------------------------------------------- C-U3 / C-U11
def test_from_ellipse_commit_aligns_slots_and_keeps_base(sku_dir):
    info = _info(sku_dir)
    base_before = (sku_dir / "pet.png").read_bytes()
    cand = mask_ellipse.propose(info, info.manifest)[0]
    assert cand.payload["selected"] == []          # 默认不替人点选
    cand.payload["selected"] = [0, 1]
    cs = mask_ellipse.commit(cand)
    assert set(cs.paths) == {"pet_eyes.png"}

    res = commit_changes(info, cs)
    assert res.report.ok, res.report.text
    assert res.migrated
    ok, msg = validate_manifest(res.merged)
    assert ok, msg
    assert (sku_dir / "pet.png").read_bytes() == base_before

    data = read_manifest(sku_dir / "manifest.json")
    parts = data["items"][0]["parts"]
    assert parts[0]["mask"] == parts[1]["mask"] == "pet_eyes.png"
    assert parts[2]["mask"] is None                # 未点选的眼仍不参与
    assert active_eye_indices(data["items"][0]) == [0, 1]

    slots, _hl = load_eye_slots([sku_dir / "pet_eyes.png"], (120, 140))
    assert 0 in slots and 1 in slots
    assert slots[0].any() and slots[1].any()

    # 幂等：已有 mask 的素材不再出候选，PNG mtime 不变
    mtime = (sku_dir / "pet_eyes.png").stat().st_mtime_ns
    info2 = load_sku(info)
    assert mask_ellipse.propose(info2, info2.manifest) == []
    assert (sku_dir / "pet_eyes.png").stat().st_mtime_ns == mtime


def test_from_ellipse_single_eye_keeps_channel_position(sku_dir):
    info = _info(sku_dir)
    cand = mask_ellipse.propose(info, info.manifest)[0]
    cand.payload["selected"] = [2]                 # 第三只眼 → _eyes_2.png 的 R
    cs = mask_ellipse.commit(cand)
    assert "pet_eyes_2.png" in cs.paths
    res = commit_changes(info, cs)
    assert res.report.ok, res.report.text
    slots, _hl = load_eye_slots([sku_dir / "pet_eyes_2.png"], (120, 140))
    assert 2 in slots and slots[2].any()
    data = read_manifest(sku_dir / "manifest.json")
    assert active_eye_indices(data["items"][0]) == [2]


# ---------------------------------------------------------------- C-U4 / C-U2
def test_domain_hint_three_runs_one_stderr_line_and_no_writes(sku_dir, capsys):
    info = _info(sku_dir)
    before = _snapshot(sku_dir)
    for _ in range(3):
        cands = mask_domain_hint.propose(info, info.manifest)
        assert cands and not cands[0].commit_allowed
        err = capsys.readouterr().err.splitlines()
        assert len(err) == 1
        assert "仅提示" in err[0]
        cs = mask_domain_hint.commit(cands[0])
        assert not cs.files and not cs.manifest_patch
    assert _snapshot(sku_dir) == before


def test_veto_and_empty_changeset_leave_bytes_equal(sku_dir):
    info = _info(sku_dir)
    before = _snapshot(sku_dir)
    cand = mask_ellipse.propose(info, info.manifest)[0]
    # 整体否决 = 不调用 commit；这里显式用空 ChangeSet 走同一道闸门
    cs = ChangeSet(sku="demo", completer=mask_ellipse.COMPLETER_ID,
                   candidate_id=cand.id, notes="vetoed")
    res = commit_changes(info, cs)
    assert res.report.ok
    assert _snapshot(sku_dir) == before


# ---------------------------------------------------------------- C-U5 / C-U10
def test_allowed_is_proposal_only_and_excludes_smile(sku_dir):
    info = _info(sku_dir)
    before = _snapshot(sku_dir)
    proposal = allowed_proposal(info.manifest)
    assert proposal["source"] == "proposal-only"
    assert proposal["runtime_consumer"] is None
    assert "smile" not in proposal["allowed"]
    assert "blink" not in proposal["allowed"]
    assert set(proposal["allowed"]) <= set(STATES)
    assert _snapshot(sku_dir) == before


def test_effects_and_config_files_unchanged(sku_dir):
    info = _info(sku_dir)
    cfg = [ROOT / "desktop_pet" / "action_bridge.py",
           ROOT / "desktop_pet" / "motion_cfg.py"]
    cfg_before = {p: p.read_bytes() for p in cfg}
    effects_before = json.dumps(info.manifest["items"][0]["effects"],
                                sort_keys=True)

    cand = mask_ellipse.propose(info, info.manifest)[0]
    cand.payload["selected"] = [0]
    res = commit_changes(info, mask_ellipse.commit(cand))
    assert res.report.ok, res.report.text

    assert "chat" not in (res.merged or {})
    assert json.dumps(res.merged["items"][0]["effects"],
                      sort_keys=True) == effects_before
    assert {p: p.read_bytes() for p in cfg} == cfg_before


# ---------------------------------------------------------------- C-U6 / C-U9
def test_mouth_open_adds_overlay_and_keeps_state_table(sku_dir):
    info = _info(sku_dir)
    base_before = (sku_dir / "pet.png").read_bytes()
    cand = mouth_open.propose(info, info.manifest)[0]
    cand.payload["selected"] = "smile"
    cs = mouth_open.commit(cand)
    assert list(cs.files) == ["pet_mouth_smile.png"]

    res = commit_changes(info, cs)
    assert res.report.ok, res.report.text
    ok, msg = validate_manifest(res.merged)
    assert ok, msg
    assert (sku_dir / "pet.png").read_bytes() == base_before
    assert res.merged["items"][0]["mouth_dir"] == "pet_mouth_smile.png"

    img = cv2.imread(str(sku_dir / "pet_mouth_smile.png"),
                     cv2.IMREAD_UNCHANGED)
    assert img is not None and img.shape[:2] == (120, 140)
    assert (img[..., 3] > 0).any()

    assert STATES == ('normal', 'blink', 'half', 'surprise', 'wink_l', 'wink_r')
    assert ExprState().from_agent("smile") == "half"


# ---------------------------------------------------------------- C-U8
def test_rollback_on_write_failure(sku_dir, monkeypatch):
    info = _info(sku_dir)
    manifest_before = (sku_dir / "manifest.json").read_bytes()
    cand = mask_ellipse.propose(info, info.manifest)[0]
    cand.payload["selected"] = [0, 1]
    cs = mask_ellipse.commit(cand)

    real = upgrade_core._atomic_write
    state = {"n": 0}

    def boom(path, data):
        state["n"] += 1
        if state["n"] == 2:            # 第一次 PNG 成功，第二次 manifest 失败
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(upgrade_core, "_atomic_write", boom)
    res = commit_changes(info, cs)
    assert not res.report.ok
    assert not (sku_dir / "pet_eyes.png").exists()
    assert (sku_dir / "manifest.json").read_bytes() == manifest_before


# ---------------------------------------------------------------- 契约细节
def test_change_set_paths_property_and_guard_scope(sku_dir):
    cs = ChangeSet(sku="demo", completer="x", candidate_id="c",
                   files={"pet_eyes.png": b"", "sub/x.png": b""})
    assert cs.paths == ["pet_eyes.png", "sub/x.png"]
    out = guard_paths(sku_dir, cs.paths)
    assert all(str(p).startswith(str(sku_dir.resolve())) for p in out)


def test_candidate_defaults():
    c = Candidate(id="a", kind="mask")
    assert c.payload == {} and c.note == "" and c.commit_allowed
