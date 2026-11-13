#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把白底抠图转成 2D 桌宠素材包。

输入（desktop_pet/source/<sku>/）：
    <stem>.png          白底原图
    <stem>_mask.png     物体掩膜
    <stem>.json         可选，含 mm_per_px_canon

输出（desktop_pet/assets/<sku>/）：
    <stem>.png          带 alpha 的透明 PNG（已裁边、缩放到 --size）
    <stem>.webp         同等内容的无损 WebP（体积更小，QMovie 可直接播）
    <stem>_preview.png  棋盘格合成预览（仅 --preview 时生成）
    manifest.json       尺寸、锚点、覆盖率、mm/px、建议显示高度

为什么不能直接用 mask 当 alpha：
    白底拍摄时边缘像素混了白色，直接把 mask 贴到深色桌面上会出现一圈白边。
    所以要 (1) 估 alpha（优先用 pymatting，缺依赖时用腐蚀/膨胀带 + 高斯羽化），
    (2) 按 C = a·F + (1-a)·白 反解前景色 F，把白色从边缘里扣掉。

用法：
    python3 desktop_pet/cutout_to_pet.py --sku bgy_xia
    python3 desktop_pet/cutout_to_pet.py --sku bgy --size 512 --preview
    python3 desktop_pet/cutout_to_pet.py --sku bgy_xia --stem "Screenshot from 2026-10-22 11-49-47_white"
    python3 desktop_pet/cutout_to_pet.py --sku bgy_xia --no-matting   # 强制用回退路径

可选依赖（装了边缘更干净）：
    pip install pymatting
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_CANON = Path(__file__).resolve().parent / "source"
DEFAULT_OUT = Path(__file__).resolve().parent / "assets"


def load_mask(path: Path, shape) -> np.ndarray:
    """读掩膜；没有掩膜文件时退回“非白像素”估计。"""
    if path.exists():
        m = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if m is not None and m.shape[:2] == shape[:2]:
            return m > 127
    print(f"  [warn] 缺少或尺寸不符的 mask：{path.name}，用非白像素估计")
    bgr = cv2.imread(str(path.with_name(path.name.replace("_mask", ""))))
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return gray < 245


def keep_main_parts(mask: np.ndarray, min_ratio: float) -> np.ndarray:
    """去掉掩膜里的灰尘/小碎片，保留主体与足够大的配件。"""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + np.where(areas >= max(32, int(areas.max() * min_ratio)))[0]
    return np.isin(labels, keep)


def matting_alpha(rgb: np.ndarray, mask: np.ndarray, edge_px: int):
    """优先用 pymatting 做颜色 matting，失败则返回 None。"""
    try:
        from pymatting import estimate_alpha_cf, estimate_foreground_ml
    except ImportError:
        return None, None

    k = np.ones((2 * edge_px + 1, 2 * edge_px + 1), np.uint8)
    m = mask.astype(np.uint8)
    fg = cv2.erode(m, k)
    bg = 1 - cv2.dilate(m, k)
    tri = np.full(mask.shape, 0.5, np.float32)
    tri[fg > 0] = 1.0
    tri[bg > 0] = 0.0

    try:
        alpha = estimate_alpha_cf(rgb.astype(np.float64) / 255.0, tri)
        fg_rgb = estimate_foreground_ml(rgb.astype(np.float64) / 255.0, alpha)
        return np.clip(alpha, 0, 1).astype(np.float32), np.clip(fg_rgb, 0, 1).astype(np.float32) * 255.0
    except Exception as exc:  # pymatting 对退化输入较敏感
        print(f"  [warn] pymatting 失败（{exc}），改用回退路径")
        return None, None


def fallback_alpha(rgb: np.ndarray, mask: np.ndarray, edge_px: int):
    """回退路径：羽化边缘 + 按白底反解前景色（去白边/溢色）。"""
    sigma = max(0.6, edge_px / 2.0)
    a = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), sigma)
    # 掩膜内部必须保持不透明，避免把主体边缘削薄
    a = np.maximum(a, cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(np.float32))
    a[a < 0.02] = 0.0

    # C = a·F + (1-a)·255  =>  F = (C - (1-a)·255) / a
    safe = np.maximum(a, 1e-3)[..., None]
    fg = (rgb.astype(np.float32) - (1.0 - a[..., None]) * 255.0) / safe
    fg = np.clip(fg, 0, 255)
    fg[a <= 0.05] = 0.0
    return a, fg


