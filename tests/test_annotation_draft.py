"""W3 选 1：标注草案只落 annotation_draft，不展开到 parts。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_annotate import AnnotationReport  # noqa: E402
from annotation_draft import build_draft, save_draft  # noqa: E402
from manifest_io import read_manifest, validate_manifest  # noqa: E402
from upgrade_core import SkuInfo  # noqa: E402

REGION = {"cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.05}


def _sku(tmp: Path, item_extra: dict | None = None) -> SkuInfo:
    d = tmp / "demo"
    d.mkdir()
    item = {"stem": "pet", "files": ["pet.png"], "parts": []}
    item.update(item_extra or {})
    mp = d / "manifest.json"
    mp.write_text(json.dumps({"schema_version": 2, "items": [item]}),
                  encoding="utf-8")
    return SkuInfo(name="demo", dir=d, manifest_path=mp,
                   manifest=read_manifest(mp), schema_version=2,
                   sku_field="demo")


def _report() -> AnnotationReport:
    return AnnotationReport(
        eyes=[dict(REGION)], mouth=dict(REGION), hair=[dict(REGION)],
        sway={"top": dict(REGION)}, warnings=["检测到 3 个眼部候选，请人工确认"])


def test_v03_draft_roundtrip_v1_v2(tmp_path):
    info = _sku(tmp_path)
    res = save_draft(info, 0, _report())
    assert res.report.ok, res.report.text
    got = read_manifest(info.manifest_path)["items"][0]
    assert got["annotation_draft"]["schema"] == "ai-annotate/1"
    assert got["annotation_draft"]["eyes"] == [REGION]
    assert got["annotation_draft"]["warnings"]
    assert validate_manifest(read_manifest(info.manifest_path))[0]


def test_v03_draft_never_auto_applies(tmp_path):
    info = _sku(tmp_path)
    before = json.dumps(info.manifest["items"][0]["parts"], sort_keys=True)
    res = save_draft(info, 0, _report())
    assert res.report.ok, res.report.text
    got = read_manifest(info.manifest_path)["items"][0]
    assert json.dumps(got["parts"], sort_keys=True) == before
    assert "hair" not in got and "cloth" not in got and "sway" not in got


def test_v03_validate_rejects_bad_draft(tmp_path):
    info = _sku(tmp_path, {"annotation_draft": {
        "schema": "ai-annotate/1", "eyes": [dict(REGION, cx=2.0)]}})
    ok, msg = validate_manifest(read_manifest(info.manifest_path))
    assert not ok and "annotation_draft" in msg


def test_v03_validate_accepts_missing_draft(tmp_path):
    info = _sku(tmp_path)
    ok, msg = validate_manifest(read_manifest(info.manifest_path))
    assert ok, msg


def test_v03_draft_dry_run_does_not_write(tmp_path):
    info = _sku(tmp_path)
    before = info.manifest_path.read_bytes()
    res = save_draft(info, 0, _report(), dry_run=True)
    assert res.report.ok, res.report.text
    assert info.manifest_path.read_bytes() == before


def test_v03_draft_keeps_unknown_item_fields(tmp_path):
    info = _sku(tmp_path, {"custom_note": "keep-me"})
    res = save_draft(info, 0, _report())
    assert res.report.ok, res.report.text
    got = read_manifest(info.manifest_path)["items"][0]
    assert got["custom_note"] == "keep-me"
    assert got["annotation_draft"]["schema"] == "ai-annotate/1"


def test_v03_draft_index_out_of_range(tmp_path):
    info = _sku(tmp_path)
    with pytest.raises(ValueError):
        save_draft(info, 1, _report())


def test_v03_build_draft_is_closed_shape():
    d = build_draft(_report())
    assert set(d) == {"schema", "eyes", "mouth", "hair", "sway", "warnings"}
    assert d["schema"] == "ai-annotate/1"
