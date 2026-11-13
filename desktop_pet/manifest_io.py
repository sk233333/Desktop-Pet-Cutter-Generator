"""manifest v2 读写：schema 校验 + v1 兼容读取 + 迁移转换。

纯 Python、零第三方依赖（不引 jsonschema）。规则见《收敛补丁清单》第 6/7 节
与《裁定回执》：

- v1（无 `schema_version`）读入不崩溃，打 warning，原样返回并标 `schema_version: 1`
- v2 的 `parts[].mask + channel`：`file_idx = i // 2`、`channel = R/G`（B 恒为高光）
- mask 文件越界时写 `mask: null` + 警告，**禁止**静默复用最后一个文件
- 迁移只做「隐式规则物化」，装饰眼仍留在 g2；`group: null` 由人工在 v2 里改
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_VERSION = 2
CHANNEL_ENUM = ("R", "G")
_WARNED: set[str] = set()


def _warn(msg: str):
    """同一句只打一次，避免逐帧/逐文件刷屏。"""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    print(f"[manifest] {msg}", file=sys.stderr)


def read_manifest(path) -> dict:
    """读 manifest；v1 自动降级（不崩溃）并打 warning。

    这里是**唯一的归一化层**：
    - v1 降级、或 v2 里混入旧 int 组号 → 一律转成 `g{n}`
    - v2 的 `eyes / eye_level / eyes_mask / blink` 视为由 `parts / timeline`
      反向生成的派生视图，不信任并存的另一份（现渲染路径照常读这些键）
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: manifest 顶层必须是对象")
    if "schema_version" not in data:
        data["schema_version"] = 1
        _warn(f"{p.name} 没有 schema_version，按 v1 解析（建议跑 tools/migrate_manifest_v1_v2.py）")
    _normalize_groups(data)
    if data.get("schema_version") == SCHEMA_VERSION:
        for it in data.get("items") or []:
            _derive_legacy(it, p.name)
    return data


def _normalize_groups(data: dict):
    """group 只在这一层归一化成字符串，下游不再判类型。"""
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        for part in it.get("parts") or []:
            if isinstance(part, dict) and part.get("group") is not None:
                part["group"] = _group_id(part["group"])
        tl = it.get("timeline")
        if isinstance(tl, dict):
            for ti in tl.get("items") or []:
                if isinstance(ti, dict) and ti.get("group") is not None:
                    ti["group"] = _group_id(ti["group"])


def _gid_to_int(gid):
    """g{n} / n / "n" → n（给仍在读旧字段的渲染路径）；其它字符串原样返回。"""
    if isinstance(gid, bool):
        return gid
    if isinstance(gid, int):
        return gid
    s = str(gid)
    if s.isdigit():
        return int(s)
    return int(s[1:]) if s.startswith("g") and s[1:].isdigit() else s


def _derive_legacy(item: dict, name: str = ""):
    """v2 → 旧字段的派生视图；与并存值不一致时警告，并以 parts 为准。"""
    parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        return
    eyes, levels, masks = [], [], []
    for p in parts:
        if isinstance(p.get("ellipse"), dict):
            eyes.append(p["ellipse"])
        levels.append(p.get("eye_level") or "auto")
        m = p.get("mask")
        if isinstance(m, str) and m not in masks:
            masks.append(m)
    tl = item.get("timeline") if isinstance(item.get("timeline"), dict) else {}
    blink = item.get("blink") if isinstance(item.get("blink"), dict) else None
    if blink is not None and blink.get("items"):
        old = [(_gid_to_int(it.get("group")), it.get("t"), it.get("hold"))
               for it in blink["items"]]
        new = [(_gid_to_int(it.get("group")), it.get("t"), it.get("hold"))
               for it in (tl.get("items") or [])]
        if old != new:
            _warn(f"{name}: blink 与 timeline 不一致，以 timeline 为准（派生视图已覆盖）")

    if eyes:
        item["eyes"] = eyes
    item["eye_level"] = levels
    item["eyes_mask"] = masks
    item["blink"] = {
        "enabled": bool(tl.get("enabled", True)),
        "cycle": float(tl.get("cycle", 8.0) or 8.0),
        "items": [
            {"group": _gid_to_int(it.get("group")), "t": float(it.get("t", 0.0)),
             "hold": float(it.get("hold", 0.2)),
             "count": int(it.get("count") or 2),
             "levels": it.get("levels"), "half": bool(it.get("half", False))}
            for it in (tl.get("items") or [])
        ],
    }


