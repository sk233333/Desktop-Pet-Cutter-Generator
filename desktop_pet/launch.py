#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令跑通：选图 -> 框取 -> 选点 -> 预览(可返回补点) -> 多边形眼眶 -> 多边形发缕 -> 生成素材 -> 启动桌宠。

五步交互（每步窗口上方都有提示，写清这一步要做什么）：
  第 1 / 5 步  框取手办   ：拖拽或点两个对角圈住手办，回车确认；r 重框
  第 2 / 5 步  选点       ：左键=前景(红) 右键=背景(蓝)，回车分割；s = 跳过
  第 3 / 5 步  预览分割   ：四联预览，回车采用；a 返回补点；ESC 取消
  第 4 / 5 步  多边形眼眶 ：左键沿眼眶边界加顶点，右键/回车闭合；
                             闭合后可继续画下一只，没有未闭合多边形时回车完成；s = 跳过
  第 5 / 5 步  多边形发缕 ：左键沿发缕轮廓加顶点（第一个顶点=发根），右键/回车闭合；
                             闭合后可继续画下一缕，再按回车完成；s = 跳过

  （--cli 仍是旧的拖框流程：框眼睛、写 _eyes.json，不落 _eyes.png，也没有发缕步骤）

用法：
    python3 desktop_pet/launch.py            # 默认打开图形界面（单窗口）
    python3 desktop_pet/launch.py --ui       # 同上，显式指定
    python3 desktop_pet/launch.py --cli      # 走原来的命令行多窗口流程
    python3 desktop_pet/launch.py --image photo.jpg   # 带参数时自动走命令行流程
    python3 desktop_pet/launch.py --image photo.jpg --box 120,80,900,780
    python3 desktop_pet/launch.py --image photo.jpg --eyes-boxes "150,200,320,300;400,210,560,310"
    python3 desktop_pet/launch.py --image photo.jpg --no-points --no-preview --no-eyes --no-launch
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
import matplotlib
if "MPLBACKEND" not in os.environ:
    matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from PIL import Image

from cutout_to_pet import DEFAULT_CANON, DEFAULT_OUT, build_one, keep_main_parts

def _setup_cjk_font():
    """挑一个系统里真实存在的中文字体，避免标题显示成方块。"""
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC",
                 "Source Han Sans SC", "WenQuanYi Zen Hei",
                 "Droid Sans Fallback", "AR PL UMing CN", "AR PL UKai CN"):
        if name in available:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = (
                [name] + list(matplotlib.rcParams["font.sans-serif"]))
            return name
    return None


CJK_FONT = _setup_cjk_font()
matplotlib.rcParams["axes.unicode_minus"] = False

SKIP = object()  # 用户在可选步骤按 s 跳过

# 这些参数只属于命令行流程；一旦出现就自动走 CLI，而不是打开界面
CLI_FLAGS = {
    "--image", "--box", "--points", "--eyes-boxes", "--no-points", "--no-eyes",
    "--no-preview", "--engine", "--device", "--sku", "--name", "--source-dir",
    "--out-dir", "--size", "--height", "--no-launch",
}
CKPT = (Path(__file__).resolve().parent.parent
        / "Punctuation_selection_of_images" / "sam2.1_hiera_small.pt")


# ------------------------------------------------------------------
# 选图
# ------------------------------------------------------------------
def choose_image() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="选择手办照片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"),
                       ("所有文件", "*")])
        root.destroy()
        return path
    except Exception as exc:  # 无 tkinter/无显示环境时退回手输
        print(f"[提示] 文件选择框不可用（{exc}）")
        return input("请输入图片路径：").strip()


# ------------------------------------------------------------------
# 交互窗口
# ------------------------------------------------------------------
def _new_figure(image, step, hint):
    fig, ax = plt.subplots(figsize=(10, 10))
    fig.suptitle(step, fontsize=14, weight="bold", y=0.995, va="top")
    ax.imshow(image)
    ax.axis("off")
    ax.set_title(hint, fontsize=11)
    fig.subplots_adjust(top=0.86, bottom=0.02, left=0.02, right=0.98)
    return fig, ax