def sharpen_cut_edges(rgb: np.ndarray, alpha: np.ndarray, level: int):
    """切割边界锐化：只作用于轮廓内外几像素的窄带，画面内部一个像素都不碰。

    level 0~3，和普通「整图锐化」是两件事，目标是让剪影更实、更饱满：
      1. 窄带内提锐：轮廓线的颜色不发虚、不发灰，边界一下子立起来；
      2. alpha 过渡压窄（S 曲线）：半透明的灰边收窄，剪影边缘利落；
      3. 2 档起把轮廓外扩 1px：补回抠图常吃掉的发丝和尖角，
         只吃「颜色与主体接近」的像素，避免把背景色一起圈进来。
    返回 (rgb, alpha)，alpha 为 0~1 浮点。
    """
    level = max(0, min(3, int(level)))
    a = np.asarray(alpha, np.float32)
    if a.ndim == 3:
        a = a[..., 0]
    if float(a.max()) > 1.5:          # 传进来的是 0~255 就归一化
        a = a / 255.0
    a = np.clip(a, 0.0, 1.0)
    if level == 0 or float(a.max()) <= 0.02:
        return rgb, a

    src = rgb.astype(np.float32)
    r = 1 + level
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    solid = (a > 0.5).astype(np.uint8)
    band = (cv2.dilate(solid, k).astype(np.float32)
            - cv2.erode(solid, k).astype(np.float32))
    band = cv2.GaussianBlur(np.clip(band, 0.0, 1.0), (0, 0), 1.2)
    # 只锐化「已经基本不透明」的那侧边缘：透明区的颜色是反解出来的，
    # 在那上面提锐会把去背景的残留放大成黑边
    ramp = np.clip((a - 0.2) / 0.3, 0.0, 1.0)
    w = band * ramp
    w[w < 0.01] = 0.0        # 窄带外的权重直接归零，内部像素逐比特不动
    w = w[..., None]

    blur = cv2.GaussianBlur(src, (0, 0), 0.8 + 0.3 * level)
    sharp = np.clip(src + (0.35 + 0.18 * level) * (src - blur), 0, 255)
    out = src * (1.0 - w) + sharp * w

    # alpha 过渡压窄：灰色毛边收窄，剪影更实
    a2 = np.clip((a - 0.5) * (1.0 + 0.45 * level) + 0.5, 0.0, 1.0)

    # 附近的主体色（只取基本不透明的那部分做参考，避免把背景色算进来）
    core = a2 > 0.8
    if core.any():
        tag = core.astype(np.float32)
        num = cv2.GaussianBlur(src * tag[..., None], (0, 0), 2.0)
        den = cv2.GaussianBlur(tag, (0, 0), 2.0)[..., None]
        near = num / np.maximum(den, 1e-3)
        # 边缘「去溢色」：半透明的轮廓上混着背景色，看着发白、发灰，
        # 往主体色收一点，剪影立刻显得实、显得饱满（这一步不改变形状）
        rim = (band * (1.0 - ramp) * (0.25 + 0.08 * level))[..., None]
        out = out * (1.0 - rim) + near * rim

        if level >= 3:
            # 补边：向外扩 1px，只收和主体颜色接近的像素（差异大的多是背景或
            # 发丝间的空隙，硬补会把它们糊成一片）
            grow = cv2.dilate(core.astype(np.uint8),
                              np.ones((3, 3), np.uint8)).astype(bool)
            ring = grow & ~(a2 > 0.35)       # 只取轮廓外那一圈，内部不参与
            diff = np.abs(src - near).max(axis=2)
            keep = ring & (diff < 32.0)
            if keep.any():
                out[keep] = near[keep]   # 补回来的这一圈用主体色，不带背景色
                a2 = np.maximum(a2, keep.astype(np.float32) * 0.6)
    # 压窄过渡后补一点点抗锯齿，避免剪影出现台阶状硬边
    a2 = cv2.GaussianBlur(a2, (0, 0), 0.5 + 0.1 * level)
    return np.clip(out, 0, 255).astype(np.float32), a2


