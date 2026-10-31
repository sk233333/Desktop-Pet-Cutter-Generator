#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 2D 桌宠动作层（不依赖 Qt）。

提供三件事：
1. Motion：呼吸 / 摇摆 / 弯曲的通用参数与逐帧取值，任何透明图都能用；
2. detect_eyes()：在上半身找成对的暗色椭圆块，定位双眼；
3. make_blink_frames()：用“局部填肤色 + 眼睑弧线”生成半闭/全闭帧。

眨眼是唯一依赖图像内容的动作：检测不到眼睛时返回空帧，窗口侧自动跳过，
呼吸与摇摆不受影响。

单独预览眨眼效果：
    python3 desktop_pet/anim.py --asset "desktop_pet/assets/bgy/bgy_001_white.png" \
        --preview desktop_pet/assets/blink_preview.png
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass
class Motion:
    """通用动作参数；默认值适用于 512px 左右的站立手办。"""

    breath: float = 0.02          # 纵向缩放幅度（2%）
    breath_period: float = 4.2    # 呼吸周期（秒）
    sway_deg: float = 1.2         # 摇摆角度（度）
    sway_period: float = 5.6      # 摇摆周期（秒）
    bend: float = 0.0             # 预留：运行时网格弯曲幅度（0 关闭）
    blink: bool = True
    blink_interval: tuple[float, float] = (3.0, 6.0)

    def at(self, t: float) -> dict:
        """返回 t 时刻的变换参数；角度单位是度，缩放是相对 1.0 的倍数。"""
        return {
            "scale_y": 1.0 + self.breath * math.sin(2 * math.pi * t / self.breath_period),
            "angle": self.sway_deg * math.sin(2 * math.pi * t / self.sway_period),
        }


@dataclass
class Eye:
    cx: float
    cy: float
    rx: float
    ry: float
    score: float = 0.0
    dark: tuple = (30, 30, 30)


def eyes_from_dicts(items: list[dict]) -> list[Eye]:
    """manifest 里的手工标注（像素坐标）-> Eye 列表。"""
    eyes = []
    for it in items:
        try:
            dark = it.get("dark") or (30, 30, 30)
            eyes.append(Eye(cx=float(it["cx"]), cy=float(it["cy"]),
                            rx=max(3.0, float(it["rx"])),
                            ry=max(3.0, float(it["ry"])),
                            score=float(it.get("score", 0.0)),
                            dark=tuple(int(v) for v in dark)))
        except (KeyError, TypeError, ValueError):
            continue
    return eyes


@dataclass
class PetAsset:
    """一张透明素材 + 锚点 + 动作参数。"""

    rgba: np.ndarray
    anchor: tuple[float, float] = (0.5, 0.99)
    motion: Motion = field(default_factory=Motion)
    eyes: list[Eye] = field(default_factory=list)
    source: Path | None = None

    @property
    def height(self) -> int:
        return self.rgba.shape[0]

    @property
    def width(self) -> int:
        return self.rgba.shape[1]

    @classmethod
    def load(cls, path: Path, motion: Motion | None = None,
             eyes: list[dict] | None = None) -> "PetAsset":
        img = Image.open(path).convert("RGBA")
        rgba = np.array(img)
        asset = cls(rgba=rgba, motion=motion or Motion(), source=path)
        # eyes=None：没有人工标注，自动检测；eyes=[]：明确不框眼，不眨眼
        asset.eyes = eyes_from_dicts(eyes) if eyes is not None else detect_eyes(rgba)
        return asset

    def blink_frames(self) -> list[np.ndarray]:
        return make_blink_frames(self.rgba, self.eyes) if self.eyes else []


