#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切割质量只读打分：白边 / 阴影 / 软边 / 剪影毛刺。

只读输入，不写素材、不改 mask、不覆盖人工选择；批量模式只产报告。
需要素材自带 alpha（主界面手动切割的产物即可）；没有 alpha 的图片
在报告里标 ``missing_alpha``，不做任何猜测。
零 Qt；只依赖 numpy / cv2。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
_K = np.ones((3, 3), np.uint8)


def _ratio(mask: np.ndarray, total: np.ndarray) -> float:
    n = int(total.sum())
    return float((mask & total).sum()) / n if n else 0.0


def score_cut(rgb: np.ndarray, alpha: np.ndarray) -> dict:
    """对一张已抠好的图打分；alpha 可是 0/1 或 0~1 浮点。"""
    a = np.asarray(alpha, np.float32)
    if a.ndim == 3:
        a = a[..., 0]
    if float(a.max(initial=0.0)) > 1.5:
        a = a / 255.0
    a = np.clip(a, 0.0, 1.0)
    rgb = np.asarray(rgb)[..., :3]
    h, w = a.shape[:2]
    notes: list[str] = []
    if h == 0 or w == 0 or float(a.max()) <= 0.0:
        return {"coverage": 0.0, "white_halo": 0.0, "shadow": 0.0,
                "soft_ratio": 0.0, "jagged": 0.0, "score": 0.0,
                "level": "bad", "notes": ["空图或全透明"]}

    solid = a > 0.5
    coverage = float(solid.mean())
    inner_edge = solid & ~(cv2.erode(solid.astype(np.uint8), _K,
                                     iterations=1).astype(bool))
    outside = cv2.dilate(solid.astype(np.uint8), _K,
                         iterations=2).astype(bool) & ~solid

    bright = rgb.min(axis=2) > 235
    dark = rgb.max(axis=2) < 80
    white_halo = _ratio(bright, inner_edge)
    shadow = _ratio(dark, outside)
    soft_ratio = float(((a > 0.02) & (a < 0.98)).mean())

    contours, _ = cv2.findContours(solid.astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    area = float(solid.sum())
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    jagged = perimeter / max(1.0, float(np.sqrt(area)))

    if white_halo > 0.25:
        notes.append("边缘疑似残留白边")
    if shadow > 0.25:
        notes.append("轮廓外侧疑似阴影未清干净")
    if soft_ratio > 0.35:
        notes.append("半透明过渡过宽，剪影会发虚")
    if coverage < 0.02:
        notes.append("主体覆盖率过低，可能只框到局部")
    elif coverage > 0.98:
        notes.append("几乎铺满整图，可能把背景也算进前景")
    if jagged > 14.0:
        notes.append("剪影边缘毛刺偏多")

    penalty = (60.0 * min(1.0, white_halo)
               + 50.0 * min(1.0, shadow)
               + 20.0 * min(1.0, soft_ratio)
               + 10.0 * min(1.0, max(0.0, jagged - 10.0) / 10.0))
    if coverage < 0.02 or coverage > 0.98:
        penalty += 20.0
    score = round(max(0.0, min(100.0, 100.0 - penalty)), 1)
    level = "good" if score >= 80 else ("warn" if score >= 55 else "bad")
    return {"coverage": round(coverage, 4),
            "white_halo": round(white_halo, 4),
            "shadow": round(shadow, 4),
            "soft_ratio": round(soft_ratio, 4),
            "jagged": round(jagged, 2),
            "score": score, "level": level, "notes": notes}


def score_file(path) -> dict:
    """读单个文件打分；没有 alpha 或读不了时只报原因，不猜掩膜。"""
    p = Path(path)
    img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if img is None:
        return {"name": p.name, "error": "unreadable"}
    if img.ndim != 3 or img.shape[2] != 4:
        return {"name": p.name, "error": "missing_alpha"}
    rgb = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2RGB)
    out = score_cut(rgb, img[..., 3])
    out["name"] = p.name
    return out


def batch_report(folder, recursive: bool = False) -> dict:
    """批量只读报告：不写任何素材，只返回结构化结果。"""
    root = Path(folder)
    if not root.is_dir():
        raise ValueError(f"不是文件夹：{root}")
    it = root.rglob("*") if recursive else root.iterdir()
    files = sorted((p for p in it
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                    and not p.stem.endswith("_mask")),
                   key=lambda p: str(p.relative_to(root)))
    items = [score_file(p) for p in files]
    scores = [i["score"] for i in items if "score" in i]
    summary = {
        "count": len(items),
        "scored": len(scores),
        "mean": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "bad": sum(1 for i in items if i.get("level") == "bad"),
        "warn": sum(1 for i in items if i.get("level") == "warn"),
        "good": sum(1 for i in items if i.get("level") == "good"),
        "errors": sum(1 for i in items if "error" in i),
    }
    return {"folder": str(root), "recursive": bool(recursive),
            "items": items, "summary": summary}


def format_report(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