def legacy_consistent(item: dict) -> tuple[bool, list[str]]:
    """自检：v2 里并存的旧字段是否与 parts/timeline 的派生视图逐项一致。

    返回 (一致?, 不一致的字段名列表)。迁移后调用；`read_manifest()` 读任何
    v2 也会走同一套派生，所以「一致」是渐进接管成立的前提。
    """
    probe = dict(item)
    _derive_legacy(probe, item.get("stem", ""))
    diffs = []
    parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
    # eyes 只有在 part 带 ellipse 时才可派生（poly 标注没有椭圆）
    if any(isinstance(p.get("ellipse"), dict) for p in parts):
        if probe.get("eyes") != item.get("eyes"):
            diffs.append("eyes")
    for key in ("eye_level", "eyes_mask"):
        if probe.get(key) != item.get(key):
            diffs.append(key)
    a = item.get("blink") if isinstance(item.get("blink"), dict) else {}
    b = probe.get("blink") if isinstance(probe.get("blink"), dict) else {}
    if bool(a.get("enabled")) != bool(b.get("enabled")) \
            or float(a.get("cycle", 0.0) or 0.0) != float(b.get("cycle", 0.0) or 0.0):
        diffs.append("blink.header")
    ai = [(i.get("group"), i.get("t"), i.get("hold")) for i in a.get("items") or []]
    bi = [(i.get("group"), i.get("t"), i.get("hold")) for i in b.get("items") or []]
    if ai != bi:
        diffs.append("blink.items")
    return (not diffs), diffs


def validate_manifest(data: dict) -> tuple[bool, str]:
    """结构校验（v2）。返回 (ok, message)。"""
    if not isinstance(data, dict):
        return False, "manifest 顶层不是对象"
    if data.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version 必须是 {SCHEMA_VERSION}"
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return False, "items 必须是非空数组"

    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return False, f"items[{i}] 不是对象"
        if not it.get("stem"):
            return False, f"items[{i}].stem 缺失"
        parts = it.get("parts")
        if not isinstance(parts, list):
            return False, f"items[{i}].parts 必须是数组"
        seen = set()
        for j, p in enumerate(parts):
            if not isinstance(p, dict):
                return False, f"items[{i}].parts[{j}] 不是对象"
            pid = p.get("id")
            if not pid:
                return False, f"items[{i}].parts[{j}].id 缺失"
            if pid in seen:
                return False, f"items[{i}].parts id 重复：{pid}"
            seen.add(pid)
            if p.get("channel") not in CHANNEL_ENUM:
                return False, f"items[{i}].parts[{j}].channel 必须是 R 或 G"
            mask = p.get("mask")
            if mask is not None and not isinstance(mask, str):
                return False, f"items[{i}].parts[{j}].mask 必须是字符串或 null"
            if "group" not in p:
                return False, f"items[{i}].parts[{j}].group 缺失（装饰件写 null）"
            grp = p["group"]
            # v2 的 part.group 只认 g{n} 或 null；旧 int 组号由 read_manifest 归一化
            if not (grp is None or isinstance(grp, str)):
                return False, f"items[{i}].parts[{j}].group 必须是字符串或 null"
        tl = it.get("timeline")
        if tl is not None:
            if not isinstance(tl, dict):
                return False, f"items[{i}].timeline 必须是对象"
            for k, ti in enumerate(tl.get("items") or []):
                if not isinstance(ti, dict):
                    return False, f"items[{i}].timeline.items[{k}] 不是对象"
                if not isinstance(ti.get("group"), (str, int)) \
                        or isinstance(ti.get("group"), bool):
                    return False, f"items[{i}].timeline.items[{k}].group 缺失"
                for key in ("t", "hold"):
                    if not isinstance(ti.get(key), (int, float)):
                        return False, f"items[{i}].timeline.items[{k}].{key} 必须是数字"
    return True, "ok"


def _group_id(raw) -> str:
    """v1 的数字组号 → v2 的 g{n}；已是字符串则原样保留。"""
    if isinstance(raw, str):
        return raw
    try:
        return f"g{int(raw)}"
    except (TypeError, ValueError):
        return "g1"


def active_eye_parts(item: dict) -> list[dict]:
    """参与眨眼/视线/表情的 part。

    默认规则：`group is None`（装饰件）与 `mask is None`（mask 文件越界清空）
    都不参与 —— 后者是第三种 part 状态，读不到文件、不抛异常、不再打 warn
    （越界 warn 只在迁移时打一次），避免每帧拿 None 去读文件。

    逐素材兜底：item.eye_parts 给一份 id 白名单时以它为准 —— **但只能救回
    「mask 存在、只是 group=null」的 part**。mask=null 是硬约束，不可被白名单
    覆盖：否则下游照样拿 None 去读文件，P0-① 的保护从配置层被绕过。
    实现上 by_id 必须从 usable 构建（见下方断言），不能改成从全量 parts 建表。
    """
    parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
    usable = [p for p in parts if p.get("mask") is not None]
    forced = item.get("eye_parts")
    if isinstance(forced, list) and forced:
        by_id = {p.get("id"): p for p in usable}
        # 不变量：by_id 只能由 usable 构建。若有人改成从全量 parts 建表，
        # mask=null 的 id 会被直接 append，P0-① 的保护就从配置层穿了。
        assert all(p.get("mask") is not None for p in by_id.values())
        out = []
        for pid in forced:
            if pid in by_id:
                out.append(by_id[pid])
            else:
                _warn(f"{item.get('stem', '?')}: eye_parts 里的 {pid} 没有可用 "
                      f"mask，仍不参与通道（mask=null 不可被白名单覆盖）")
        return out
    return [p for p in usable if p.get("group") is not None]