def detect_eyes(rgba: np.ndarray, upper: float = 0.62) -> list[Eye]:
    """自动找一对眼睛；找不到返回空列表（调用方应跳过眨眼）。

    判据：上半身、暗色块、面积适中、两个块尺寸相近且水平并排。
    阈值从暗到亮依次尝试：灰头发/浅色皮肤在 <120 时会和眼线连成一片，
    所以先用 70 这类“只保留近黑”的阈值，失败再放宽。
    """
    w = rgba.shape[1]
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > 0.05)
    if len(xs) == 0:
        return []
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    roi_y1 = int(y0 + (y1 - y0) * upper)
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    area_all = float((x1 - x0) * (y1 - y0))

    for thr in (70, 85, 100, 115):
        dark = ((gray < thr) & (alpha > 80)).astype(np.uint8)
        dark[:y0, :] = 0
        dark[roi_y1:, :] = 0
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        n, labels, stats, cents = cv2.connectedComponentsWithStats(dark, 8)
        cands = []
        for i in range(1, n):
            _, _, cw, ch, area = stats[i]
            if area < area_all * 0.002 or area > area_all * 0.09:
                continue
            if ch < 4 or cw < 4:
                continue
            aspect = cw / float(ch)
            if not 0.35 <= aspect <= 3.2:
                continue
            if (cents[i][1] - y0) / float(y1 - y0) > upper:
                continue
            cands.append((i, cents[i][0], cents[i][1], cw, ch, area))

        best, best_score = [], 0.0
        for a in range(len(cands)):
            for b in range(a + 1, len(cands)):
                _, ax, ay, aw, _, aa = cands[a]
                _, bx, by, bw, _, ba = cands[b]
                if min(aa, ba) / max(aa, ba) < 0.45:
                    continue
                if abs(ay - by) > (y1 - y0) * 0.14:
                    continue
                sep = abs(ax - bx)
                if sep < w * 0.06 or sep > w * 0.62:
                    continue
                score = (aa + ba) * (1.0 - abs(aw - bw) / max(aw, bw))
                if score > best_score:
                    best_score = score
                    best = [cands[a], cands[b]]
        if not best:
            continue

        eyes = []
        for idx, cx, cy, cw, ch, _ in best:
            mask = (labels == idx) & (gray < 140)
            vals = rgba[..., :3][mask]
            if len(vals):
                gl = gray[mask]
                sel = vals[gl <= np.percentile(gl, 25)]
                dark_col = tuple(int(v) for v in np.median(sel, axis=0))
            else:
                dark_col = (30, 30, 30)
            eyes.append(Eye(cx=float(cx), cy=float(cy),
                            rx=max(3.0, cw / 2.0 * 1.05), ry=max(3.0, ch / 2.0 * 1.05),
                            score=float(best_score), dark=dark_col))
        return eyes
    return []


def _eye_skin_color(rgb: np.ndarray, alpha: np.ndarray, eye: Eye, gray: np.ndarray) -> np.ndarray:
    """从眼睛外圈取肤色：排除暗像素，避免把眼线当成皮肤。"""
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = ((xx - eye.cx) / max(eye.rx, 1e-3)) ** 2 + ((yy - eye.cy) / max(eye.ry, 1e-3)) ** 2
    ring = (dist >= 1.15 ** 2) & (dist <= 1.9 ** 2) & (alpha > 128) & (gray > 100)
    if ring.sum() < 12:
        ring = (dist <= 2.4 ** 2) & (alpha > 128)
    if ring.sum() == 0:
        return np.array([210, 210, 210], np.float32)
    return np.median(rgb[ring].astype(np.float32), axis=0)