def pick_rect(image, step, hint, color="lime",
              allow_skip=False, full_key=True):
    """画一个框。返回 (x0,y0,x1,y1)；s -> SKIP；ESC/关窗 -> None。"""
    pts, drag = [], {"start": None, "cur": None}
    state = {"box": None, "skip": False, "cancel": False}

    fig, ax = _new_figure(image, step, hint)
    rp = Rectangle((0, 0), 0, 0, linewidth=2.5, edgecolor=color,
                   facecolor="none", visible=False)
    ax.add_patch(rp)
    mk, = ax.plot([], [], "o", color="yellow", markersize=9, visible=False)

    def refresh(tip=""):
        rect = None
        if drag["start"] and drag["cur"]:
            rect = (*drag["start"], *drag["cur"])
        elif len(pts) == 2:
            rect = (*pts[0], *pts[1])
        if rect:
            x1, y1, x2, y2 = rect
            rp.set_bounds(min(x1, x2), min(y1, y2),
                          abs(x2 - x1), abs(y2 - y1))
            rp.set_visible(True)
        else:
            rp.set_visible(False)
        if len(pts) == 1:
            mk.set_data([pts[0][0]], [pts[0][1]])
            mk.set_visible(True)
        else:
            mk.set_visible(False)
        ax.set_title(hint + (f"\n{tip}" if tip else ""), fontsize=11)
        fig.canvas.draw_idle()

    def on_press(e):
        if e.inaxes != ax or e.button != 1 or e.xdata is None:
            return
        drag["start"] = (int(e.xdata), int(e.ydata))
        drag["cur"] = None

    def on_move(e):
        if drag["start"] is None or e.inaxes != ax or e.xdata is None:
            return
        drag["cur"] = (int(e.xdata), int(e.ydata))
        refresh()

    def on_release(e):
        if drag["start"] is None:
            return
        start, drag["start"] = drag["start"], None
        if e.inaxes == ax and e.xdata is not None:
            end = (int(e.xdata), int(e.ydata))
        else:
            end = drag["cur"] or start
        drag["cur"] = None
        if abs(end[0] - start[0]) > 6 or abs(end[1] - start[1]) > 6:
            pts[:] = [list(start), list(end)]
        else:
            if len(pts) >= 2:
                pts.clear()
            pts.append(list(start))
        refresh("回车确认，或继续拖/点重新框")

    def on_key(e):
        k = (e.key or "").lower()
        if k == "enter" and len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            state["box"] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        elif k == "f" and full_key:
            h, w = image.shape[:2]
            state["box"] = (0, 0, w, h)
        elif k == "s" and allow_skip:
            state["skip"] = True
        elif k == "r":
            pts.clear()
            drag["start"], drag["cur"] = None, None
            refresh("已清空，重新框")
            return
        elif k == "u":
            if pts:
                pts.pop()
            refresh("已撤销")
            return
        elif k == "escape":
            state["cancel"] = True
        else:
            return
        plt.close()

    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("motion_notify_event", on_move)
    fig.canvas.mpl_connect("button_release_event", on_release)
    fig.canvas.mpl_connect("key_press_event", on_key)
    refresh()
    plt.show()

    if state["skip"]:
        return SKIP
    if state["cancel"] or state["box"] is None:
        return None
    h, w = image.shape[:2]
    x0, y0, x1, y1 = state["box"]
    x0, y0 = max(0, min(x0, w - 2)), max(0, min(y0, h - 2))
    x1, y1 = min(w, max(x1, x0 + 2)), min(h, max(y1, y0 + 2))
    return x0, y0, x1, y1


def pick_points(image, box, step, hint, prev=None):
    """在前景/背景上标点（可带上次的点）。返回 [(x,y,label)]；s -> SKIP；ESC -> None。"""
    points = [list(p) for p in prev] if prev else []
    state = {"skip": False, "cancel": False}

    fig, ax = _new_figure(image, step, hint)

    def refresh(tip=""):
        ax.clear()
        ax.imshow(image)
        ax.axis("off")
        x1, y1, x2, y2 = box
        ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2.0,
                               edgecolor="#39ff14", facecolor="none",
                               linestyle="--"))
        for i, (x, y, lab) in enumerate(points, 1):
            c = "red" if lab == 1 else "blue"
            ax.plot(x, y, "x", color=c, markersize=13, markeredgewidth=2.5)
            ax.text(x + 8, y - 8, ("F" if lab == 1 else "B") + str(i),
                    color=c, fontsize=10, weight="bold")
        ax.set_title(hint + (f"\n{tip}" if tip else ""), fontsize=11)
        fig.subplots_adjust(top=0.86, bottom=0.02, left=0.02, right=0.98)
        fig.canvas.draw_idle()

    def on_click(e):
        if e.inaxes != ax or e.xdata is None:
            return
        x, y = int(e.xdata), int(e.ydata)
        if e.button == 1:
            points.append([x, y, 1])
            print(f"      +前景 ({x},{y})")
        elif e.button == 3:
            points.append([x, y, 0])
            print(f"      -背景 ({x},{y})")
        else:
            return
        refresh()

    def on_key(e):
        k = (e.key or "").lower()
        if k == "enter":
            plt.close()
        elif k == "s":
            points.clear()
            state["skip"] = True
            plt.close()
        elif k == "u":
            if points:
                points.pop()
            refresh("已撤销一个点")
        elif k == "r":
            points.clear()
            refresh("已清空，重新标点")
        elif k == "escape":
            state["cancel"] = True
            plt.close()

    fig.canvas.mpl_connect("button_press_event", on_click)
    fig.canvas.mpl_connect("key_press_event", on_key)
    refresh()
    plt.show()

    if state["cancel"]:
        return None
    if state["skip"]:
        return SKIP
    return points