def active_eye_indices(item: dict) -> list[int] | None:
    """active part 指回的原始 `eyes` 下标。

    返回 None 表示「没有 parts（v1）→ 不过滤」。过滤只决定哪些下标参与
    眨眼/视线/表情，**绝不重编号**：几何一律取 `eyes[eye_index]`，
    否则像 da90 那种坐标错位会再犯一次。
    """
    parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        # v1：没有 parts，返回 None = 不过滤，保持旧行为（眨眼/视线仍用全部 eyes）。
        # 但「迁移后会变成 0 只 active」必须提前可见，否则宠物突然不眨眼会被当成坏了。
        eyes = item.get("eyes") or []
        masks = item.get("eyes_mask") or []
        would = [i for i in range(len(eyes)) if i // 2 < len(masks)]
        if eyes and not would:
            _warn(f"{item.get('stem', '?')}: 没有可用 eyes.png，迁移后 active=0"
                  f"（眨眼/视线会关闭）；请先补 mask 再迁移")
        return None
    active = set(id(p) for p in active_eye_parts(item))
    out = []
    for k, p in enumerate(parts):
        if id(p) not in active:
            continue
        idx = p.get("eye_index")
        out.append(idx if isinstance(idx, int) and not isinstance(idx, bool) else k)
    return out


def v1_item_to_v2(item: dict, warn=None) -> dict:
    """把单个 v1 item 转成 v2：物化隐式分组，保留旧字段供现渲染路径读取。"""
    warn = warn or _warn
    eyes = list(item.get("eyes") or [])
    levels = list(item.get("eye_level") or [])
    masks = list(item.get("eyes_mask") or [])
    n = len(eyes)
    levels = (levels + ["auto"] * n)[:n]

    parts = []
    for i in range(n):
        file_idx = i // 2                        # 每个 mask 文件装 2 只眼
        channel = "R" if i % 2 == 0 else "G"     # B 恒为高光，不对应任何 eye part
        mask = masks[file_idx] if file_idx < len(masks) else None
        if mask is None:
            warn(f"{item.get('stem')}: 第 {i + 1} 只眼找不到 mask 文件"
                 f"（file_idx={file_idx}，共 {len(masks)} 个），写成 mask=null")
        part = {
            "id": f"eye_{i + 1}",
            "eye_index": i,                      # 指回原始 eyes 下标，过滤时不重编号
            "group": f"g{i // 2 + 1}",           # 隐式「每两只一组」物化，装饰眼不特殊处理
            "eye_level": levels[i],
            "mask": mask,
            "channel": channel,
        }
        if isinstance(eyes[i], dict):
            part["ellipse"] = eyes[i]            # 旧渲染路径仍用椭圆
        parts.append(part)

    blink = item.get("blink") if isinstance(item.get("blink"), dict) else {}
    timeline = {
        "enabled": bool(blink.get("enabled", True)),
        "cycle": float(blink.get("cycle", 8.0) or 8.0),
        "items": [
            {
                "group": _group_id(it.get("group", 1)),
                "t": float(it.get("t", 0.0)),
                "hold": float(it.get("hold", 0.2)),
                "half": False,                   # Q2：半闭默认关
            }
            for it in (blink.get("items") or [])
        ],
    }

    out = dict(item)                             # 保留 v1 字段：现渲染路径还在读
    out["parts"] = parts
    out["timeline"] = timeline
    out.setdefault("effects", item.get("effects"))
    out.setdefault("gaze", item.get("gaze", True))
    # 并存的旧字段统一改成「由 parts/timeline 派生」的那一份，
    # 迁移产物与运行时 read_manifest() 派生的结果逐项一致。
    _derive_legacy(out, str(item.get("stem", "")))
    return out


def v1_to_v2(data: dict, warn=None) -> tuple[dict, list[str]]:
    """整份 v1 manifest → v2；同时返回「推导出的分组表」供人工核对。"""
    items = data.get("items") or []
    rows = []
    new_items = []
    for it in items:
        new_items.append(v1_item_to_v2(it, warn))
        cells = [f"{p['id']}→{p['group']}({p['mask'] or 'null'}/{p['channel']})"
                 for p in new_items[-1]["parts"]]
        rows.append(f"{it.get('stem', '?')}: " + "  ".join(cells))
    out = dict(data)
    out["schema_version"] = SCHEMA_VERSION
    out["items"] = new_items
    return out, rows