def _draw_closed_eye(out_rgb: np.ndarray, alpha: np.ndarray, eye: Eye,
                     cover: float, skin: np.ndarray, closed: bool):
    """用椭圆遮罩把眼睛逐步“盖上”，closed 时再加一条下弯的眼睑线。"""
    h, w = alpha.shape
    x0 = max(0, int(eye.cx - eye.rx * 2.2))
    x1 = min(w, int(eye.cx + eye.rx * 2.2) + 1)
    y0 = max(0, int(eye.cy - eye.ry * 2.2))
    y1 = min(h, int(eye.cy + eye.ry * 2.2) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    ell = ((xx - eye.cx) / eye.rx) ** 2 + ((yy - eye.cy) / eye.ry) ** 2 <= 1.0
    if cover < 1.0:
        ell &= yy <= eye.cy - eye.ry + 2.0 * eye.ry * cover

    soft = cv2.GaussianBlur(ell.astype(np.float32), (0, 0), 1.2)[..., None]
    roi = out_rgb[y0:y1, x0:x1]
    roi[:] = roi * (1.0 - soft) + skin[None, None, :] * soft

    if closed:
        canvas = np.ascontiguousarray(out_rgb)
        cv2.ellipse(canvas,
                    (int(round(eye.cx)), int(round(eye.cy + eye.ry * 0.15))),
                    (max(2, int(eye.rx * 0.9)), max(2, int(eye.ry * 0.55))),
                    0.0, 200.0, 340.0, eye.dark,
                    max(1, int(eye.ry * 0.18)), lineType=cv2.LINE_AA)
        out_rgb[:] = canvas


def make_blink_frames(rgba: np.ndarray, eyes: list[Eye],
                      covers: tuple[float, ...] = (0.55, 1.0)) -> list[np.ndarray]:
    """返回半闭、全闭两帧（RGBA）。没有眼睛时返回空列表。"""
    if not eyes:
        return []
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    frames = []
    for cover in covers:
        out = rgba.copy()
        for eye in eyes:
            skin = _eye_skin_color(rgba[..., :3], rgba[..., 3], eye, gray)
            _draw_closed_eye(out[..., :3], rgba[..., 3], eye, cover, skin,
                             closed=(cover >= 1.0))
        frames.append(out)
    return frames


def _shift_roi(roi: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """把 ROI 平移 (dx, dy)，空出来的边缘用该侧边缘像素补齐。"""
    out = np.roll(roi, (dy, dx), axis=(0, 1))
    if dy > 0:
        out[:dy] = roi[0:1]
    elif dy < 0:
        out[dy:] = roi[-1:]
    if dx > 0:
        out[:, :dx] = out[:, dx:dx + 1]
    elif dx < 0:
        out[:, dx:] = out[:, dx - 1:dx]
    return out


def _ellipse(h: int, w: int, cx: float, cy: float,
             rx: float, ry: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - cx) / max(0.5, rx)) ** 2 + ((yy - cy) / max(0.5, ry)) ** 2 <= 1.0


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """填掉被前景包住的洞（虹膜内部的亮色区会被误判成洞）。"""
    m = mask.astype(np.uint8)
    h, w = m.shape
    inv = (1 - m).astype(np.uint8)
    tmp = inv.copy()
    ff = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(tmp, ff, (0, 0), 2)
    holes = (inv == 1) & (tmp == 1)
    return mask | holes


def _fill_nearest(rgb: np.ndarray, hole: np.ndarray,
                  src_ok: np.ndarray | None = None) -> np.ndarray:
    """用最近的未遮挡像素填洞。

    比 cv2.inpaint 更稳：inpaint 会把洞旁边的黑色眼线/睫毛颜色抹进来，
    上眼睑附近就会发灰；取最近邻则只会拿到紧挨着的眼白。

    src_ok 用来限制取样源（只允许从眼白这种亮像素取样）：虹膜贴着黑色眼线时，
    不限制就会把眼线的黑色沿最近邻方向拉成一撮放射状黑楔子。
    """
    src = hole.astype(np.uint8) if src_ok is None else (hole | ~src_ok).astype(np.uint8)
    if not (src == 0).any():                 # 没有可用取样点（洞把 ROI 填满了）
        return np.repeat(rgb.reshape(-1, 3).mean(0)[None, None, :], rgb.shape[0],
                         axis=0).repeat(rgb.shape[1], axis=1)
    _, labels = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 3,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    yy, xx = np.nonzero(src == 0)
    lut_x = np.zeros(int(labels.max()) + 1, np.int32)
    lut_y = np.zeros(int(labels.max()) + 1, np.int32)
    lut_x[labels[yy, xx]] = xx
    lut_y[labels[yy, xx]] = yy
    out = rgb.copy()
    m = hole.astype(bool)
    out[m] = rgb[lut_y[labels[m]], lut_x[labels[m]]]
    return out


class _EyeGeo:
    """单只眼的图层拆解：眼白底层 / 虹膜纹理 / 高光 / 眼眶。"""

    __slots__ = ("box", "cx", "cy", "rx", "ry", "opening", "open_soft", "iris",
                 "alpha", "foot", "sclera", "texture", "hl", "split_hl")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _eye_geometry(rgba: np.ndarray, eye: Eye) -> _EyeGeo | None:
    """在眼眶内找虹膜椭圆，并生成「去虹膜的眼白图」和「去高光的虹膜纹理」。

    找不准就返回 None，调用方退回整块眼区平移的保守做法。
    """
    h, w = rgba.shape[:2]
    pad = int(max(eye.rx, eye.ry) * 0.35) + 2
    x0 = max(0, int(eye.cx - eye.rx - pad))
    x1 = min(w, int(eye.cx + eye.rx + pad) + 1)
    y0 = max(0, int(eye.cy - eye.ry - pad))
    y1 = min(h, int(eye.cy + eye.ry + pad) + 1)
    ww, hh = x1 - x0, y1 - y0
    if ww < 8 or hh < 6:
        return None

    rgb = rgba[y0:y1, x0:x1, :3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    sat, val = hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    lx, ly = eye.cx - x0, eye.cy - y0
    opening = _ellipse(hh, ww, lx, ly, eye.rx, eye.ry)

    # 虹膜判据：眼眶内有彩度（彩色瞳孔）或够暗（深色瞳孔），排除亮而淡的高光
    cand = (opening & ((sat > 0.35) | (val < 0.45))
            & ~((val > 0.85) & (sat < 0.55))).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(cand, 8)

    best = None
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < max(8, int(opening.sum() * 0.08)):
            continue
        d2 = (cents[i][0] - lx) ** 2 + (cents[i][1] - ly) ** 2
        score = area / (1.0 + d2 * 0.5)
        if best is None or score > best[0]:
            best = (score, i)
    if best is None:
        return None

    iris = _fill_holes(labels == best[1]) & opening
    ys, xs = np.where(iris)
    if len(xs) < 12:
        return None
    irx = (xs.max() - xs.min() + 1) / 2.0
    iry = (ys.max() - ys.min() + 1) / 2.0
    icx = (xs.min() + xs.max()) / 2.0
    icy = (ys.min() + ys.max()) / 2.0
    # 虹膜应接近圆形且不超出眼眶；细长的暗带（刘海、眼线）不是虹膜
    if min(irx, iry) / max(irx, iry) < 0.45:
        return None
    if irx > eye.rx * 1.02 or iry > eye.ry * 1.02:
        return None

    area_det = int(iris.sum())
    det = (icx, icy, irx, iry)      # 检出范围：仍用于找高光，不参与补全

    # 动漫眼的虹膜下半部常是「高亮 + 低饱和」的浅色（浅青、浅粉），
    # 会被上面的彩度/暗度判据漏掉，只剩深色上半部被检出；
    # 直接按它平移就表现为「只有上半只眼在动」。
    # 用眼眶做下限把虹膜补成椭圆：顶部锚定检出的上缘往下补，左右取大值。
    # 多出来的部分是眼白（白移白不可见），并且留 14%~16% 边距，碰不到眼睑/眼线
    irx = max(irx, 0.84 * eye.rx)
    iry = max(iry, 0.86 * eye.ry)
    icx = float(np.clip((xs.min() + xs.max()) / 2.0,
                        lx - eye.rx * 0.25, lx + eye.rx * 0.25))
    icy = float(np.clip(ys.min() + iry,
                        ly - eye.ry * 0.25, ly + eye.ry * 0.25))
    iris = _ellipse(hh, ww, icx, icy, irx, iry) & opening

    # 眼球（虹膜）的软边：用距离变换做约 2px 的过渡，和原图的抗锯齿边缘一致。
    # 平移时这圈软边跟着眼球一起走，原位置不会残留一圈暗线（「眼眶被切」的硬边）
    inner = cv2.distanceTransform(iris.astype(np.uint8), cv2.DIST_L2, 3)
    outer = cv2.distanceTransform((~iris).astype(np.uint8), cv2.DIST_L2, 3)
    alpha = np.clip(0.5 + (inner - outer) / 2.0, 0.0, 1.0).astype(np.float32)
    # 虹膜的「足迹」：比硬掩膜多向外 3px，盖住原图 2px 左右的抗锯齿/描边暗边，
    # 否则眼球挪开后旧位置会残留一条细灰线（看起来像眼眶被切了一刀）
    foot = cv2.dilate(iris.astype(np.uint8), np.ones((7, 7), np.uint8))
    foot = cv2.GaussianBlur(foot.astype(np.float32), (0, 0), 0.6)

    # 眼球挪开后露出的眼白：把虹膜连同软边一起挖掉，只用「眼眶内的浅色像素」填平。
    # 取样源必须限制在 opening 内：眼眶外是眼睑、睫毛和皮肤，最近的肤色/眼线色
    # 会被拉进填充区，眼球移开后就是一撮灰紫色的「黑影」；
    # 再要求低饱和够亮，避免把虹膜外侧那圈偏紫的过渡阴影当成眼白
    hole = cv2.dilate(iris.astype(np.uint8), np.ones((7, 7), np.uint8))
    lit = (val > 0.75) & (sat < 0.2) & opening & (hole == 0)
    if lit.sum() < 6:
        lit = (val > 0.55) & (sat < 0.45) & opening & (hole == 0)
    if lit.sum() < 6:
        lit = opening & (hole == 0)
    if lit.sum() < 6:
        lit = hole == 0
    sclera = _fill_nearest(rgb, hole, src_ok=lit)
    # 环形取样做最近邻填充会留下扇形接缝，轻微模糊后眼白过渡才自然
    # （只有洞内的值会被用到，模糊到洞外不影响原图）
    sclera = cv2.GaussianBlur(sclera, (0, 0), 1.2)

    disc = _ellipse(hh, ww, det[0], det[1], max(1.0, det[2] - 0.6),
                    max(1.0, det[3] - 0.6))
    # 高光是「低饱和 + 够亮」的小块：手办眼白高光常被阴影压暗，
    # 单看亮度会和浅色虹膜下半部混在一起，所以同时要求饱和度够低
    cand = disc & (val > 0.45) & (sat < 0.45) & opening
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), 8)
    hl = np.zeros_like(iris)
    if n2 > 1:
        j = 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))
        area = int(st2[j, cv2.CC_STAT_AREA])
        # 超过虹膜一半的亮区是虹膜本身的配色，不是镜面高光
        if 3 <= area <= 0.55 * max(1, area_det):
            hl = lab2 == j
    split_hl = bool(hl.any())
    if split_hl:
        hm = cv2.dilate(hl.astype(np.uint8), np.ones((2, 2), np.uint8))
        texture = cv2.inpaint(rgb, (hm.astype(bool) & iris).astype(np.uint8), 2,
                              cv2.INPAINT_TELEA)
    else:
        texture = rgb.copy()

    return _EyeGeo(box=(x0, y0, ww, hh), cx=icx, cy=icy, rx=irx, ry=iry,
                   opening=opening, open_soft=cv2.GaussianBlur(
                       opening.astype(np.float32), (0, 0), 0.8),
                   iris=iris, alpha=alpha, foot=foot,
                   sclera=sclera, texture=texture, hl=hl, split_hl=split_hl)