def preview_mask(rgb, box, mask, step):
    """四联预览：原图+框 / Mask / 叠加 / 白底。回车采用，a 返回补点，ESC 取消。"""
    act = {"value": "cancel"}
    fig, axs = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(step, fontsize=14, weight="bold", y=0.985)
    axs = axs.ravel()

    x0, y0, x1, y1 = box
    axs[0].imshow(rgb)
    axs[0].add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, linewidth=2.0,
                               edgecolor="lime", facecolor="none"))
    axs[0].set_title("原图 + 框", fontsize=10)

    axs[1].imshow(mask, cmap="gray")
    axs[1].set_title("Mask", fontsize=10)

    tint = np.zeros_like(rgb)
    tint[..., 1] = 255
    ov = rgb.astype(np.float32)
    gray = (rgb.astype(np.float32) * np.array([0.299, 0.587, 0.114])).sum(axis=2)
    dim = np.repeat((gray * 0.45)[..., None], 3, axis=2)
    ov = np.where(mask[..., None] > 0, rgb.astype(np.float32) * 0.55 + tint * 0.45, dim)
    axs[2].imshow(ov.clip(0, 255).astype(np.uint8))
    axs[2].set_title("叠加（绿 = 前景）", fontsize=10)

    wb = np.full_like(rgb, 255)
    wb[mask > 0] = rgb[mask > 0]
    axs[3].imshow(wb)
    axs[3].set_title("白底图", fontsize=10)
    for ax in axs:
        ax.axis("off")

    fig.text(0.5, 0.025, "回车 = 采用并继续     a = 返回补点     ESC = 取消",
             ha="center", fontsize=11)
    fig.subplots_adjust(top=0.90, bottom=0.075, wspace=0.05, hspace=0.18)

    def on_key(e):
        k = (e.key or "").lower()
        if k == "enter":
            act["value"] = "ok"
        elif k == "a":
            act["value"] = "refine"
        elif k == "escape":
            act["value"] = "cancel"
        else:
            return
        plt.close()

    fig.canvas.mpl_connect("key_press_event", on_key)
    plt.show()
    return act["value"]


# ------------------------------------------------------------------
# 掩膜估计
# ------------------------------------------------------------------
def fill_holes(mask: np.ndarray, protect: np.ndarray | None = None,
               max_area: float = 0.015) -> np.ndarray:
    """把被前景包住的**小**洞填上（眼白、腮红、发丝缝里的碎点）。

    只填面积小于整图 max_area 的洞：人物轮廓有时会围出一大块背景
    （手臂与身体之间、头发与肩膀之间），那块必须留着当背景，
    否则掩膜会膨胀成“一整块”，看起来就像点没用。
    """
    inv = (1 - mask).astype(np.uint8)
    ff = inv.copy()
    if protect is not None:
        ff[protect > 0] = 1  # 挡住泛洪，避免这些点被当成洞
    hh = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, hh, (0, 0), 0)
    holes = (inv & (ff != 0)).astype(np.uint8)
    if protect is not None:
        holes[protect > 0] = 0

    n, cc, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=4)
    limit = max_area * mask.shape[0] * mask.shape[1]
    small = np.zeros_like(holes)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] <= limit:
            small[cc == i] = 1
    return (mask | small).astype(np.uint8)


def _color_region(lab_img: np.ndarray, x: int, y: int, tol: float = 10) -> np.ndarray | None:
    """取 (x, y) 所在的一块颜色相近的连通区域。"""
    d = np.linalg.norm(lab_img - lab_img[y, x], axis=2)
    same = (d < tol).astype(np.uint8)
    n, cc = cv2.connectedComponents(same, connectivity=4)
    if n <= 1 or cc[y, x] <= 0:
        return None
    return cc == cc[y, x]


def apply_points(mask: np.ndarray, rgb: np.ndarray | None,
                 pts, labels) -> np.ndarray:
    """把补点当局部编辑用。

    红点：补一小块前景。
    蓝点：删掉掩膜内和它颜色相近的整块区域（比如成片的背景），
          颜色区域太大时退化成删一个小圆。
    这样单击就能生效，又不会因为一个点就推翻整张掩膜。
    """
    if pts is None or len(pts) == 0:
        return mask.astype(np.uint8)
    h, w = mask.shape[:2]
    r = max(3, int(min(h, w) * 0.02))
    m = mask.astype(np.uint8).copy()
    lab_img = (cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
               if rgb is not None else None)
    total = max(1, int(m.sum()))

    for (px, py), lab in zip(pts, labels):
        x, y = int(px), int(py)
        if not (0 <= x < w and 0 <= y < h):
            continue
        if int(lab) == 1:
            cv2.circle(m, (x, y), r, 1, -1)
            continue
        region = _color_region(lab_img, x, y) if lab_img is not None else None
        if region is not None:
            region = region & (m > 0)
            if region.sum() <= 0.3 * total:  # 别一口吃掉大半个主体
                m[region] = 0
                continue
        cv2.circle(m, (x, y), r * 3, 0, -1)
    return m


def clean_mask(mask: np.ndarray, pts=None, labels=None,
               rgb: np.ndarray | None = None) -> np.ndarray:
    mask = keep_main_parts(mask.astype(bool), 0.002).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    if pts is None or len(pts) == 0:
        return fill_holes(mask)

    radius = max(2, int(min(mask.shape) * 0.01))
    protect = _point_disk(mask.shape, pts, labels, radius, 0)
    mask = fill_holes(mask, protect)
    return apply_points(mask, rgb, pts, labels)


def _point_disk(shape, pts, labels, radius: int, which: int) -> np.ndarray:
    """把标签为 which 的补点画成小圆盘。"""
    m = np.zeros(shape[:2], np.uint8)
    for (px, py), lab in zip(pts, labels):
        if int(lab) != which:
            continue
        cv2.circle(m, (int(px), int(py)), radius, 1, -1)
    return m


def load_sam(device: str):
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    if not CKPT.exists():
        raise FileNotFoundError(f"找不到 SAM 权重：{CKPT}")
    print(f"[模型] 加载 SAM2（{device}）...")
    model = build_sam2(config_file="configs/sam2.1/sam2.1_hiera_s.yaml",
                       ckpt_path=str(CKPT), device=device)
    print("[模型] 就绪 ✓")
    return SAM2ImagePredictor(model)


