"""C6–C8：manifest v2 迁移与兼容读取。

对应《收敛补丁清单》第 9 节 + 《裁定回执》的两条硬规则：
  ① `file_idx = i // 2`，越界写 mask=null + warn，禁止复用最后一个文件
  ② 装饰眼迁移后仍是 g2，脚本不擅自改成 group: null
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from manifest_io import (CHANNEL_ENUM, active_eye_indices,  # noqa: E402
                         active_eye_parts, legacy_consistent, read_manifest,
                         validate_manifest, v1_to_v2)
from migrate_manifest_v1_v2 import migrate  # noqa: E402

SCHEMA = json.loads((ROOT / "desktop_pet" / "manifest.schema.json").read_text("utf-8"))


def _v1_item(n_eyes=2, masks=("a_pet_eyes.png",)):
    return {
        "stem": "a_pet",
        "width": 300, "height": 400,
        "display_height_px": 256,
        "anchor": {"x": 0.5, "y": 0.99},
        "eyes": [{"cx": 100 + i * 40, "cy": 120, "rx": 9, "ry": 6}
                 for i in range(n_eyes)],
        "eye_level": ["L2"] * n_eyes,
        "eyes_mask": list(masks),
        "blink": {"enabled": True, "cycle": 8.0,
                  "items": [{"group": 1, "t": 2.0, "hold": 0.2, "count": 2}]},
    }


# ---------------- C6 ----------------
def test_read_v1_does_not_crash_and_warns(tmp_path, capsys):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema": "pet-asset/1", "items": [_v1_item()]}),
                 encoding="utf-8")
    data = read_manifest(p)
    assert data["schema_version"] == 1
    assert "schema_version" in capsys.readouterr().err


# ---------------- C7 ----------------
def test_channel_mapping_two_files():
    """eye_1→文件1/R、eye_2→文件1/G、eye_3→文件2/R —— 旧骨架的 i%3 已弃用。"""
    item = _v1_item(3, ("a_pet_eyes.png", "a_pet_eyes_2.png"))
    v2, _ = v1_to_v2({"items": [item]})
    parts = v2["items"][0]["parts"]
    assert (parts[0]["mask"], parts[0]["channel"]) == ("a_pet_eyes.png", "R")
    assert (parts[1]["mask"], parts[1]["channel"]) == ("a_pet_eyes.png", "G")
    assert (parts[2]["mask"], parts[2]["channel"]) == ("a_pet_eyes_2.png", "R")


def test_mask_out_of_range_is_null_with_warning():
    """3 只眼只有 1 个文件 → 第三只 mask=null，不复用前一个文件。"""
    warn = []
    item = _v1_item(3, ("only_one.png",))
    v2, _ = v1_to_v2({"items": [item]}, warn=warn.append)
    third = v2["items"][0]["parts"][2]
    assert third["mask"] is None
    assert third["channel"] == "R"
    assert any("mask=null" in w for w in warn)


def test_decorative_eye_stays_in_g2_not_null():
    """规则②：迁移只物化，装饰眼不擅自置 null。"""
    item = _v1_item(3, ("a.png", "b.png"))
    v2, _ = v1_to_v2({"items": [item]})
    groups = [p["group"] for p in v2["items"][0]["parts"]]
    assert groups == ["g1", "g1", "g2"]
    assert all(g is not None for g in groups)


def test_eye_level_aligned_with_eyes_and_timeline_half_default_false():
    item = _v1_item(2)
    item["eye_level"] = ["L1"]                      # 少一项 → 按 len(eyes) 截断/补齐
    v2, _ = v1_to_v2({"items": [item]})
    levels = [p["eye_level"] for p in v2["items"][0]["parts"]]
    assert levels == ["L1", "auto"]
    tl = v2["items"][0]["timeline"]
    assert tl["items"][0]["group"] == "g1"
    assert tl["items"][0]["half"] is False           # Q2：半闭默认关


def test_migration_writes_backup_and_validates(tmp_path, capsys):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema": "pet-asset/1", "items": [_v1_item(3, ("a.png", "b.png"))]}),
                 encoding="utf-8")
    assert migrate(p) is True
    assert (tmp_path / "manifest.json.v1.bak").exists()
    out = read_manifest(p)
    assert out["schema_version"] == 2
    ok, msg = validate_manifest(out)
    assert ok, msg
    printed = capsys.readouterr().out
    assert "分组表" in printed and "eye_3→g2" in printed


def test_validate_rejects_bad_channel_and_version():
    ok, msg = validate_manifest({"schema_version": 1, "items": []})
    assert not ok and "schema_version" in msg
    bad = {"schema_version": 2, "items": [{
        "stem": "a", "parts": [{"id": "e1", "group": "g1", "channel": "B"}]}]}
    ok, msg = validate_manifest(bad)
    assert not ok and "channel" in msg


def test_schema_json_is_parseable_and_has_channel_enum():
    assert SCHEMA["$defs"]["part"]["properties"]["channel"]["enum"] == ["R", "G"]
    assert SCHEMA["$defs"]["timeline"]["properties"]["items"]["items"][
        "properties"]["half"]["default"] is False


# ---------------- T2 前置：g{n} 一路到 Blink 都不丢信息 ----------------
def test_v2_timeline_group_and_half_reach_blink(tmp_path):
    from anim import blinks_from_plan
    p = tmp_path / "t2.json"
    p.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "a",
        "parts": [
            {"id": "e1", "group": "g1", "channel": "R", "mask": "a.png"},
            {"id": "e2", "group": "g1", "channel": "G", "mask": "a.png"},
        ],
        "timeline": {"enabled": True, "cycle": 8.0, "items": [
            {"group": "g1", "t": 2.0, "hold": 0.4, "half": True}]},
    }]}), encoding="utf-8")
    item = read_manifest(p)["items"][0]
    blinks, cycle = blinks_from_plan(item["blink"])
    assert cycle == 8.0
    assert [(b.group, b.group_no, b.half) for b in blinks] == [("g1", 1, True)]


# ---------------- C8c：派生用全量 parts，索引不能左移 ----------------
def test_c8c_derived_eyes_keep_index_when_part_inactive(tmp_path):
    """mask=null 只影响通道选择，派生 eyes 仍按全量 parts 保序（长度 3）。"""
    ell = [{"cx": 10 + i * 20, "cy": 20, "rx": 3, "ry": 4} for i in range(3)]
    p = tmp_path / "c8c.json"
    p.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "a",
        "parts": [
            {"id": "e1", "group": "g1", "channel": "R", "mask": "a.png",
             "ellipse": ell[0]},
            {"id": "e2", "group": "g1", "channel": "G", "mask": "a.png",
             "ellipse": ell[1]},
            {"id": "e3", "group": "g2", "channel": "R", "mask": None,
             "ellipse": ell[2]},
        ],
    }]}), encoding="utf-8")
    it = read_manifest(p)["items"][0]
    assert it["eyes"] == ell                      # 派生用全量 parts，索引稳定
    assert [x["id"] for x in active_eye_parts(it)] == ["e1", "e2"]
    assert it["eyes"][2] == ell[2]                 # 第三只还是原来那只


# ---------------- 逐素材兜底：eye_parts 白名单 ----------------
def test_eye_parts_whitelist_rescues_group_null_only():
    item = {"stem": "wl_ok", "eye_parts": ["eye_3"], "parts": [
        {"id": "eye_1", "group": "g1", "channel": "R", "mask": "a.png"},
        {"id": "eye_3", "group": None, "channel": "R", "mask": "b.png"},
    ]}
    assert [p["id"] for p in active_eye_parts(item)] == ["eye_3"]


# ---------------- C8d：白名单不能绕开 mask=null ----------------
def test_c8d_whitelist_cannot_resurrect_mask_null(capsys):
    """C8d：白名单塞进 mask=null 的 part → 仍不激活、不抛异常、打明确 warn。"""
    item = {"stem": "wl_bad", "eye_parts": ["eye_2"], "parts": [
        {"id": "eye_1", "group": "g1", "channel": "R", "mask": "a.png"},
        {"id": "eye_2", "group": "g1", "channel": "G", "mask": None},
    ]}
    for _ in range(3):
        assert active_eye_parts(item) == []
    err = capsys.readouterr().err
    assert "wl_bad" in err and "mask=null" in err
    assert err.count("eye_2") == 1          # 只打一次，不刷屏


# ---------------- T6：active 下标指回原始 eyes ----------------
def test_active_eye_indices_v1_does_not_filter():
    item = {"eyes": [{"cx": 1, "cy": 1, "rx": 2, "ry": 2}],
            "eyes_mask": []}
    assert active_eye_indices(item) is None


def test_active_eye_indices_points_back_to_original_eyes(tmp_path):
    ell = [{"cx": 10 + i * 20, "cy": 20, "rx": 3, "ry": 4} for i in range(3)]
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "a", "parts": [
            {"id": "e1", "eye_index": 0, "group": "g1", "channel": "R",
             "mask": "a.png", "ellipse": ell[0]},
            {"id": "e2", "eye_index": 1, "group": "g1", "channel": "G",
             "mask": "a.png", "ellipse": ell[1]},
            {"id": "e3", "eye_index": 2, "group": "g2", "channel": "R",
             "mask": None, "ellipse": ell[2]},
        ],
    }]}), encoding="utf-8")
    it = read_manifest(p)["items"][0]
    assert active_eye_indices(it) == [0, 1]      # 不重编号，索引仍指回原 eyes
    assert it["eyes"] == ell                     # 派生仍是全量


# ---------------- C8 ----------------
def test_group_null_part_is_inactive():
    item = {"stem": "a", "parts": [
        {"id": "eye_l", "group": "g1", "channel": "R", "mask": "a.png"},
        {"id": "eye_r", "group": "g1", "channel": "G", "mask": "a.png"},
        {"id": "cat_eye", "group": None, "channel": "R", "mask": "b.png"},
    ]}
    active = active_eye_parts(item)
    assert [p["id"] for p in active] == ["eye_l", "eye_r"]


# ---------------- C8b：mask=null 是第三种状态 ----------------
def test_c8b_mask_null_part_is_inactive_and_silent(capsys):
    """group 有值但 mask 越界被清空：不进任何通道、不抛异常、不重复 warn。"""
    item = {"stem": "a", "parts": [
        {"id": "eye_1", "group": "g1", "channel": "R", "mask": "a.png"},
        {"id": "eye_2", "group": "g1", "channel": "G", "mask": "a.png"},
        {"id": "eye_3", "group": "g2", "channel": "R", "mask": None},
    ]}
    for _ in range(3):                       # 反复取也不该刷屏/抛异常
        active = active_eye_parts(item)
        assert [p["id"] for p in active] == ["eye_1", "eye_2"]
    assert capsys.readouterr().err == ""


# ---------------- P0-②：group 归一化只在 read_manifest ----------------
def test_group_normalized_to_gstr_on_read(tmp_path):
    """v2 里混入旧 int 组号：read_manifest 统一成 g{n}，下游不再判类型。"""
    p = tmp_path / "norm.json"
    p.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "a",
        "parts": [
            {"id": "e1", "group": 1, "channel": "R", "mask": "a.png"},
            {"id": "e2", "group": "g2", "channel": "G", "mask": "a.png"},
        ],
        "timeline": {"enabled": True, "cycle": 8.0,
                     "items": [{"group": 1, "t": 2.0, "hold": 0.2}]},
    }]}), encoding="utf-8")
    out = read_manifest(p)
    it = out["items"][0]
    assert [p["group"] for p in it["parts"]] == ["g1", "g2"]
    assert it["timeline"]["items"][0]["group"] == "g1"
    # 归一化后的东西必须过校验（part.group 只认字符串/null）
    ok, msg = validate_manifest(out)
    assert ok, msg


# ---------------- P0-③：并存字段是派生视图 ----------------
def test_legacy_fields_are_derived_from_parts(tmp_path):
    """并存的 eyes/eye_level/blink 被 parts/timeline 反向覆盖，不信另一份。"""
    p = tmp_path / "derived.json"
    p.write_text(json.dumps({"schema_version": 2, "items": [{
        "stem": "a",
        "eyes": [{"cx": 999, "cy": 999, "rx": 1, "ry": 1}],   # 故意写错
        "eye_level": ["L0"],                                  # 故意写短
        "parts": [
            {"id": "e1", "group": "g1", "channel": "R", "mask": "a.png",
             "eye_level": "L2", "ellipse": {"cx": 10, "cy": 20, "rx": 3, "ry": 4}},
            {"id": "e2", "group": "g1", "channel": "G", "mask": "a.png",
             "eye_level": "L1", "ellipse": {"cx": 30, "cy": 20, "rx": 3, "ry": 4}},
        ],
        "timeline": {"enabled": True, "cycle": 8.0,
                     "items": [{"group": "g1", "t": 2.0, "hold": 0.2}]},
    }]}), encoding="utf-8")
    out = read_manifest(p)
    it = out["items"][0]
    assert it["eyes"] == [{"cx": 10, "cy": 20, "rx": 3, "ry": 4},
                          {"cx": 30, "cy": 20, "rx": 3, "ry": 4}]
    assert it["eye_level"] == ["L2", "L1"]
    assert it["blink"]["items"] == [{"group": 1, "t": 2.0, "hold": 0.2,
                                     "count": 2, "levels": None,
                                     "half": False}]


def test_migrated_legacy_fields_are_consistent(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"items": [_v1_item(3, ("a.png", "b.png"))]}),
                 encoding="utf-8")
    out, _ = v1_to_v2({"items": [_v1_item(3, ("a.png", "b.png"))]})
    assert all(legacy_consistent(it)[0] for it in out["items"])
    assert migrate(p) is True
    data = read_manifest(p)
    assert all(legacy_consistent(it)[0] for it in data["items"])


# ---------------- P1-D：schema 按位置收紧 ----------------
def test_schema_group_types_are_position_specific():
    part = SCHEMA["$defs"]["part"]["properties"]["group"]
    types = [o.get("type") for o in part["oneOf"]]
    assert "integer" not in types and "null" in types, types
    ti = SCHEMA["$defs"]["timeline"]["properties"]["items"]["items"]
    types = [o.get("type") for o in ti["properties"]["group"]["oneOf"]]
    assert "integer" in types and "null" not in types, types


def test_validator_rejects_group_null_in_timeline_and_int_in_part():
    base = {"schema_version": 2, "items": [{
        "stem": "a", "parts": [{"id": "e1", "group": "g1", "channel": "R",
                                "mask": "a.png"}],
        "timeline": {"items": [{"group": "g1", "t": 0, "hold": 0.2}]}}]}
    ok, _ = validate_manifest(base)
    assert ok

    bad_tl = json.loads(json.dumps(base))
    bad_tl["items"][0]["timeline"]["items"][0]["group"] = None
    ok, msg = validate_manifest(bad_tl)
    assert not ok and "group" in msg

    bad_part = json.loads(json.dumps(base))
    bad_part["items"][0]["parts"][0]["group"] = 1
    ok, msg = validate_manifest(bad_part)
    assert not ok and "group" in msg


# ---------------- P1-E：schema 与 validator 常量表一致 ----------------
def test_schema_matches_validator_constants():
    part = SCHEMA["$defs"]["part"]
    assert part["properties"]["channel"]["enum"] == list(CHANNEL_ENUM)
    assert set(part["required"]) == {"id", "group", "channel"}
    ti = SCHEMA["$defs"]["timeline"]["properties"]["items"]["items"]
    assert set(ti["required"]) == {"group", "t", "hold"}
    assert ti["properties"]["half"]["default"] is False
    assert SCHEMA["properties"]["schema_version"]["const"] == 2


# ---------------- P1-F：v1 降级 warning 只打一次 ----------------
def test_v1_warning_printed_once_per_file(tmp_path, capsys):
    p = tmp_path / "once_only_v1.json"
    p.write_text(json.dumps({"items": [_v1_item()]}), encoding="utf-8")
    read_manifest(p)
    read_manifest(p)
    read_manifest(p)
    err = capsys.readouterr().err
    assert err.count("没有 schema_version") == 1


# ---------------- P1-G：--check 纯只读 ----------------
def test_check_mode_writes_nothing(tmp_path):
    p = tmp_path / "readonly.json"
    raw = json.dumps({"items": [_v1_item(3, ("a.png", "b.png"))]})
    p.write_text(raw, encoding="utf-8")
    assert migrate(p, check=True) is True
    assert p.read_text(encoding="utf-8") == raw
    assert not (tmp_path / "readonly.json.v1.bak").exists()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["readonly.json"]