def _compose_gaze(out: np.ndarray, rgba: np.ndarray, geo: _EyeGeo,
                  sx: int, sy: int):
    """用位移场把一只眼整体形变：虹膜刚体平移，眼眶轮廓不动。

    不挖洞、不填补：位移权重在虹膜内部≈1（虹膜+瞳孔整体平移），
    到眼眶边界衰减到 0（眼睑/眼线轮廓不动），中间的「眼白」靠拉伸吸收位移。
    这样眼球挪开的位置不会露出任何被挖掉的底色——之前挖洞再填最近邻，
    会把这颗眼外围那圈紫灰色描边抹进来，看起来就是「移开眼睛产生黑影」。
    """
    x0, y0, ww, hh = geo.box
    rgb = rgba[y0:y0 + hh, x0:x0 + ww, :3].astype(np.float32)
    dst = rgb.copy()

    if sx or sy:
        # 权重：虹膜掩膜的软边（约 2px 过渡）再乘眼眶淡出，保证采样始终在眼内
        w = cv2.GaussianBlur(geo.alpha, (0, 0), 2.2) * geo.open_soft
        yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
        # remap 的 map 是「目标像素去源图哪里取」：写 x - sx 才是把内容往 +sx 挪
        map_x = xx - sx * w
        map_y = yy - sy * w
        dst = cv2.remap(geo.texture, map_x, map_y, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT101)
        dst = dst.astype(np.float32)

    # 高光跟的是光源不是视线：钉在原位（贴回未平移的原图块）
    if geo.split_hl and geo.hl.any():
        hm = cv2.GaussianBlur(geo.hl.astype(np.float32), (0, 0), 0.6)[..., None]
        dst = dst * (1.0 - hm) + rgb * hm

    out[y0:y0 + hh, x0:x0 + ww, :3] = np.clip(dst, 0, 255).astype(np.uint8)