def premultiplied_resize(rgb: np.ndarray, alpha: np.ndarray, size: int):
    """在预乘空间缩放，避免透明边缘被背景色污染。"""
    h, w = alpha.shape
    if size <= 0 or max(h, w) <= size:
        return rgb, alpha, 1.0
    scale = size / float(max(h, w))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    pm = np.dstack([rgb * alpha[..., None], alpha])
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
    pm = cv2.resize(pm, (nw, nh), interpolation=interp)
    a2 = np.clip(pm[..., 3], 0.0, 1.0)
    rgb2 = np.divide(pm[..., :3], np.maximum(pm[..., 3:4], 1e-3))
    rgb2 = np.clip(rgb2, 0, 255)
    return rgb2, a2, scale


def trim_to_content(rgb: np.ndarray, alpha: np.ndarray, margin: int):
    ys, xs = np.where(alpha > 0.05)
    if len(xs) == 0:
        return rgb, alpha, (0, 0, alpha.shape[1], alpha.shape[0])
    x0, x1 = max(0, xs.min() - margin), min(alpha.shape[1], xs.max() + margin + 1)
    y0, y1 = max(0, ys.min() - margin), min(alpha.shape[0], ys.max() + margin + 1)
    return rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1], (int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def checkerboard_preview(rgb: np.ndarray, alpha: np.ndarray, cell: int = 16) -> np.ndarray:
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 235, 190).astype(np.float32)
    bg = np.dstack([bg] * 3)
    return np.clip(rgb * alpha[..., None] + bg * (1.0 - alpha[..., None]), 0, 255).astype(np.uint8)


def load_eyes_sidecar(img_path: Path, shape) -> list[dict] | None:
    """读同目录的 <stem>_eyes.json（image_assist.py 框选产出，坐标基于源图）。"""
    p = img_path.with_name(f"{img_path.stem}_eyes.json")
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"  [warn] {p.name} 解析失败，忽略眼睛标注")
        return None
    sw, sh = data.get("image_size", [shape[1], shape[0]])
    if (int(sw), int(sh)) != (shape[1], shape[0]):
        print(f"  [warn] {p.name} 尺寸 {sw}×{sh} 与源图 {shape[1]}×{shape[0]} 不符，忽略")
        return None
    eyes = data.get("eyes")
    # 返回 [] 表示“明确标注过、但一只眼都不框”（不眨眼）；None 才回退自动检测
    return eyes if isinstance(eyes, list) else None


def load_eye_levels(img_path: Path) -> list[str]:
    """读 <stem>_eyes.json 里的 eye_level（逐只眼的处理档位）。

    缺省返回空列表，运行时按眼睛内部结构自动判；写了 L2/L1/L0 就照写死，
    低清放大/锯齿图自动判不稳时可直接手改 json。
    """
    p = img_path.with_name(f"{img_path.stem}_eyes.json")
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    lv = data.get("eye_level")
    return [str(v) for v in lv] if isinstance(lv, list) else []


def load_eye_masks(img_path: Path, shape):
    """读同目录的 <stem>_eyes.png / _eyes_2.png：R/G = 眼眶 mask，B = 高光。

    每个文件装两只眼（眼1/眼2 各占一个通道），多余的眼依次放进 _eyes_2.png …
    尺寸必须和源图一致，否则返回 None（说明标注不是在这张图上做的）。
    """
    paths = []
    first = img_path.with_name(f"{img_path.stem}_eyes.png")
    if first.exists():
        paths.append(first)
    k = 2
    while True:
        p = img_path.with_name(f"{img_path.stem}_eyes_{k}.png")
        if not p.exists():
            break
        paths.append(p)
        k += 1
    if not paths:
        return None
    sockets, hl = [], None
    for p in paths:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)  # BGR
        if im is None or im.shape[:2] != shape[:2]:
            print(f"  [warn] {p.name} 读不到或尺寸与源图不符，忽略眼睛 mask")
            continue
        b, g, r = im[..., 0], im[..., 1], im[..., 2]
        for ch in (r, g):
            m = ch > 127
            if m.any():
                sockets.append(m)
        mh = b > 127
        if mh.any():
            hl = mh if hl is None else (hl | mh)
    if not sockets:
        return None
    return sockets, hl


