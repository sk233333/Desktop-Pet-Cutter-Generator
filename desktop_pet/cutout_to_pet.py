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
              size: int, edge_px: int, margin: int, use_matting: bool, preview: bool):
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

    fg, a, crop = trim_to_content(fg, a, margin)
    fg, a, scale = premultiplied_resize(fg, a, size)

    eyes_boxes = load_eyes_sidecar(img_path, bgr.shape)
    eyes = map_eyes(eyes_boxes, crop, scale, rgb) if eyes_boxes else []

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