def border_white_ratio(crop: np.ndarray) -> float:
    """看裁切块四周一圈有多白，用来判断是否白底图。"""
    h, w = crop.shape[:2]
    b = max(4, int(min(h, w) * 0.06))
    border = np.concatenate([
        crop[:b].reshape(-1, 3), crop[-b:].reshape(-1, 3),
        crop[:, :b].reshape(-1, 3), crop[:, -b:].reshape(-1, 3)])
    return float((border.min(axis=1) > 232).mean())


def _point_satisfaction(mask: np.ndarray, pts, labels):
    """前景点落在掩膜内的比例 / 背景点落在掩膜外的比例。"""
    h, w = mask.shape[:2]
    f_in, b_out = [], []
    for (px, py), lab in zip(pts, labels):
        x, y = int(px), int(py)
        if not (0 <= x < w and 0 <= y < h):
            continue
        (f_in if int(lab) == 1 else b_out).append(bool(mask[y, x]))
    fi = float(np.mean(f_in)) if f_in else 1.0
    bo = float(np.mean([not v for v in b_out])) if b_out else 1.0
    return fi, bo


def mask_with_sam(predictor, rgb, box, pts, labels):
    """框 + 点 -> 掩膜。

    有补点时以“点是否被满足”来选候选，而不是只看 SAM 分数：
    框很大时框本身几乎没有信息量，SAM 会把整幅图当成一个物体（分数还很高），
    这时优先只用点提示、框只用来裁剪，右键排除才会真正生效。
    """
    predictor.set_image(rgb)
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = box
    has_pts = pts is not None and len(pts) > 0

    clip = np.zeros((h, w), bool)
    clip[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = True
    box_cover = float(clip.mean())

    box_kw = {"box": np.array(box, np.float32)}
    pts_kw = {"point_coords": pts, "point_labels": labels}
    if not has_pts:
        modes = [("框", box_kw)]
    elif box_cover > 0.5:
        print(f"    框覆盖整图 {box_cover:.0%}，优先只用点提示（框只做裁剪）")
        modes = [("点", pts_kw), ("框+点", {**box_kw, **pts_kw})]
    else:
        print(f"    SAM 提示：框 + 前景 {(labels == 1).sum()} 点 / 背景 {(labels == 0).sum()} 点")
        modes = [("框+点", {**box_kw, **pts_kw}), ("点", pts_kw)]

    pick, fallback = None, None
    for tag, kw in modes:
        masks, scores, _ = predictor.predict(multimask_output=True, **kw)
        cands = []
        for i in range(len(masks)):
            m = (masks[i] > 0) & clip
            if not m.any():
                continue
            fi, bo = (_point_satisfaction(m, pts, labels) if has_pts
                      else (1.0, 1.0))
            cands.append((m, float(scores[i]), i, tag, fi, bo))
            print(f"      {tag} 候选{i}：分数 {scores[i]:.3f} "
                  f"覆盖 {m.mean() * 100:.1f}%"
                  + (f"  前景点在内 {fi:.0%} 背景点在外 {bo:.0%}" if has_pts else ""))
        if not cands:
            continue
        # 先找完全满足补点的候选，再挑分数最高的
        ok = [c for c in cands if c[4] >= 1.0 and c[5] >= 1.0]
        if ok:
            pick = max(ok, key=lambda c: c[1])
            break
        best_fb = max(cands, key=lambda c: (c[4] + c[5], c[1]))
        if fallback is None or ((best_fb[4] + best_fb[5], best_fb[1])
                                > (fallback[4] + fallback[5], fallback[1])):
            fallback = best_fb
    if pick is None:
        pick = fallback
    if pick is None:
        return np.zeros((h, w), np.uint8)
    print(f"    采用 {pick[3]} 候选{pick[2]}：覆盖 {pick[0].mean() * 100:.1f}%"
          + ("（补点没有全部满足，已按局部编辑补强）"
             if pick[4] < 1.0 or pick[5] < 1.0 else ""))
    return pick[0].astype(np.uint8)


def _paint_point_labels(gc: np.ndarray, pts, labels, radius: int):
    """把补点画成一小块确定前景/背景；单像素对 GrabCut 几乎没影响。"""
    h, w = gc.shape[:2]
    for (px, py), lab in zip(pts, labels):
        px, py = int(px), int(py)
        if not (0 <= px < w and 0 <= py < h):
            continue
        cv2.circle(gc, (px, py), radius,
                   cv2.GC_FGD if lab == 1 else cv2.GC_BGD, -1)


def mask_with_grabcut(bgr, box, pts, labels):
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = box
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)

    if pts is not None and len(pts):
        print(f"    GrabCut 提示：框 + 前景 {(labels == 1).sum()} 点 / 背景 {(labels == 0).sum()} 点")
    else:
        print("    GrabCut 提示：只有框（没有补点）")

    # 先用框做一轮正常初始化（比直接把所有框内像素标成“可能前景”更稳）
    # 框必须留出至少 1px 边缘，否则 GrabCut 取不到背景样本会直接报错
    rx0, ry0 = max(1, x0), max(1, y0)
    rx1, ry1 = min(w - 1, x1), min(h - 1, y1)
    if rx1 - rx0 < 2 or ry1 - ry0 < 2:
        return np.zeros((h, w), np.uint8)
    gc = np.zeros((h, w), np.uint8)
    rect = (rx0, ry0, rx1 - rx0, ry1 - ry0)
    cv2.grabCut(bgr, gc, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)

    # 再把补点作为硬约束，重新跑一轮；左键加点 / 右键排除才能真正生效
    if pts is not None and len(pts):
        _paint_point_labels(gc, pts, labels, max(3, int(min(h, w) * 0.012)))
        cv2.grabCut(bgr, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    return ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)