def gaze_frame(rgba: np.ndarray, eyes: list[Eye], dx: int, dy: int) -> np.ndarray:
    """生成「看向 (dx, dy)」的帧，位移单位是像素。

    只挪虹膜，眼白跟着重新分布；上眼睑在向下看时压住虹膜上缘；
    镜面高光基本钉在原位；两眼分别朝鼻侧微收（近距离内聚）。
    找不准虹膜时退回「整块眼区平移到眼眶内」的保守做法。
    """
    if not eyes or (dx == 0 and dy == 0):
        return rgba
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    out = rgba.copy()
    center_x = sum(e.cx for e in eyes) / len(eyes)
    two_eyes = len(eyes) >= 2

    for eye in eyes:
        if eye.ry < 0.45 * eye.rx:      # 眯着/快闭上的那只眼不参与视线
            continue
        geo = _eye_geometry(rgba, eye)
        if geo is not None:
            sx = int(np.clip(round(dx), -0.6 * geo.rx, 0.6 * geo.rx))
            sy = int(np.clip(round(dy), -0.6 * geo.ry, 0.6 * geo.ry))
        else:
            sx = int(np.clip(round(dx), -eye.rx * 0.4, eye.rx * 0.4))
            sy = int(np.clip(round(dy), -eye.ry * 0.4, eye.ry * 0.4))
        # 近距离目标：两眼各自朝鼻侧收一点，避免平行平移像在看远处。
        # 内聚量按视线位移的 20% 取（上限 2px）；固定 1~2px 会把 2px 的视线整个抵掉，
        # 导致某只眼在这个方向上完全不动
        if two_eyes and sx:
            conv = min(2, int(round(0.2 * abs(sx))))
            if conv:
                sx += conv if eye.cx < center_x else -conv
        if sx == 0 and sy == 0:
            continue

        if geo is not None:
            _compose_gaze(out, rgba, geo, sx, sy)
        else:
            skin = _eye_skin_color(rgba[..., :3], rgba[..., 3], eye, gray)
            _draw_closed_eye(out[..., :3], rgba[..., 3], eye, 1.0, skin,
                             closed=False)
            ex = max(4, int(eye.rx * 1.35))
            ey = max(4, int(eye.ry * 1.35))
            x0, x1 = max(0, int(eye.cx - ex)), min(rgba.shape[1], int(eye.cx + ex) + 1)
            y0, y1 = max(0, int(eye.cy - ey)), min(rgba.shape[0], int(eye.cy + ey) + 1)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            src = _shift_roi(rgba[y0:y1, x0:x1], sx, sy)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            ell = (((xx - eye.cx) / max(1.0, ex - 1)) ** 2
                   + ((yy - eye.cy) / max(1.0, ey - 1)) ** 2) <= 1.0
            roi = out[y0:y1, x0:x1]
            roi[ell] = src[ell]
            out[y0:y1, x0:x1] = roi

        # 向下看时虹膜上缘本来就会被眼眶裁掉（等于上眼睑压住），
        # 不再额外铺一层平涂肤色——那会在深色大虹膜上留下突兀的灰块
    return out


