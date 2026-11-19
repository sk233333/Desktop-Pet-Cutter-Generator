"""mouth.open：补 ``mouth_dir`` 空槽，产物是独立覆盖层 PNG。

不新增 ``ExprBank.STATES``、不改 ``apply()`` 签名或别名表；三档为
闭 / 微笑 / 小 o。线宽与颜色从原图的眼睛 ``dark`` 取参数。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from completers.base import (Candidate, ChangeSet, OverlayImage, Report,
                             encode_png, guard_paths, load_base_rgb)

COMPLETER_ID = "mouth.open"
_KIND = "mouth"
LEVELS = ("closed", "smile", "o")
_LEVEL_LABEL = {"closed": "闭", "smile": "微笑", "o": "小 o"}


def _sku_dir(sku) -> Path:
    return Path(getattr(sku, "dir", sku))


def _sku_name(sku) -> str:
    return str(getattr(sku, "name", _sku_dir(sku).name))


def _eyes(item: dict) -> list[dict]:
    out = []
    for e in item.get("eyes") or []:
        if not isinstance(e, dict):
            continue
        out.append({"cx": float(e.get("cx", 0.0)),
                    "cy": float(e.get("cy", 0.0)),
                    "rx": float(e.get("rx", 0.0)),
                    "ry": float(e.get("ry", 0.0)),
                    "dark": list(e.get("dark") or [])})
    return out


def propose(sku, manifest: dict) -> list[Candidate]:
    """只读：给「有眼睛、还没有 mouth_dir」的素材各出一个候选。"""
    sku_dir, sku_name = _sku_dir(sku), _sku_name(sku)
    out: list[Candidate] = []
    for i, item in enumerate(manifest.get("items") or []):
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "")
        eyes = _eyes(item)
        if not stem or not eyes or item.get("mouth_dir"):
            continue
        out.append(Candidate(
            id=f"{COMPLETER_ID}:{stem}",
            kind=_KIND,
            payload={"sku": sku_name, "sku_dir": str(sku_dir),
                     "item_index": i, "stem": stem,
                     "width": int(item.get("width") or 0),
                     "height": int(item.get("height") or 0),
                     "eyes": eyes, "levels": list(LEVELS),
                     "selected": "smile"},
            note="独立覆盖层：不碰原图、不改 STATES / 别名表",
        ))
    return out


def _geom(payload: dict) -> tuple[float, float, float, float]:
    eyes = payload["eyes"]
    e0 = eyes[0]
    e1 = eyes[1] if len(eyes) > 1 else eyes[0]
    cx = (e0["cx"] + e1["cx"]) / 2.0
    below = max(e0["cy"], e1["cy"]) + max(e0["ry"], e1["ry"])
    cy = below + max(4.0, max(e0["ry"], e1["ry"]))
    span = abs(e1["cx"] - e0["cx"]) if len(eyes) > 1 else e0["rx"] * 3.0
    half = max(5.0, span * 0.32)
    return cx, cy, half, max(2.0, half * 0.5)


def _color(payload: dict) -> tuple[int, int, int, int]:
    dark = (payload["eyes"][0].get("dark") or []) if payload["eyes"] else []
    if len(dark) >= 3:
        r, g, b = (int(dark[0]), int(dark[1]), int(dark[2]))
    else:
        r, g, b = 45, 40, 48
    return b, g, r, 255


def _draw(canvas: np.ndarray, payload: dict, level: str) -> np.ndarray:
    cx, cy, half, height = _geom(payload)
    color = _color(payload)
    thick = 2 if max(payload["width"], payload["height"]) >= 256 else 1
    if level == "o":
        cv2.ellipse(canvas, (int(round(cx)), int(round(cy))),
                    (max(2, int(round(height))), max(2, int(round(height * 1.2)))),
                    0, 0, 360, color, thick)
    elif level == "smile":
        pts = []
        for t in np.linspace(-1.0, 1.0, 13):
            pts.append((int(round(cx + t * half)),
                        int(round(cy + height * (1.0 - t * t)))))
        cv2.polylines(canvas, [np.array(pts, np.int32)], False, color, thick)
    else:                                     # closed
        cv2.line(canvas, (int(round(cx - half)), int(round(cy))),
                 (int(round(cx + half)), int(round(cy))), color, thick)
    return canvas


def preview(cand: Candidate, engine=None) -> OverlayImage:
    p = cand.payload
    level = p.get("selected") or "smile"
    base = load_base_rgb(p["sku_dir"], p["stem"])
    overlay = np.zeros((p["height"], p["width"], 4), np.uint8)
    _draw(overlay, p, level)
    return OverlayImage(base=base, overlay=overlay,
                        note=f"{cand.note}（当前档：{_LEVEL_LABEL.get(level, level)}）")


def commit(cand: Candidate) -> ChangeSet:
    p = cand.payload
    level = p.get("selected") or "smile"
    if level not in LEVELS:
        level = "smile"
    canvas = np.zeros((int(p["height"]), int(p["width"]), 4), np.uint8)
    _draw(canvas, p, level)
    name = f"{p['stem']}_mouth_{level}.png"
    patch = {
        f"items/{p['item_index']}/mouth_dir": name,
        f"items/{p['item_index']}/mouth_level": level,
    }
    return ChangeSet(sku=p["sku"], completer=COMPLETER_ID,
                     candidate_id=cand.id,
                     files={name: encode_png(canvas)},
                     manifest_patch=patch,
                     notes=f"mouth.open：{_LEVEL_LABEL.get(level, level)} 档 → {name}",
                     meta={"stem": p["stem"], "level": level})


def verify(changeset: ChangeSet, sku_dir=None, manifest_after=None) -> Report:
    lines: list[str] = []
    ok = True
    try:
        guard_paths(sku_dir or ".", changeset.paths)
        lines.append(f"路径守卫：{len(changeset.paths)} 个文件都在 SKU 目录内")
    except Exception as exc:                 # noqa: BLE001
        return Report(False, [f"路径守卫失败：{exc}"])

    if manifest_after is not None:
        from manifest_io import validate_manifest
        good, msg = validate_manifest(manifest_after)
        ok = ok and good
        lines.append(f"validate_manifest：{msg}")
        for i, item in enumerate(manifest_after.get("items") or []):
            if item.get("stem") == changeset.meta.get("stem"):
                if item.get("mouth_dir") != changeset.manifest_patch.get(
                        f"items/{i}/mouth_dir"):
                    ok = False
                    lines.append("mouth_dir 未按预期写入")
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

    for name, data in changeset.files.items():
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None or not (img[..., 3] > 0).any():
            ok = False
            lines.append(f"{name}: 覆盖层为空或解码失败")
    return Report(ok, lines)
