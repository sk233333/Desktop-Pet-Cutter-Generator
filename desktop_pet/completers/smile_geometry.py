"""expr.smile_geometry：配置期「补微笑」的零 AI 几何生成器（B-1）。

只产数据：``items[].smile_geometry`` + 独立覆盖层 ``<stem>_smile.png``。
不注册 ``smile`` 状态 —— 不进 ``ExprBank.STATES``、不改 ``smile → half``
别名表、不进 ``derive_allowed()`` / 向导 ``allowed`` 提案。``runtime_consumer``
恒为 ``null``，当前没有运行时读方（已实现 / 未接线）。

几何组合只包含三项：眼睛轻眯、嘴角弧、腮红 alpha；不复刻 ``mouth.open``
的三档微笑嘴贴片。覆盖层不覆盖原图、不改 base / mask。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from completers.base import (Candidate, ChangeSet, OverlayImage, Report,
                             encode_png, guard_paths, load_base_rgb)

COMPLETER_ID = "expr.smile_geometry"
_KIND = "smile_geometry"
SCHEMA = "smile-geometry/1"

# 三档人工点选；数值只做几何占位，质量由图/人眼决定
PRESETS = {
    "light": {"lid_drop": 0.12, "lid_sag": 0.04, "arc_ratio": 0.38,
              "blush_alpha": 40},
    "medium": {"lid_drop": 0.20, "lid_sag": 0.07, "arc_ratio": 0.48,
               "blush_alpha": 64},
    "strong": {"lid_drop": 0.28, "lid_sag": 0.10, "arc_ratio": 0.58,
               "blush_alpha": 88},
}
DEFAULT_PRESET = "medium"
_PARAM_RANGES = {
    "lid_drop": (0.0, 0.45),
    "lid_sag": (0.0, 0.25),
    "arc_ratio": (0.0, 1.0),
    "blush_alpha": (0.0, 160.0),
}


def _sku_dir(sku) -> Path:
    return Path(getattr(sku, "dir", sku))


def _sku_name(sku) -> str:
    return str(getattr(sku, "name", _sku_dir(sku).name))


def _fit(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _sanitize(raw: dict) -> dict:
    """把任意参数组合夹到合法区间；缺项用 medium 档兜底。"""
    base = PRESETS[DEFAULT_PRESET]
    out: dict[str, float] = {}
    for key, (lo, hi) in _PARAM_RANGES.items():
        out[key] = _fit(raw.get(key, base[key]), lo, hi)
    out["blush_alpha"] = float(int(round(out["blush_alpha"])))
    return out


def _params(preset) -> dict:
    return _sanitize(PRESETS.get(str(preset)) or PRESETS[DEFAULT_PRESET])


def _pick_eyes(item: dict) -> list[dict]:
    """取参与几何的眼睛：v2 走 active_eye_indices，v1 取前两只（真眼在列表前）。"""
    eyes = [e for e in (item.get("eyes") or []) if isinstance(e, dict)]
    if not eyes:
        return []
    parts = [p for p in (item.get("parts") or []) if isinstance(p, dict)]
    picked = eyes
    if parts:
        from manifest_io import active_eye_indices
        idxs = active_eye_indices(item)
        if idxs is not None:
            picked = [eyes[i] for i in idxs if 0 <= i < len(eyes)]
    if len(picked) > 2:
        picked = picked[:2]
    out = []
    for e in picked:
        out.append({
            "cx": float(e.get("cx", 0.0)),
            "cy": float(e.get("cy", 0.0)),
            "rx": max(1.0, float(e.get("rx", 0.0))),
            "ry": max(1.0, float(e.get("ry", 0.0))),
            "dark": list(e.get("dark") or []),
        })
    return out


def _geometry(eyes: list[dict], width: int, height: int,
              params: dict) -> dict:
    """纯几何：眼睑线 / 嘴角弧 / 腮红椭圆；全部夹进画布。"""
    n = len(eyes)
    ex = sum(e["cx"] for e in eyes) / n
    ey = sum(e["cy"] for e in eyes) / n
    rx = sum(e["rx"] for e in eyes) / n
    ry = sum(e["ry"] for e in eyes) / n
    span = abs(eyes[1]["cx"] - eyes[0]["cx"]) if n > 1 else rx * 2.5

    g_eyes = []
    for e in eyes:
        lid_y = e["cy"] - e["ry"] + params["lid_drop"] * 2.0 * e["ry"]
        g_eyes.append({
            "cx": round(_fit(e["cx"], 0.0, width - 1.0), 3),
            "cy": round(_fit(e["cy"], 0.0, height - 1.0), 3),
            "rx": round(_fit(e["rx"], 1.0, width / 2.0), 3),
            "ry": round(_fit(e["ry"], 1.0, height / 2.0), 3),
            "lid_y": round(_fit(lid_y, 0.0, height - 1.0), 3),
        })

    half = max(6.0, span * 0.30 if n > 1 else rx * 3.0)
    arc_h = max(2.0, half * params["arc_ratio"])
    mouth = {
        "cx": round(_fit(ex, 0.0, width - 1.0), 3),
        "cy": round(_fit(max(e["cy"] + e["ry"] for e in eyes)
                         + max(6.0, ry * 1.8), 0.0, height - 1.0), 3),
        "half": round(_fit(half, 1.0, width * 0.45), 3),
        "height": round(_fit(arc_h, 1.0, height * 0.20), 3),
    }

    blush = []
    for e in eyes:
        sign = -1.0 if e["cx"] <= ex else 1.0
        blush.append({
            "cx": round(_fit(e["cx"] + sign * e["rx"] * 0.8,
                             0.0, width - 1.0), 3),
            "cy": round(_fit(e["cy"] + e["ry"] * 1.6,
                             0.0, height - 1.0), 3),
            "rx": round(_fit(max(3.0, e["rx"] * 1.0), 1.0, width / 2.0), 3),
            "ry": round(_fit(max(2.0, e["ry"] * 0.7), 1.0, height / 2.0), 3),
            "alpha": int(round(params["blush_alpha"])),
        })
    return {"eyes": g_eyes, "mouth_arc": mouth, "blush": blush}


def _color(eyes: list[dict]) -> tuple[int, int, int, int]:
    dark = (eyes[0].get("dark") or []) if eyes else []
    if len(dark) >= 3:
        r, g, b = (int(dark[0]), int(dark[1]), int(dark[2]))
    else:
        r, g, b = 45, 40, 48
    return b, g, r, 255


def _draw(canvas: np.ndarray, eyes: list[dict], width: int, height: int,
          preset: str) -> np.ndarray:
    params = _params(preset)
    geom = _geometry(eyes, width, height, params)
    color = _color(eyes)
    thick = 2 if max(width, height) >= 256 else 1

    # 腮红先画，眼睑线与嘴角弧后画，保持线条干净
    for b in geom["blush"]:
        cv2.ellipse(canvas,
                    (int(round(b["cx"])), int(round(b["cy"]))),
                    (max(1, int(round(b["rx"]))), max(1, int(round(b["ry"])))),
                    0, 0, 360, (180, 140, 255, b["alpha"]), -1, cv2.LINE_AA)

    for e in geom["eyes"]:
        pts = []
        for t in np.linspace(-1.0, 1.0, 13):
            pts.append((int(round(e["cx"] + t * e["rx"])),
                        int(round(e["lid_y"]
                                  + params["lid_sag"] * e["ry"]
                                  * 2.0 * (1.0 - t * t)))))
        cv2.polylines(canvas, [np.array(pts, np.int32)], False, color, thick)

    m = geom["mouth_arc"]
    pts = []
    for t in np.linspace(-1.0, 1.0, 17):
        pts.append((int(round(m["cx"] + t * m["half"])),
                    int(round(m["cy"] + m["height"] * (1.0 - t * t)))))
    cv2.polylines(canvas, [np.array(pts, np.int32)], False, color, thick)
    return canvas


def _slot(payload: dict, preset: str) -> dict:
    params = _params(preset)
    name = f"{payload['stem']}_smile.png"
    geom = _geometry(payload["eyes"], int(payload["width"]),
                     int(payload["height"]), params)
    return {
        "schema": SCHEMA,
        "preset": preset,
        "overlay": name,
        "runtime_consumer": None,
        "params": params,
        "eyes": geom["eyes"],
        "mouth_arc": geom["mouth_arc"],
        "blush": geom["blush"],
    }


def propose(sku, manifest: dict) -> list[Candidate]:
    """只读：给「有眼睛、有 base 图」的素材各出一个候选。"""
    sku_dir, sku_name = _sku_dir(sku), _sku_name(sku)
    out: list[Candidate] = []
    for i, item in enumerate(manifest.get("items") or []):
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "")
        eyes = _pick_eyes(item)
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if not stem or not eyes or width <= 0 or height <= 0:
            continue
        if not (sku_dir / f"{stem}.png").exists():
            continue
        out.append(Candidate(
            id=f"{COMPLETER_ID}:{stem}",
            kind=_KIND,
            payload={"sku": sku_name, "sku_dir": str(sku_dir),
                     "item_index": i, "stem": stem,
                     "width": width, "height": height,
                     "eyes": eyes, "presets": list(PRESETS),
                     "preset": DEFAULT_PRESET,
                     "existing": item.get("smile_geometry") or None,
                     "has_mouth": bool(item.get("mouth_dir"))},
            note=("配置期几何数据槽：眼睛轻眯 / 嘴角弧 / 腮红 alpha；"
                  "独立覆盖层，不改 STATES / 别名表，未接线"),
        ))
    return out


def preview(cand: Candidate, engine=None) -> OverlayImage:
    p = cand.payload
    preset = str(p.get("preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    base = load_base_rgb(p["sku_dir"], p["stem"])
    overlay = np.zeros((int(p["height"]), int(p["width"]), 4), np.uint8)
    _draw(overlay, p["eyes"], int(p["width"]), int(p["height"]), preset)
    extra = "；已有 mouth_dir，嘴角弧仅作预览/数据" if p.get("has_mouth") else ""
    return OverlayImage(
        base=base, overlay=overlay,
        note=f"{cand.note}（当前档：{preset}{extra}）")


def commit(cand: Candidate) -> ChangeSet:
    p = cand.payload
    preset = str(p.get("preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    canvas = np.zeros((int(p["height"]), int(p["width"]), 4), np.uint8)
    _draw(canvas, p["eyes"], int(p["width"]), int(p["height"]), preset)
    slot = _slot(p, preset)
    return ChangeSet(
        sku=p["sku"], completer=COMPLETER_ID, candidate_id=cand.id,
        files={slot["overlay"]: encode_png(canvas)},
        manifest_patch={f"items/{p['item_index']}/smile_geometry": slot},
        notes=(f"expr.smile_geometry：{preset} 档 → {slot['overlay']} + "
               f"items[{p['item_index']}].smile_geometry（runtime_consumer=null）"),
        meta={"stem": p["stem"], "preset": preset,
              "item_index": p["item_index"], "slot": slot},
    )


def verify(changeset: ChangeSet, sku_dir=None, manifest_after=None) -> Report:
    lines: list[str] = []
    ok = True
    try:
        guard_paths(sku_dir or ".", changeset.paths)
        lines.append(f"路径守卫：{len(changeset.paths)} 个文件都在 SKU 目录内")
    except Exception as exc:                 # noqa: BLE001
        return Report(False, [f"路径守卫失败：{exc}"])

    base_name = f"{changeset.meta.get('stem')}.png"
    if base_name in changeset.paths:
        ok = False
        lines.append("禁止覆盖 base 原图")

    slot = changeset.meta.get("slot") or {}
    if slot.get("runtime_consumer") is not None:
        ok = False
        lines.append("runtime_consumer 必须是 null（B-1 未接线）")
    if slot.get("preset") not in PRESETS:
        ok = False
        lines.append(f"preset 非法：{slot.get('preset')}")

    for name, data in changeset.files.items():
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3 or img.shape[2] != 4:
            ok = False
            lines.append(f"{name}: 覆盖层为空或不是 RGBA")
            continue
        if not (img[..., 3] > 0).any():
            ok = False
            lines.append(f"{name}: 覆盖层全透明")
        if name != slot.get("overlay"):
            ok = False
            lines.append(f"{name}: 与 smile_geometry.overlay 不一致")
        try:
            json.loads(json.dumps(slot))     # 数据槽必须可 JSON 序列化
        except (TypeError, ValueError) as exc:
            ok = False
            lines.append(f"smile_geometry 不可序列化：{exc}")

    if manifest_after is not None:
        from manifest_io import validate_manifest
        good, msg = validate_manifest(manifest_after)
        ok = ok and good
        lines.append(f"validate_manifest：{msg}")
        item = next((it for it in manifest_after.get("items") or []
                     if isinstance(it, dict)
                     and it.get("stem") == changeset.meta.get("stem")), None)
        if item is None:
            ok = False
            lines.append("manifest 里找不到写入的 item")
        elif item.get("smile_geometry") != slot:
            ok = False
            lines.append("smile_geometry 未按预期写入")

    try:
        from expr_state import STATES
        if STATES != ('normal', 'blink', 'half', 'surprise', 'wink_l',
                      'wink_r', 'smile'):
            ok = False
            lines.append(f"ExprBank.STATES 被改动：{STATES}")
        else:
            lines.append("ExprBank.STATES 新 golden 未变（七态）")
    except Exception as exc:                 # noqa: BLE001
        lines.append(f"STATES 检查跳过：{exc}")
    return Report(ok, lines)