def checkerboard(rgba: np.ndarray, cell: int = 12) -> np.ndarray:
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 235, 190).astype(np.float32)
    bg = np.dstack([bg] * 3)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return np.clip(rgba[..., :3] * a + bg * (1 - a), 0, 255).astype(np.uint8)


def _montage(frames: list[np.ndarray]) -> np.ndarray:
    cells = [checkerboard(f) for f in frames]
    h = max(c.shape[0] for c in cells)
    cells = [cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
             for c in cells]
    return np.hstack(cells)


def main():
    ap = argparse.ArgumentParser(description="检测双眼并预览眨眼帧")
    ap.add_argument("--asset", type=Path, required=True)
    ap.add_argument("--preview", type=Path, default=None)
    args = ap.parse_args()

    asset = PetAsset.load(args.asset)
    print(f"素材 {asset.width}×{asset.height}  眼睛 {len(asset.eyes)} 个")
    for e in asset.eyes:
        print(f"  center=({e.cx:.1f},{e.cy:.1f}) r=({e.rx:.1f},{e.ry:.1f}) score={e.score:.1f}")
    frames = asset.blink_frames()
    print(f"眨眼帧 {len(frames)} 张")
    if args.preview and frames:
        out = _montage([asset.rgba] + frames)
        cv2.imwrite(str(args.preview), cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
        print(f"预览 -> {args.preview}")


if __name__ == "__main__":
    main()
