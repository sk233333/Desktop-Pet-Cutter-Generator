"""mask.domain_hint：只把「哪只可能是真眼」摆给人看，不写盘、不拍板。

3b3d 的反例是「面积最大的两只恰好是脚下挂件」——任何启发式都不得替人
决定哪只是真眼。本补全器只输出候选框与理由，提交永远为空。
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from completers.base import (Candidate, ChangeSet, OverlayImage, Report,
                             load_base_rgb)

COMPLETER_ID = "mask.domain_hint"
_KIND = "hint"


def _sku_dir(sku) -> Path:
    return Path(getattr(sku, "dir", sku))


def _sku_name(sku) -> str:
    return str(getattr(sku, "name", _sku_dir(sku).name))


def _rows(item: dict) -> list[dict]:
    eyes = [e for e in (item.get("eyes") or []) if isinstance(e, dict)]
    if not eyes:
        return []
    cys = np.array([float(e.get("cy", 0.0)) for e in eyes])
    rys = np.array([max(0.5, float(e.get("ry", 0.0))) for e in eyes])
    med_cy, med_ry = float(np.median(cys)), float(np.median(rys))
    rows = []
    for k, e in enumerate(eyes):
        ry = max(0.5, float(e.get("ry", 0.0)))
        gap = abs(float(e.get("cy", 0.0)) - med_cy)
        # 越靠近眼睛簇的垂直中心、越接近中位大小，越像「真眼」
        score = max(0.0, 1.0 - gap / (3.0 * med_ry))
        note = "与眼睛簇同层" if gap <= 2.0 * med_ry else f"y 偏离 {gap:.0f}px"
        rows.append({"index": k,
                     "cx": float(e.get("cx", 0.0)),
                     "cy": float(e.get("cy", 0.0)),
                     "rx": float(e.get("rx", 0.0)),
                     "ry": ry,
                     "score": round(float(score), 3),
                     "note": note})
    return rows


def propose(sku, manifest: dict) -> list[Candidate]:
    """只读：每个有 eyes 的素材出一个提示候选（不可提交）。"""
    sku_dir, sku_name = _sku_dir(sku), _sku_name(sku)
    out: list[Candidate] = []
    for i, item in enumerate(manifest.get("items") or []):
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "")
        rows = _rows(item)
        if not stem or not rows:
            continue
        out.append(Candidate(
            id=f"{COMPLETER_ID}:{stem}",
            kind=_KIND,
            payload={"sku": sku_name, "sku_dir": str(sku_dir),
                     "item_index": i, "stem": stem,
                     "width": int(item.get("width") or 0),
                     "height": int(item.get("height") or 0),
                     "rows": rows},
            note="只读提示：不写盘、不替人判定真眼；请到眼睛标注里人工点选",
            commit_allowed=False,
        ))
    if out:
        n_eye = sum(len(c.payload["rows"]) for c in out)
        print(f"[domain_hint] {len(out)} 个素材 / {n_eye} 只候选眼；"
              f"仅提示，不写盘，请人工点选", file=sys.stderr)
    return out


def _overlay(payload: dict) -> np.ndarray:
    canvas = np.zeros((int(payload["height"]), int(payload["width"]), 4),
                      np.uint8)
    for row in payload.get("rows") or []:
        hot = row.get("score", 0.0) >= 0.5
        color = (90, 200, 120, 255) if hot else (40, 150, 255, 210)
        x0 = int(round(row["cx"] - row["rx"] - 2))
        y0 = int(round(row["cy"] - row["ry"] - 2))
        x1 = int(round(row["cx"] + row["rx"] + 2))
        y1 = int(round(row["cy"] + row["ry"] + 2))
        cv2.rectangle(canvas, (x0, y0), (x1, y1), color,
                      2 if hot else 1)
        cv2.putText(canvas, f"{row['index']}:{row['score']:.2f}",
                    (x0, max(9, y0 - 3)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.3, color, 1, cv2.LINE_AA)
    return canvas


def preview(cand: Candidate, engine=None) -> OverlayImage:
    payload = cand.payload
    base = load_base_rgb(payload["sku_dir"], payload["stem"])
    return OverlayImage(base=base, overlay=_overlay(payload), note=cand.note)


def commit(cand: Candidate) -> ChangeSet:
    return ChangeSet(sku=cand.payload["sku"], completer=COMPLETER_ID,
                     candidate_id=cand.id,
                     notes="mask.domain_hint 只出提示：不产生任何文件与 patch",
                     meta={"readonly": True})


def verify(changeset: ChangeSet, sku_dir=None, manifest_after=None) -> Report:
    if changeset.files or changeset.manifest_patch:
        return Report(False, ["只读补全器不应产生落盘产物"])
    return Report(True, ["只读提示：无落盘产物"])