def estimate_mask(rgb, rgba, has_alpha, box, pts, labels, engine, device,
                  cache=None):
    """按图/框/点估计整幅掩膜；cache 复用已加载的 SAM 预测器。"""
    cache = cache if cache is not None else {}
    x0, y0, x1, y1 = box

    if has_alpha:
        return clean_mask((rgba[..., 3] > 20).astype(np.uint8))

    crop_rgb = rgb[y0:y1, x0:x1]
    wr = border_white_ratio(crop_rgb)
    has_pts = pts is not None and len(pts) > 0
    if wr > 0.8:
        gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
        base = np.zeros(rgb.shape[:2], np.uint8)
        base[y0:y1, x0:x1] = (gray < 245).astype(np.uint8)
        if not has_pts:
            print(f"    背景≈白（{wr:.0%}），用非白像素取掩膜（不加载 SAM）")
            return clean_mask(base)
        # 白底时 SAM 会把整幅图当成前景，补点几乎无效；
        # 以阈值结果为初值跑 GrabCut，左键加点/右键排除才能真正生效
        print(f"    背景≈白（{wr:.0%}），阈值初值 + 补点微调")
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        gc = np.full(base.shape, cv2.GC_BGD, np.uint8)
        gc[base > 0] = cv2.GC_PR_FGD
        _paint_point_labels(gc, pts, labels, max(3, int(min(base.shape) * 0.012)))
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(bgr, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
        return clean_mask(((gc == cv2.GC_FGD)
                           | (gc == cv2.GC_PR_FGD)).astype(np.uint8), pts, labels, rgb)

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    use = engine
    if use == "auto":
        use = "sam" if CKPT.exists() else "grabcut"
    full_box = (x1 - x0) * (y1 - y0) >= 0.95 * rgb.shape[0] * rgb.shape[1]
    if use == "sam" and full_box and not has_pts:
        print("    整图框且没有补点，SAM 会返回整幅图，改用 GrabCut")
        use = "grabcut"

    if use == "sam":
        if "predictor" not in cache:
            dev = device
            try:
                import torch
                if dev == "cuda" and not torch.cuda.is_available():
                    print("    CUDA 不可用，改用 CPU")
                    dev = "cpu"
            except ImportError:
                dev = "cpu"
            try:
                cache["predictor"] = load_sam(dev)
            except Exception as exc:
                print(f"    SAM 加载失败（{exc}），回退 GrabCut")
                use = "grabcut"
            else:
                cache["device"] = dev
        if use == "sam":
            mask = mask_with_sam(cache["predictor"], rgb, box, pts, labels)
            return clean_mask(mask, pts, labels, rgb)

    mask = mask_with_grabcut(bgr, box, pts, labels)
    return clean_mask(mask, pts, labels, rgb)


# ------------------------------------------------------------------
# 输出
# ------------------------------------------------------------------
def safe_stem(name: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z_\-]+", "_", name).strip("_")
    return stem or "pet"


def parse_rects(text: str | None) -> list[tuple[int, int, int, int]]:
    if not text:
        return []
    rects = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        vals = [int(v) for v in part.split(",")]
        if len(vals) != 4:
            raise ValueError(f"眼睛框需要 4 个整数：{part}")
        rects.append((vals[0], vals[1], vals[2], vals[3]))
    return rects


def save_eyes_json(source_dir: Path, stem: str, crop_size, boxes, origin,
                   force: bool = False, levels=None) -> Path | None:
    """眼睛框（原图坐标）-> 裁切区域坐标，写到 source 目录供 build_one 使用。

    force=True 时即使没有框也写一份空 eyes 列表：明确表示“这只/这些眼不眨眼”。
    levels 是逐只眼的处理档位（L2/L1/L0/auto），与 boxes 同序写入 manifest，
    允许手工覆盖自动判定：同一张图里可以一只走移虹膜、另一只走整块微移。
    """
    if not boxes and not force:
        return None
    ox, oy = origin
    cw, ch = crop_size
    eyes = []
    eye_levels = []
    for i, (bx0, by0, bx1, by1) in enumerate(boxes):
        x1, x2 = sorted((bx0 - ox, bx1 - ox))
        y1, y2 = sorted((by0 - oy, by1 - oy))
        x1, x2 = max(0, min(x1, cw)), max(0, min(x2, cw))
        y1, y2 = max(0, min(y1, ch)), max(0, min(y2, ch))
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            eyes.append({"x1": int(x1), "y1": int(y1),
                         "x2": int(x2), "y2": int(y2)})
            if levels:
                eye_levels.append(str(levels[i]) if i < len(levels) else "auto")
    data = {"stem": stem, "image_size": [int(cw), int(ch)], "eyes": eyes}
    if levels:
        data["eye_level"] = eye_levels
    p = source_dir / f"{stem}_eyes.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def save_hair_polys(source_dir: Path, stem: str, crop_size, wisps, origin,
                    force: bool = False, kind: str = "hair") -> Path | None:
    """多边形发缕 / 衣服（原图坐标）-> 裁切区域坐标，写到 source 目录供 build_one 使用。

    每块落盘一张灰度 mask `<stem>_<kind>1.png`…，json 记发根(旋转中心)与发梢；
    kind="hair" 是头发，"cloth" 是衣服 / 小附件的飘动区域。
    force=True 时即使一块都没有也写一份空列表：明确表示这张图不做这类飘动。
    """
    if not wisps and not force:
        return None
    ox, oy = origin
    cw, ch = crop_size
    out = []

    def clamp(v: float, hi: int) -> float:
        return round(max(0.0, min(float(hi - 1), v)), 1)

    for i, w in enumerate(wisps, 1):
        m = w["mask"][oy:oy + ch, ox:ox + cw]
        if m.shape != (ch, cw) or not m.any():
            continue
        name = f"{stem}_{kind}{i}.png"
        cv2.imwrite(str(source_dir / name), m.astype(np.uint8) * 255)
        out.append({"mask": name,
                    "pivot": [clamp(float(w["pivot"][0]) - ox, cw),
                              clamp(float(w["pivot"][1]) - oy, ch)],
                    "tip": [clamp(float(w["tip"][0]) - ox, cw),
                            clamp(float(w["tip"][1]) - oy, ch)]})
    keep = {it["mask"] for it in out}
    for old in source_dir.glob(f"{stem}_{kind}*.png"):
        if old.name not in keep:
            old.unlink()
    p = source_dir / f"{stem}_{kind}.json"
    p.write_text(json.dumps({"stem": stem, "image_size": [int(cw), int(ch)],
                             "wisps": out}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def save_blink_json(source_dir: Path, stem: str, plan: dict) -> Path:
    """眨眼时间轴 -> source 目录的 <stem>_blink.json（与坐标无关，可直接用）。

    每个对象是 {group: 眼组, count: 眼睛数量, levels: ["L1-1","L1-2"],
    t: 循环内触发秒数, hold: 闭眼保持秒数}，按 (t, group) 升序保存。
    """
    items = []
    for it in plan.get("items") or []:
        try:
            group = max(1, int(it.get("group", 1)))
            t = max(0.0, float(it["t"]))
            hold = min(3.0, max(0.03, float(it.get("hold", 0.2))))
            count = min(8, max(1, int(it.get("count", 2))))
        except (KeyError, TypeError, ValueError):
            continue
        levels = []
        for tok in it.get("levels") or []:
            try:
                eye = int(tok.get("eye", 0))
            except (TypeError, ValueError):
                continue
            lv = str(tok.get("level") or "auto")
            if eye >= 1:
                levels.append(f"{'自动' if lv == 'auto' else lv}-{eye}")
        items.append({"group": group, "t": round(t, 2),
                      "hold": round(hold, 2), "count": count,
                      "levels": levels})
    items.sort(key=lambda it: (it["t"], it["group"]))
    try:
        cycle = max(1.0, float(plan.get("cycle", 8.0)))
    except (TypeError, ValueError):
        cycle = 8.0
    p = source_dir / f"{stem}_blink.json"
    p.write_text(json.dumps({"stem": stem,
                             "blink": {"enabled": bool(plan.get("enabled", True)),
                                       "on": bool(plan.get("blink", True)),
                                       "gaze": bool(plan.get("gaze", True)),
                                       "cycle": round(cycle, 2),
                                       "items": items}},
                            ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def update_manifest(out_dir: Path, sku: str, item: dict) -> Path:
    path = out_dir / "manifest.json"
    items = []
    if path.exists():
        try:
            items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
        except json.JSONDecodeError:
            items = []
    items = [it for it in items if it.get("stem") != item["stem"]]
    items.append(item)
    items.sort(key=lambda it: it.get("stem", ""))
    path.write_text(json.dumps({"schema": "pet-asset/1", "sku": sku,
                                "generated_at": datetime.now().isoformat(timespec="seconds"),
                                "items": items}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="选图 -> 框取 -> 选点 -> 预览(可返回补点) -> 多边形眼眶 -> 生成桌宠")
    ap.add_argument("--image", default=None, help="图片路径；不填则弹出文件选择框")
    ap.add_argument("--box", default=None, help="跳过第 1 步，直接给 x0,y0,x1,y1")
    ap.add_argument("--points", default=None,
                    help="跳过第 2 步，格式 x,y,f;x,y,b（f=前景 b=背景）")
    ap.add_argument("--eyes-boxes", default=None,
                    help="跳过第 4 步，直接给外接框（--cli 用；界面里是画多边形眼眶）"
                         "，格式 x0,y0,x1,y1;x0,y0,x1,y1（原图坐标）")
    ap.add_argument("--no-points", action="store_true", help="跳过选点（不标点）")
    ap.add_argument("--no-eyes", action="store_true", help="跳过眼眶标注（用自动检测）")
    ap.add_argument("--no-preview", action="store_true",
                    help="跳过分割预览确认，直接生成素材")
    ap.add_argument("--engine", choices=("auto", "sam", "grabcut"), default="auto",
                    help="掩膜引擎：auto 优先 SAM，失败回退 GrabCut")
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--sku", default="custom", help="素材包名（默认 custom）")
    ap.add_argument("--name", default=None, help="素材文件名（默认由图片名生成）")
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_CANON)
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="输出目录，默认 desktop_pet/assets/<sku>")
    ap.add_argument("--size", type=int, default=512, help="最长边像素（默认 512）")
    ap.add_argument("--height", type=int, default=0, help="桌宠显示高度，0 用 manifest 值")
    ap.add_argument("--no-launch", action="store_true", help="只生成素材，不启动窗口")
    ap.add_argument("--ui", action="store_true",
                    help="强制打开图形操作界面（不带任何参数时默认就是界面）")
    ap.add_argument("--cli", action="store_true",
                    help="强制走命令行多窗口流程（等价于带上 --image 等参数）")
    args = ap.parse_args()

    given = {a.split("=", 1)[0] for a in sys.argv[1:] if a.startswith("--")}
    use_cli = args.cli or (not args.ui and bool(given & CLI_FLAGS))
    if not use_cli:
        from ui_app import run_ui
        run_ui()
        return

    if CJK_FONT is None:
        print("[提示] 未找到中文字体，窗口标题可能显示为方块；"
              "可安装 fonts-noto-cjk 后重试")

    # ---------- 读图 ----------
    path = args.image or choose_image()
    if not path:
        sys.exit("未选择图片")
    img_path = Path(path).expanduser()
    if not img_path.exists():
        sys.exit(f"图片不存在：{img_path}")

    stem = safe_stem(args.name or (img_path.stem + "_pet"))
    sku = safe_stem(args.sku)
    source_dir = args.source_dir / sku
    out_dir = args.out_dir or (DEFAULT_OUT / sku)
    source_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] 读图 {img_path.name}")
    im = Image.open(img_path)
    exif = im.getexif().get(274, 1)
    rgba_pil = im.convert("RGBA")
    if exif in (3, 6, 8):  # 手机竖拍旋转
        rgba_pil = rgba_pil.transpose({
            3: Image.ROTATE_180, 6: Image.ROTATE_270, 8: Image.ROTATE_90}[exif])
    rgba = np.array(rgba_pil)
    rgb = rgba[..., :3]
    has_alpha = int(rgba[..., 3].min()) < 250
    print(f"    尺寸 {rgb.shape[1]}×{rgb.shape[0]}" +
          ("  含 alpha，直接用透明通道" if has_alpha else ""))

    # ---------- 第 1 步：框取 ----------
    print("[2/6] 第 1 / 4 步 · 框取手办")
    if args.box:
        vals = [int(v) for v in args.box.split(",")]
        if len(vals) != 4:
            sys.exit("--box 需要 4 个整数：x0,y0,x1,y1")
        h, w = rgb.shape[:2]
        box = (max(0, vals[0]), max(0, vals[1]),
               min(w, vals[2]), min(h, vals[3]))
    else:
        box = pick_rect(
            rgb, "第 1 / 4 步 · 框取手办",
            "拖拽或左键点两个对角圈住手办，回车确认\n"
            "r = 重框    u = 撤销    f = 用整张图    ESC = 取消",
            allow_skip=False)
        if box is SKIP or box is None:
            sys.exit("已取消")
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 8:
        sys.exit("框太小了")
    print(f"    框 [{x0},{y0},{x1},{y1}]  {x1 - x0}×{y1 - y0}")

    # ---------- 第 2 步：选点 ----------
    pts, labels = None, None
    print("[3/6] 第 2 / 4 步 · 选点")
    if has_alpha:
        print("    已有 alpha，跳过选点")
    elif args.points:
        coords, labs = [], []
        for part in args.points.split(";"):
            raw = part.replace(" ", "")
            if not raw:
                continue
            vals = raw.split(",")
            if len(vals) not in (2, 3):
                sys.exit("--points 格式：x,y,f;x,y,b（f=前景 b=背景）")
            lab_s = vals[2].lower() if len(vals) == 3 else "f"
            lab = {"f": 1, "1": 1, "b": 0, "0": 0}.get(lab_s)
            if lab is None:
                sys.exit("--points 标签只能是 f/1（前景）或 b/0（背景）")
            coords.append([int(vals[0]), int(vals[1])])
            labs.append(lab)
        pts, labels = np.array(coords, np.float32), np.array(labs, np.int32)
        print(f"    前景 {(labels == 1).sum()} 个，背景 {(labels == 0).sum()} 个")
    elif args.no_points:
        print("    --no-points，跳过")
    else:
        res = pick_points(
            rgb, box, "第 2 / 4 步 · 选点",
            "左键 = 前景(红)   右键 = 背景(蓝)   回车 = 分割\n"
            "r = 全部清空    u = 撤销上一个点    s = 不补点    ESC = 取消")
        if res is None:
            sys.exit("已取消")
        if res is SKIP:
            print("    已跳过补点")
        elif res:
            pts = np.array([[p[0], p[1]] for p in res], dtype=np.float32)
            labels = np.array([p[2] for p in res], dtype=np.int32)
            print(f"    前景 {(labels == 1).sum()} 个，背景 {(labels == 0).sum()} 个")

    # ---------- 第 4 步：预览分割结果（a 返回补点） ----------
    print("[4/6] 第 3 / 4 步 · 预览分割结果")
    prev_pts = []
    if pts is not None:
        prev_pts = [[int(x), int(y), int(l)] for (x, y), l in zip(pts, labels)]
    cache = {}
    mask = estimate_mask(rgb, rgba, has_alpha, box, pts, labels,
                         args.engine, args.device, cache)

    if args.no_preview:
        print("    --no-preview，直接采用当前掩膜")
    else:
        while True:
            act = preview_mask(rgb, box, mask, "第 3 / 4 步 · 预览分割结果")
            if act == "ok":
                break
            if act == "cancel":
                sys.exit("已取消")
            res = pick_points(
                rgb, box, "第 3 / 4 步 · 补点精修（回车重新分割）",
                "左键 = 前景(红)   右键 = 背景(蓝)   回车 = 重新分割\n"
                "u = 撤销上一个点    r = 全部清空    s = 不再补点    ESC = 取消",
                prev=prev_pts)
            if res is None:
                sys.exit("已取消")
            if res is SKIP:
                print("    不再补点，沿用当前掩膜")
                break
            prev_pts = res
            pts = np.array([[p[0], p[1]] for p in res], np.float32)
            labels = np.array([p[2] for p in res], np.int32)
            print(f"    前景 {(labels == 1).sum()} 个，背景 {(labels == 0).sum()} 个")
            mask = estimate_mask(rgb, rgba, has_alpha, box, pts, labels,
                                 args.engine, args.device, cache)

    # ---------- 第 5 步：框眼睛 ----------
    print("[5/6] 第 4 / 4 步 · 定位左眼 / 右眼（用于视线与眨眼）")
    eyes_boxes = []
    eyes_manual = False
    if args.eyes_boxes:
        eyes_boxes = parse_rects(args.eyes_boxes)
        eyes_manual = True
        print(f"    命令行给了 {len(eyes_boxes)} 个眼睛框")
    elif args.no_eyes:
        print("    --no-eyes，跳过左眼/右眼定位（启动时自动检测）")
    else:
        eyes_manual = True
        idx = 1
        while True:
            res = pick_rect(
                rgb, f"第 4 / 4 步 · 框眼睛（第 {idx} 只）",
                "框住一只眼睛的整个眼窝（含眼白），拖拽或点两个对角，回车确认\n"
                "确认后会继续框下一只；u = 撤销当前框    s = 完成框眼    ESC = 取消",
                color="cyan", allow_skip=True, full_key=False)
            if res is None:
                sys.exit("已取消")
            if res is SKIP:
                break
            eyes_boxes.append(res)
            print(f"    第 {idx} 只眼框 {res}")
            idx += 1
        print(f"    共框了 {len(eyes_boxes)} 只眼"
              + ("（不做眨眼/视线）" if not eyes_boxes else ""))

    # ---------- 第 6 步：生成素材 ----------
    print("[6/6] 生成透明素材")

    # ---------- 裁切 + 白底合成 ----------
    crop = rgb[y0:y1, x0:x1]
    white = np.full_like(crop, 255)
    white[mask[y0:y1, x0:x1] > 0] = crop[mask[y0:y1, x0:x1] > 0]
    src_png = source_dir / f"{stem}.png"
    src_mask = source_dir / f"{stem}_mask.png"
    Image.fromarray(white).save(src_png)
    Image.fromarray(mask[y0:y1, x0:x1] * 255).save(src_mask)

    eyes_path = None
    if eyes_manual:
        eyes_path = save_eyes_json(source_dir, stem, (x1 - x0, y1 - y0),
                                   eyes_boxes, (x0, y0), force=True)
        print(f"    ✓ 眼睛标注 {eyes_path.name}（{len(eyes_boxes)} 只，"
              f"未框的眼睛不眨眼）")
    elif (source_dir / f"{stem}_eyes.json").exists():
        print(f"    · 沿用已有 {stem}_eyes.json")

    item = build_one(src_png, src_mask, source_dir / f"{stem}.json", out_dir,
                     args.size, edge_px=4, margin=8,
                     use_matting=True, preview=True)
    manifest = update_manifest(out_dir, sku, item)
    total = len(json.loads(manifest.read_text(encoding="utf-8"))["items"])
    print(f"    ✓ {out_dir / (stem + '.png')}")
    print(f"    ✓ {manifest}（共 {total} 个素材）")
    print(f"    掩膜覆盖率 {item['coverage'] * 100:.1f}%  "
          f"手工眼睛 {len(item.get('eyes') or [])} 个")

    if args.no_launch:
        print("[完成] --no-launch，跳过启动")
        return

    print("[启动] 桌宠窗口（拖动/滚轮缩放/右键退出）")
    from PyQt6.QtWidgets import QApplication
    from anim import PetAsset
    from manifest_io import active_eye_indices
    from pet_window import PetWindow, prefer_xcb

    asset = PetAsset.load(out_dir / f"{stem}.png", eyes=item.get("eyes"),
                          hair=item.get("hair"),
                          cloth=item.get("cloth"),
                          blink=item.get("blink"),
                          eye_masks=[out_dir / n for n in
                                     (item.get("eyes_mask") or [])],
                          eye_level=item.get("eye_level"),
                          active_eyes=active_eye_indices(item))
    asset.anchor = (item["anchor"]["x"], item["anchor"]["y"])
    prefer_xcb()   # 必须在 QApplication 之前决定平台插件
    app = QApplication(sys.argv[:1])
    height = args.height or item.get("display_height_px") or 256
    win = PetWindow(asset, height)
    win.show()
    print(f"    眼睛 {len(asset.eyes)} 个，眨眼帧 {len(win.blink_pixmaps)} 张，"
          f"眨眼时间轴 {'开' if asset.blinks else '关（随机）'}")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