def boxes_from_eye_masks(sockets: list[np.ndarray]) -> list[dict]:
    """眼眶 mask -> 外接矩形（源图坐标），供 _eyes.json / manifest 向后兼容。"""
    boxes = []
    for m in sockets:
        ys, xs = np.where(m)
        if len(xs) == 0:
            continue
        boxes.append({"x1": int(xs.min()), "y1": int(ys.min()),
                      "x2": int(xs.max()) + 1, "y2": int(ys.max()) + 1})
    return boxes


def map_eye_masks(sockets: list[np.ndarray], highlight: np.ndarray | None,
                  crop, size) -> tuple[list[np.ndarray], np.ndarray | None]:
    """源图坐标的 mask -> 素材像素坐标（裁边 + 缩放），最近邻保持边界锐利。"""
    x0, y0, cw, ch = crop
    nw, nh = int(size[0]), int(size[1])
    out = []
    for m in sockets:
        mm = m[y0:y0 + ch, x0:x0 + cw].astype(np.uint8)
        if mm.shape[:2] != (ch, cw):
            continue
        out.append(cv2.resize(mm * 255, (nw, nh),
                              interpolation=cv2.INTER_NEAREST) > 127)
    hm = (
        cv2.resize(highlight[y0:y0 + ch, x0:x0 + cw].astype(np.uint8) * 255,
                   (nw, nh), interpolation=cv2.INTER_NEAREST) > 127
        if highlight is not None else None)
    return out, hm


def eye_highlight_mask(rgb: np.ndarray, sockets: list[np.ndarray]) -> np.ndarray:
    """眶内「低饱和 + 够亮」的小块：写进 <stem>_eyes.png 的 B 通道。

    和运行时用的是同一判据，所以贴回的高光位置一致；占眶面积太大的亮区
    （淡色虹膜、金色角饰）不算高光。
    """
    hsv = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    sat, val = hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    hl = np.zeros(rgb.shape[:2], bool)
    for m in sockets:
        cand = (m & (val > 0.45) & (sat < 0.45)).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(cand, 8)
        if n <= 1:
            continue
        j = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        area = int(st[j, cv2.CC_STAT_AREA])
        if 3 <= area <= 0.55 * float(max(1, m.sum())):
            hl |= lab == j
    return hl


