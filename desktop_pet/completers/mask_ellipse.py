"""mask.from_ellipse：用 manifest 里已有的 ``eyes`` 椭圆生成眼眶 socket。

椭圆 socket 是「恢复能力的下限实现」：它能立刻让眨眼 / 视线重新有 mask 可用，
但贴合度不如手工重画。**已有 mask 的素材整个跳过**，不覆盖、不重跑。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from completers.base import (Candidate, ChangeSet, OverlayImage, Report,
                             encode_png, guard_paths, load_base_rgb)

COMPLETER_ID = "mask.from_ellipse"
_KIND = "mask"


def _sku_dir(sku) -> Path:
    return Path(getattr(sku, "dir", sku))


def _sku_name(sku) -> str:
    return str(getattr(sku, "name", _sku_dir(sku).name))


def _has_mask_file(sku_dir: Path, stem: str) -> bool:
    return bool(list(sku_dir.glob(f"{stem}_eyes*.png")))


def _eye_rows(item: dict) -> list[dict]:
    rows = []
    for k, e in enumerate(item.get("eyes") or []):
        if not isinstance(e, dict):
            continue
        rows.append({"index": k,
                     "cx": float(e.get("cx", 0.0)),
                     "cy": float(e.get("cy", 0.0)),
                     "rx": float(e.get("rx", 0.0)),
                     "ry": float(e.get("ry", 0.0))})
    return rows


def propose(sku, manifest: dict) -> list[Candidate]:
    """只读：为「有 eyes、没有任何 mask」的素材各出一个候选。"""
    sku_dir, sku_name = _sku_dir(sku), _sku_name(sku)
    out: list[Candidate] = []
    for i, item in enumerate(manifest.get("items") or []):
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "")
        rows = _eye_rows(item)
        if not stem or not rows:
            continue
        if item.get("eyes_mask") or _has_mask_file(sku_dir, stem):
            continue                      # 已有 mask：本补全器不碰（C-U11）
        out.append(Candidate(
            id=f"{COMPLETER_ID}:{stem}",
            kind=_KIND,
            payload={
                "sku": sku_name,
                "sku_dir": str(sku_dir),
                "item_index": i,
                "stem": stem,
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "eyes": rows,
                # 默认一个都不选：必须逐只点清真眼，禁止按「前 N 只」批量
                "selected": [],
            },
            note=("椭圆 socket 为下限实现；请逐只点清真眼（Issue #1），"
                  "不要按「前 N 只」批量"),
        ))
    return out


def _ellipse_mask(shape, eye: dict) -> np.ndarray:
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    center = (int(round(eye["cx"])), int(round(eye["cy"])))
    axes = (max(1, int(round(eye["rx"])) + 1), max(1, int(round(eye["ry"])) + 1))
    cv2.ellipse(m, center, axes, 0, 0, 360, 255, -1)
    return m


def _overlay(payload: dict) -> np.ndarray:
    h, w = int(payload.get("height") or 0), int(payload.get("width") or 0)
    canvas = np.zeros((h, w, 4), np.uint8)
    selected = set(payload.get("selected") or [])
    for eye in payload.get("eyes") or []:
        color = (90, 200, 120, 255) if eye["index"] in selected \
            else (40, 150, 255, 210)
        thick = 2 if eye["index"] in selected else 1
        cv2.ellipse(canvas, (int(round(eye["cx"])), int(round(eye["cy"]))),
                    (max(1, int(round(eye["rx"])) + 1),
                     max(1, int(round(eye["ry"])) + 1)),
                    0, 0, 360, color, thick)
        cv2.putText(canvas, str(eye["index"]),
                    (int(round(eye["cx"])) - 3, int(round(eye["cy"])) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1, cv2.LINE_AA)
    return canvas


def preview(cand: Candidate, engine=None) -> OverlayImage:
    """并排预览：原图铺白底 + 椭圆候选（绿=已选，橙=未选）。"""
    payload = cand.payload
    base = load_base_rgb(payload["sku_dir"], payload["stem"])
    return OverlayImage(base=base, overlay=_overlay(payload), note=cand.note)


def _pair_name(stem: str, pair_index: int) -> str:
    return f"{stem}_eyes.png" if pair_index == 0 else \
        f"{stem}_eyes_{pair_index + 1}.png"


def commit(cand: Candidate) -> ChangeSet:
    """只返回 PNG 字节 + parts[].mask patch；不写盘。"""
    p = cand.payload
    sku, stem = p["sku"], p["stem"]
    selected = sorted({int(k) for k in (p.get("selected") or [])
                       if any(e["index"] == int(k) for e in p["eyes"])})
    if not selected:
        return ChangeSet(sku=sku, completer=COMPLETER_ID, candidate_id=cand.id,
                         notes="未点选任何眼睛：不产生覆盖层与 manifest patch")

    width, height = int(p["width"]), int(p["height"])
    slots = (max(selected) // 2 + 1) * 2
    by_index = {e["index"]: e for e in p["eyes"]}
    files: dict[str, bytes] = {}
    patch: dict[str, object] = {}
    for pair in range(slots // 2):
        canvas = np.zeros((height, width, 3), np.uint8)
        for ch in (0, 1):                      # R=眼1、G=眼2，B 恒为高光
            idx = pair * 2 + ch
            if idx in selected:
                canvas[..., 2 - ch] = _ellipse_mask((height, width),
                                                    by_index[idx])
        name = _pair_name(stem, pair)
        files[name] = encode_png(canvas)
        for ch in (0, 1):
            idx = pair * 2 + ch
            if idx in selected:
                patch[f"items/{p['item_index']}/parts/{idx}/mask"] = name

    return ChangeSet(sku=sku, completer=COMPLETER_ID, candidate_id=cand.id,
                     files=files, manifest_patch=patch,
                     notes=f"椭圆 socket：选中 {len(selected)} 只眼（下标 "
                           f"{selected}）",
                     meta={"stem": stem, "selected": selected})


def verify(changeset: ChangeSet, sku_dir=None, manifest_after=None) -> Report:
    """逐比特校验：路径守卫、manifest 校验、选中通道非空。"""
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

    if manifest_after is not None:
        from manifest_io import validate_manifest
        good, msg = validate_manifest(manifest_after)
        ok = ok and good
        lines.append(f"validate_manifest：{msg}")

    selected = changeset.meta.get("selected") or []
    for name, data in changeset.files.items():
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            ok = False
            lines.append(f"{name}: PNG 解码失败")
            continue
        for idx in selected:
            if idx // 2 >= img.shape[2]:
                continue
            ch = 2 - (idx % 2)               # R/G 在 BGR 里的位置
            if not (img[..., ch] > 127).any():
                ok = False
                lines.append(f"{name}: 眼 {idx} 的通道为空")
    if selected:
        lines.append(f"选中通道非空：{selected}")
    return Report(ok, lines)