def save_eye_masks(out_dir: Path, stem: str, sockets: list[np.ndarray],
                   highlight: np.ndarray | None = None) -> list[str]:
    """眼眶 mask -> <stem>_eyes.png（R=眼1、G=眼2、B=高光），超过两只另起文件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    names = []
    h, w = sockets[0].shape[:2]
    for i in range(0, len(sockets), 2):
        canvas = np.zeros((h, w, 3), np.uint8)
        canvas[..., 2] = sockets[i].astype(np.uint8) * 255      # cv2 的 R
        if i + 1 < len(sockets):
            canvas[..., 1] = sockets[i + 1].astype(np.uint8) * 255
        if highlight is not None:
            canvas[..., 0] = highlight.astype(np.uint8) * 255
        name = f"{stem}_eyes.png" if i == 0 else f"{stem}_eyes_{i // 2 + 1}.png"
        cv2.imwrite(str(out_dir / name), canvas)
        names.append(name)
    return names


def load_hair_sidecar(img_path: Path, shape, kind: str = "hair") -> list[dict] | None:
    """读同目录的 <stem>_<kind>.json + <stem>_<kind>1.png…（多边形飘动区域标注）。

    kind="hair" 是发缕，"cloth" 是衣服 / 小附件的飘动区域，格式完全一致。
    """
    label = "头发" if kind == "hair" else "衣服"
    p = img_path.with_name(f"{img_path.stem}_{kind}.json")
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"  [warn] {p.name} 解析失败，忽略{label}标注")
        return None
    sw, sh = data.get("image_size", [shape[1], shape[0]])
    if (int(sw), int(sh)) != (shape[1], shape[0]):
        print(f"  [warn] {p.name} 尺寸 {sw}×{sh} 与源图 {shape[1]}×{shape[0]} 不符，忽略")
        return None
    wisps = data.get("wisps")
    if not isinstance(wisps, list):
        return None
    out = []
    for it in wisps:
        try:
            m = cv2.imread(str(img_path.with_name(str(it["mask"]))),
                           cv2.IMREAD_GRAYSCALE)
            if m is None or m.shape[:2] != shape[:2]:
                print(f"  [warn] {it.get('mask')} 读不到或尺寸与源图不符，忽略这块{label}")
                continue
            out.append({"mask": m > 127,
                        "pivot": [float(it["pivot"][0]), float(it["pivot"][1])],
                        "tip": [float(it["tip"][0]), float(it["tip"][1])]})
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out or None


def load_blink_sidecar(img_path: Path) -> dict | None:
    """读同目录的 <stem>_blink.json（眨眼时间轴，可直接给桌宠用）。"""
    p = img_path.with_name(f"{img_path.stem}_blink.json")
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"  [warn] {p.name} 解析失败，忽略眨眼时间轴")
        return None
    plan = data.get("blink") if isinstance(data.get("blink"), dict) else data
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        return None
    return plan


def map_hair_wisps(wisps: list[dict], crop, size, out_dir: Path,
                   stem: str, kind: str = "hair") -> list[dict] | None:
    """发缕 / 衣物 mask 随裁边/缩放搬到素材分辨率，最近邻保持边界；pivot/tip 同步换算。"""
    x0, y0, cw, ch = crop
    nw, nh = int(size[0]), int(size[1])
    sx = nw / float(max(1, cw))
    sy = nh / float(max(1, ch))
    out = []
    for i, w in enumerate(wisps, 1):
        mm = w["mask"][y0:y0 + ch, x0:x0 + cw].astype(np.uint8)
        if mm.shape != (ch, cw):
            continue
        m = cv2.resize(mm * 255, (nw, nh), interpolation=cv2.INTER_NEAREST) > 127
        if not m.any():
            continue
        name = f"{stem}_{kind}{i}.png"
        cv2.imwrite(str(out_dir / name), m.astype(np.uint8) * 255)
        out.append({"mask": name,
                    "pivot": [round((w["pivot"][0] - x0) * sx, 1),
                              round((w["pivot"][1] - y0) * sy, 1)],
                    "tip": [round((w["tip"][0] - x0) * sx, 1),
                            round((w["tip"][1] - y0) * sy, 1)]})
    if not out:
        return None
    keep = {it["mask"] for it in out}
    for old in out_dir.glob(f"{stem}_{kind}*.png"):
        if old.name not in keep:
            old.unlink()
    return out


def map_eyes(boxes: list[dict], crop, scale: float,
             rgb: np.ndarray) -> list[dict]:
    """源图坐标 -> 素材像素坐标（裁边 + 缩放）；顺带取眼线颜色。"""
    x0, y0 = crop[0], crop[1]
    h_src, w_src = rgb.shape[:2]
    out = []
    for b in boxes:
        try:
            sx1, sx2 = sorted((float(b["x1"]), float(b["x2"])))
            sy1, sy2 = sorted((float(b["y1"]), float(b["y2"])))
        except (KeyError, TypeError, ValueError):
            continue
        sx1, sx2 = max(0, sx1), min(w_src - 1, sx2)
        sy1, sy2 = max(0, sy1), min(h_src - 1, sy2)
        if sx2 <= sx1 or sy2 <= sy1:
            continue

        cx = ((sx1 + sx2) / 2.0 - x0) * scale
        cy = ((sy1 + sy2) / 2.0 - y0) * scale
        rx = max(3.0, (sx2 - sx1) / 2.0 * scale)
        ry = max(3.0, (sy2 - sy1) / 2.0 * scale)

        region = rgb[int(sy1):int(sy2) + 1, int(sx1):int(sx2) + 1].astype(np.uint8)
        gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        vals = region.reshape(-1, 3)
        gl = gray.reshape(-1)
        sel = vals[gl <= np.percentile(gl, 25)] if len(vals) else np.zeros((0, 3))
        dark = [int(v) for v in np.median(sel, axis=0)] if len(sel) else [30, 30, 30]

        out.append({"cx": round(cx, 1), "cy": round(cy, 1),
                    "rx": round(rx, 1), "ry": round(ry, 1), "dark": dark})
    return out


def build_one(img_path: Path, mask_path: Path, meta_path: Path, out_dir: Path,
              size: int, edge_px: int, margin: int, use_matting: bool, preview: bool,
              edge: int = 0):
    bgr = cv2.imread(str(img_path))  # BGR
    if bgr is None:
        raise RuntimeError(f"读不到图：{img_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    mask = keep_main_parts(load_mask(mask_path, bgr.shape), 0.002)
    if not mask.any():
        raise RuntimeError(f"空掩膜：{mask_path}")

    a, fg = (None, None)
    mode = "fallback"
    if use_matting:
        a, fg = matting_alpha(rgb, mask, edge_px)
        if a is not None:
            mode = "pymatting"
    if a is None:
        a, fg = fallback_alpha(rgb, mask, edge_px)

    # 主体内部强制不透明，避免 matting 在纹理处把 alpha 压低
    inner = cv2.erode(mask.astype(np.uint8), np.ones((2 * edge_px + 1,) * 2, np.uint8))
    a = np.maximum(a, (inner > 0).astype(np.float32))

    # 切割边界锐化（剪切预览里「边缘 −/+」选的档位）：只收紧轮廓、提亮边色，
    # 主体内部不动；放在 matting 之后，预览和最终素材才是同一套观感
    if edge > 0:
        fg, a = sharpen_cut_edges(fg, a, edge)

    fg, a, crop = trim_to_content(fg, a, margin)
    fg, a, scale = premultiplied_resize(fg, a, size)

    # 眼眶 mask（<stem>_eyes.png）随同样的裁边/缩放搬到素材分辨率
    eye_mask_data = load_eye_masks(img_path, bgr.shape)
    mask_boxes = None
    eye_mask_files = []
    domains = None
    if eye_mask_data is not None:
        sockets, hl = eye_mask_data
        mask_boxes = boxes_from_eye_masks(sockets)
        mapped, mapped_hl = map_eye_masks(sockets, hl, crop,
                                          (a.shape[1], a.shape[0]))
        if mapped:
            # 每只眼眶应只有 1 个连通域；多出来的是配饰上的装饰眼（误标），
            # 在表情贴片里会被当成第三只眼，必须写进 report 提醒重标。
            domains = [int(cv2.connectedComponentsWithStats(
                m.astype(np.uint8), 8)[0] - 1) for m in mapped]
            if any(d != 1 for d in domains):
                print(f"[warn] {img_path.stem}: 眼眶连通域 {domains}"
                      f"（每只眼应为 1，多出的多为配饰误标）")
            eye_mask_files = save_eye_masks(out_dir, img_path.stem,
                                            mapped, mapped_hl)

    eyes_boxes = load_eyes_sidecar(img_path, bgr.shape)
    if eyes_boxes is None and mask_boxes:
        # 只有 mask、没有 _eyes.json：用 mask 的外接矩形补一份，保持向后兼容
        eyes_boxes = mask_boxes
    eyes = map_eyes(eyes_boxes, crop, scale, rgb) if eyes_boxes else []
    # 逐只眼档位与 eyes 同序；缺省/多余都补 auto，运行时再按内部结构判
    eye_levels = load_eye_levels(img_path) if eyes_boxes is not None else []
    if eyes:
        eye_levels = (eye_levels + ["auto"] * len(eyes))[:len(eyes)]
    else:
        eye_levels = []

    hair_data = load_hair_sidecar(img_path, bgr.shape)
    hair = (map_hair_wisps(hair_data, crop, (a.shape[1], a.shape[0]), out_dir,
                           img_path.stem) if hair_data else None)
    cloth_data = load_hair_sidecar(img_path, bgr.shape, kind="cloth")
    cloth = (map_hair_wisps(cloth_data, crop, (a.shape[1], a.shape[0]), out_dir,
                            img_path.stem, kind="cloth") if cloth_data else None)
    blink = load_blink_sidecar(img_path)

    h, w = a.shape
    ys, xs = np.where(a > 0.05)
    # 锚点：底部中心（桌宠落地/站立用）。y 以左上角为原点，1.0 ≈ 脚底。
    if len(xs):
        foot_y = int(ys.max())
        row = xs[ys >= foot_y - 2]
        anchor = {"x": float(row.mean()) / w, "y": float(foot_y) / h}
    else:
        anchor = {"x": 0.5, "y": 1.0}

    mm_per_px = None
    if meta_path.exists():
        try:
            mm_per_px = json.loads(meta_path.read_text(encoding="utf-8")).get("mm_per_px_canon")
        except json.JSONDecodeError:
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    rgba = np.dstack([fg, a * 255.0]).astype(np.uint8)
    png = out_dir / f"{img_path.stem}.png"
    Image.fromarray(rgba, "RGBA").save(png)

    webp = out_dir / f"{img_path.stem}.webp"
    Image.fromarray(rgba, "RGBA").save(webp, "WEBP", lossless=True, quality=100, method=6)

    if preview:
        prev = checkerboard_preview(fg, a)
        Image.fromarray(prev, "RGB").save(out_dir / f"{img_path.stem}_preview.png")

    return {
        "stem": img_path.stem,
        "width": w,
        "height": h,
        "scale": round(scale, 4),
        "anchor": {k: round(v, 4) for k, v in anchor.items()},
        "coverage": round(float(a.mean()), 4),
        "alpha_mode": mode,
        "mm_per_px_canon": mm_per_px,
        "display_height_px": 256,
        "eyes": eyes if eyes_boxes is not None else None,
        "eyes_source": "manual" if eyes_boxes is not None else None,
        "eye_level": eye_levels or None,
        "eyes_mask": eye_mask_files or None,
        "eye_domains": domains if eye_mask_files else None,
        "hair": hair,
        "cloth": cloth,
        "blink": blink,
        "files": [png.name, webp.name],
    }


def main():
    ap = argparse.ArgumentParser(description="白底抠图 -> 桌宠透明素材包")
    ap.add_argument("--sku", required=True, help="SKU 名，对应 desktop_pet/source/<sku>/")
    ap.add_argument("--canon-dir", type=Path, default=DEFAULT_CANON, help="抠图源根目录")
    ap.add_argument("--out-dir", type=Path, default=None, help="输出目录，默认 desktop_pet/assets/<sku>")
    ap.add_argument("--stem", default=None, help="只处理指定 stem（不带 .png）")
    ap.add_argument("--size", type=int, default=512, help="最长边像素，0=保持原尺寸（默认 512）")
    ap.add_argument("--edge-px", type=int, default=4, help="matting 未知带宽度（默认 4px）")
    ap.add_argument("--margin", type=int, default=8, help="裁边后保留的透明边距（默认 8px）")
    ap.add_argument("--no-matting", action="store_true", help="禁用 pymatting，强制走回退路径")
    ap.add_argument("--preview", action="store_true", help="额外输出棋盘格预览图")
    args = ap.parse_args()

    canon = args.canon_dir / args.sku
    if not canon.is_dir():
        sys.exit(f"源目录不存在：{canon}\n请先把白底图和 <stem>_mask.png 放进 desktop_pet/source/{args.sku}/")

    out_dir = args.out_dir or (DEFAULT_OUT / args.sku)
    stems = sorted(p.name[:-4] for p in canon.glob("*.png")
                   if not p.name.endswith("_mask.png") and not p.name.endswith("_preview.png"))
    if args.stem:
        stems = [s for s in stems if s == args.stem]
    if not stems:
        sys.exit(f"{canon} 下没有匹配的图")

    print(f"SKU={args.sku}  图数={len(stems)}  输出={out_dir}  size={args.size}")
    manifest_path = out_dir / "manifest.json"
    old_items = []
    # 单张重跑时保留已有条目，避免一次 --stem 把整包 manifest 覆盖掉
    if args.stem and manifest_path.exists():
        try:
            old_items = json.loads(manifest_path.read_text(encoding="utf-8")).get("items", [])
        except json.JSONDecodeError:
            old_items = []
    kept = [it for it in old_items if it.get("stem") not in stems]

    manifest = {"schema": "pet-asset/1", "sku": args.sku,
                "generated_at": datetime.now().isoformat(timespec="seconds"), "items": []}
    for i, stem in enumerate(stems, 1):
        try:
            item = build_one(canon / f"{stem}.png", canon / f"{stem}_mask.png",
                             canon / f"{stem}.json", out_dir, args.size, args.edge_px,
                             args.margin, not args.no_matting, args.preview)
            manifest["items"].append(item)
            print(f"[{i}/{len(stems)}] {stem}  {item['width']}×{item['height']}  "
                  f"alpha={item['alpha_mode']}  coverage={item['coverage']}")
        except Exception as exc:
            print(f"[{i}/{len(stems)}] 跳过 {stem}：{exc}")

    manifest["items"].extend(kept)
    manifest["items"].sort(key=lambda it: it.get("stem", ""))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(f"完成：{len(manifest['items'])} 个素材 -> {out_dir}/manifest.json")


if __name__ == "__main__":
    main()
