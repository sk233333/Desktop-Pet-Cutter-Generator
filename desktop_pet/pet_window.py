#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyQt6 透明置顶桌宠窗口。

加载 cutout_to_pet.py 生成的透明素材，按 anim.py 的通用动作层做
呼吸（纵向缩放）、摇摆（绕脚底旋转）和随机眨眼，并支持：

- 视线跟随：眼珠朝鼠标方向偏移（需要眼睛位置）
- 点击反应：单击时弹跳一下并连眨两下
- 拖拽甩动：拖动时记录速度，松手后按惯性滑行
- 落地回弹：松手后受重力落到屏幕底部，触地回弹并轻微压缩；
  关掉落地回弹则原地停住，可以把桌宠摆到任意高度
- 眨眼时间轴：素材标注里的 blink 计划（眼组 + 时刻 + 闭眼时长）按周期循环播放；
  每两只眼一组（眼1+眼2、眼3+眼4…），各组按自己的时刻独立眨眼；
  没有计划时保持原来的随机眨眼

用法：
    python3 desktop_pet/pet_window.py --sku bgy --stem bgy_001_white
    python3 desktop_pet/pet_window.py --asset desktop_pet/assets/bgy/bgy_001_white.png
    python3 desktop_pet/pet_window.py --sku bgy --height 320 --no-blink
    # 多只同屏：同一素材复制 3 只 / 同一 sku 下取多只素材
    python3 desktop_pet/pet_window.py --sku bgy --stem bgy_001_white --count 3
    python3 desktop_pet/pet_window.py --sku bgy --stems bgy_001_white,bgy_002_white

多只时同进程开多个透明窗口，共享一个动画定时器并两两做碰撞分离：
左右相挤会互相推开，从上方砸到另一只会被弹起并滑向一侧，不会叠罗汉。

操作：左键拖动/单击，滚轮缩放（保持脚底位置）；双击暂停；
右键菜单可暂停、开关眨眼/视线/落地、放大/缩小/复位、退出这只桌宠、退出桌宠系统。
注意：Wayland 合成器不允许客户端定位窗口，惯性/落地回弹/自由摆放都无法生效。
启动时会自动改走 XWayland（xcb）；如需保留原生 Wayland，显式设置
QT_QPA_PLATFORM=wayland（此时拖动退回系统级移动，没有落地回弹）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, QRect, Qt, QTimer
from PyQt6.QtGui import (QCursor, QGuiApplication, QImage, QPainter, QPixmap,
                         QTransform)
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QWidget

try:
    from .anim import (HairField, PetAsset, blink_group_counts, blink_phases,
                       compose_group_blink, eye_group_bbox, gaze_frame,
                       group_eyes, resize_rgba)
    from .action_bridge import ActionBridge
    from .arbiter import Arbiter, timeline_phase
    from .manifest_io import active_eye_indices, read_manifest
except ImportError:  # 直接以脚本方式运行
    from anim import (HairField, PetAsset, blink_group_counts, blink_phases,
                      compose_group_blink, eye_group_bbox, gaze_frame,
                      group_eyes, resize_rgba)
    from action_bridge import ActionBridge
    from arbiter import Arbiter, timeline_phase
    from manifest_io import active_eye_indices, read_manifest


class _ExprPort:
    """ActionBridge 的 sm 端口：把 `.set()` 转成 arbiter.request()。

    ActionBridge 不再直写表情状态，只提交一次 interaction 请求；
    由 arbiter 决定是否被时间轴/更高优先级占住。
    """

    def __init__(self, arbiter, enabled=lambda: True):
        self._a = arbiter
        self._enabled = enabled

    def set(self, name, hold_ms=None):
        if self._enabled():
            self._a.request("interaction", "expr", name, hold_ms)
        return name

ROOT = Path(__file__).resolve().parent


def prefer_xcb():
    """Wayland 会话下优先切到 XWayland（xcb），必须在 QApplication 之前调用。

    Wayland 合成器不允许客户端自己定位窗口：拖动只能交给 startSystemMove，
    惯性甩动 / 重力下落 / 落地回弹全靠 move()，native wayland 下一个都看不到。
    xcb（XWayland）具备窗口定位能力，这些动作才真的动起来；显式设置过
    QT_QPA_PLATFORM 时保持用户的选择。
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if ((os.environ.get("XDG_SESSION_TYPE") or "").lower() == "wayland"
            and os.environ.get("DISPLAY")):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        print("[平台] Wayland 会话：改用 XWayland(xcb)，惯性/落地回弹需要窗口定位能力")


def to_qimage(rgba: np.ndarray) -> QImage:
    """numpy RGBA -> QImage；copy() 防止引用悬空。"""
    buf = np.ascontiguousarray(rgba)
    h, w = buf.shape[:2]
    img = QImage(buf.data, w, h, buf.strides[0], QImage.Format.Format_RGBA8888)
    return img.copy()


def to_pixmap(rgba: np.ndarray, height: int | None) -> QPixmap:
    pm = QPixmap.fromImage(to_qimage(rgba))
    if height and pm.height() != height:
        pm = pm.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    return pm


class PetWindow(QWidget):
    def __init__(self, asset: PetAsset, height: int | None,
                 fps: int = 24, blink: bool = True, animate: bool = True,
                 gaze: bool = True, land: bool = True, hair: bool = True,
                 pack=None):
        super().__init__(None,
                         Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("桌宠")

        self.pack = pack              # PetPack：多只时由它统一驱动与碰撞
        self.asset = asset
        self.raw_blink = asset.blink_frames() if (blink and asset.blink_on) else []
        self.blink_enabled = blink and bool(self.raw_blink)
        self.animate = animate
        self.paused = False
        self.elapsed = 0.0
        self.blink_start: float | None = None
        self.pending_blinks = 0
        self.next_blink = random.uniform(*asset.motion.blink_interval)
        self.drag = None
        self.current: QPixmap | None = None
        self.margin = 32
        self.display_height = 0
        self.initial_height = height or asset.height

        # 交互增强：视线跟随 / 甩动惯性 / 落地回弹
        self.gaze_enabled = gaze and asset.gaze_on
        self.land_enabled = land
        # 头发飘动：只对画过多边形发缕的素材生效
        self.hair_enabled = hair and bool(asset.hair)
        self.hair_field: HairField | None = None
        self.cloth_enabled = hair and bool(asset.cloth)
        self.cloth_field: HairField | None = None
        self.gaze_cache: dict[tuple[int, int], QPixmap] = {}
        # 头发摆动要与眨眼/视线帧合成，这里缓存显示尺寸的 RGBA
        self.gaze_rgba_cache: dict[tuple[int, int], np.ndarray] = {}
        self.blink_rgba: list[np.ndarray] = []
        self.gaze_vec = [0.0, 0.0]     # 缓动后的虹膜偏移（素材像素）
        self.gaze_max = 3.0
        self.vel = [0.0, 0.0]          # 松手后的速度（像素/秒）
        self.bounce_start: float | None = None
        self.bounce_amp = 0.0
        self._last_global = None
        self._last_t = 0.0
        self._press_global = None
        self._press_moved = False
        # Wayland 下普通客户端的 move() 会被合成器忽略，拖动要交给
        # 系统级移动（startSystemMove），X11 仍走原来的手动拖动（带惯性）
        self._wayland = QGuiApplication.platformName().startswith("wayland")
        self._sys_moving = False
        self._pointer = None          # 最近一次鼠标事件位置（Wayland 用）
        self.setMouseTracking(True)

        # 每帧唯一状态源：blink / expr / gaze / bubble 都由 arbiter.resolve() 决定，
        # 任何地方都不再直接改表情状态（写入一律走 arbiter.request）。
        self.arbiter = Arbiter()
        self._resolved = self.arbiter.resolve()
        self._last_bubble = ""
        self.expr_enabled = True       # 随机表情开关（右键菜单「表情」里可关）

        # 待机效果编排（mood × 表情/视线/气泡）；表缺失时退回纯随机表情
        self.director = None
        try:
            from effect_director import EffectDirector
            self.director = EffectDirector.load(
                cfg=getattr(asset, "effects_cfg", None))
        except Exception:
            self.director = None
        self._expr_rgba: dict[str, np.ndarray] = {}
        self._expr_pixmaps: dict[str, QPixmap] = {}

        self._rebuild(self.initial_height)

        # 本机动作桥：右键可列动作，双击按绑定触发；没有配置时完全静默
        self.actions = None
        self._bubble_lbl = None
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)
        try:
            self.actions = ActionBridge.for_asset(
                asset, _ExprPort(self.arbiter, lambda: self.expr_enabled), self)
            self.actions.feedback.connect(self._bubble)
        except Exception:
            self.actions = None

        self.fps = max(8, fps)
        if pack is None:
            # 单只：自己驱动；多只时由 PetPack 一个定时器统一驱动，
            # 否则每只各自 tick 会导致碰撞在一帧里被重复求解
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(int(1000 / self.fps))
        else:
            pack.add(self)

        self._place_bottom_right()

    # ---------- 尺寸 ----------
    def _rebuild(self, height: int, keep_anchor: bool = False):
        """按新显示高度重建 pixmap；keep_anchor=True 时保持脚底屏幕位置。"""
        scr = QApplication.primaryScreen()
        limit = int(scr.availableGeometry().height() * 0.9) if scr else 1024
        height = max(72, min(limit, int(height)))

        anchor_screen = None
        if keep_anchor and self.isVisible():
            anchor_screen = (self.x() + self.margin + self.ax,
                             self.y() + self.margin + self.ay)

        self.display_height = height
        self.base = to_pixmap(self.asset.rgba, height)
        self._expr_rgba.clear()          # 显示尺寸变了，表情帧缓存作废
        self._expr_pixmaps.clear()
        self.blink_pixmaps = [to_pixmap(f, height) for f in self.raw_blink]
        # 头发飘动要按显示尺寸重采样 RGBA，位移场也按显示尺寸烘焙
        self.base_rgba = resize_rgba(self.asset.rgba, self.base.height())
        # 碰撞用的主体包围盒（不含窗口透明边距）：按不透明像素算，
        # 再留几像素余量覆盖呼吸/摇摆的轻微形变，避免边缘刚好蹭到
        alpha = self.base_rgba[:, :, 3]
        ys, xs = np.nonzero(alpha > 8)
        if len(xs):
            pad = max(3, int(round(self.base.width() * 0.015)))
            self.body_local = (max(0, int(xs.min()) - pad),
                               max(0, int(ys.min()) - pad),
                               min(self.base.width(), int(xs.max()) + 1 + pad),
                               min(self.base.height(), int(ys.max()) + 1 + pad))
        else:
            self.body_local = (0, 0, self.base.width(), self.base.height())
        # 眨眼帧也要有显示尺寸的 RGBA，才能在头发摆动时继续眨眼
        self.blink_rgba = [resize_rgba(f, self.base.height())
                           for f in self.raw_blink]
        # 每两只眼一个眼组：组内同步眨眼，不同组按各自的时间独立眨眼
        self.group_rgba: dict[int, list[np.ndarray]] = {}
        self.group_rects: dict[int, tuple[int, int, int, int]] = {}
        if self.blink_enabled:
            gf = self.asset.group_blink_frames()
            self.group_rgba = {
                gi: [resize_rgba(f, self.base.height()) for f in frames]
                for gi, frames in gf.items()
            }
            s = self.base.height() / float(max(1, self.asset.height))
            # 分组必须与 group_blink_frames() 用同一套 counts，否则组边界会串位
            for gi, eyes in enumerate(group_eyes(
                    self.asset.eyes,
                    blink_group_counts(self.asset.blinks) or None), 1):
                x0, y0, x1, y1 = eye_group_bbox(eyes)
                self.group_rects[gi] = (
                    max(0, int(x0 * s)), max(0, int(y0 * s)),
                    min(self.base.width(), int(x1 * s) + 1),
                    min(self.base.height(), int(y1 * s) + 1))
        self.hair_field = (HairField(self.asset.hair, self.asset.rgba,
                                     self.base.width(), self.base.height(),
                                     **self.asset.hair_params())
                           if self.hair_enabled else None)
        self.cloth_field = (HairField(self.asset.cloth, self.asset.rgba,
                                      self.base.width(), self.base.height(),
                                      **self.asset.hair_params())
                            if self.cloth_enabled else None)
        self.ax = self.asset.anchor[0] * self.base.width()
        self.ay = self.asset.anchor[1] * self.base.height()
        self.margin = max(20, min(80, height // 8))
        self.resize(self.base.width() + self.margin * 2,
                    self.base.height() + self.margin * 2)
        self.current = self.base
        self.gaze_cache.clear()  # 尺寸变了，视线帧要重新生成
        self.gaze_rgba_cache.clear()
        # 目标偏移幅度要和每只眼实际能挪的量对齐：以前取眼睛尺寸得到 8~9px，
        # 而 L2 按眼白余量最多只有 2~3px，鼠标一离开就先把每个分量夹死，
        # 再拉远也不会变化，看起来就是「离远了不跟」。这里取各眼单方向
        # 可达幅度（limit_x/limit_y 分开算）的最大值；具体某一眼某个方向
        # 超出自己的余量时由 gaze_frame 按方向等比收回。
        limits = [max(g.limit_x, g.limit_y) for _, g in self._gaze_geo_pairs()
                  if g is not None and getattr(g, "enabled", False)]
        if limits:
            self.gaze_max = max(1.0, float(max(limits)))
        else:
            self.gaze_max = 2.0
        self.gaze_vec = [0.0, 0.0]

        if anchor_screen is not None:
            self.move(int(round(anchor_screen[0] - self.margin - self.ax)),
                      int(round(anchor_screen[1] - self.margin - self.ay)))

    def _scale_by(self, factor: float):
        self._rebuild(int(round(self.display_height * factor)), keep_anchor=True)

    # ---------- 位置 ----------
    @staticmethod
    def _ground(g) -> int:
        return g.bottom() - 8

    def _place_bottom_right(self):
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        self.move(g.right() - self.width() - 48,
                  self._ground(g) - self.height())

    def closeEvent(self, event):
        """关闭时从同进程宠物组里摘掉，最后一只关掉后停掉共享定时器。"""
        super().closeEvent(event)
        if self.pack is not None:
            self.pack.remove(self)

    def body_rect(self) -> QRect:
        """手办主体在屏幕上的包围盒（不带窗口的透明边距）。"""
        x0, y0, x1, y1 = self.body_local
        m = self.margin
        return QRect(self.x() + m + x0, self.y() + m + y0, x1 - x0, y1 - y0)

    def body_width(self) -> int:
        return self.body_local[2] - self.body_local[0]

    def _movable(self) -> bool:
        """拖动中的窗口视为被抓住，只推开别人；暂停的窗口也不主动挪。"""
        return self.drag is None and not self.paused

    def shift(self, dx: float, dy: float):
        """碰撞分离用：直接挪窗口，并夹在屏幕可用区里。"""
        x = self.x() + dx
        y = self.y() + dy
        scr = QApplication.primaryScreen()
        g = scr.availableGeometry() if scr else None
        if g is not None:
            left = g.left() - self.margin
            right = g.right() - self.width() + self.margin
            x = min(max(left, x), max(left, right))
            y = max(g.top(), y)
        self.move(int(round(x)), int(round(y)))

    def showEvent(self, event):
        super().showEvent(event)
        self._place_bottom_right()

    # ---------- 动画 ----------
    def tick(self):
        dt = 1.0 / self.fps
        now_ms = time.monotonic() * 1000.0
        if not self.paused:
            self.elapsed += dt
            self._step_physics(dt)
            self._update_state(now_ms)
            self._update_gaze(dt)
            self._update_hair(dt)
        self.current = self._pick_frame()
        self.update()

    def _update_state(self, now_ms: float):
        """每帧唯一的状态写入点：时间轴相位 + 待机候选，全部经 arbiter。

        相位只由 `timeline_phase()` 产出后喂 `set_timeline_blink()`；
        待机效果只产 `request()`，不直接改表情状态。
        """
        items = [{"t": b.t * 1000.0, "hold": b.hold * 1000.0,
                  "half": b.half} for b in (self.asset.blinks or [])]
        phase, _ = timeline_phase(self.elapsed * 1000.0,
                                  self.asset.blink_cycle * 1000.0, items,
                                  half_enabled=True)
        self.arbiter.set_timeline_blink(phase, now_ms=now_ms)

        eff = (self.director.tick()
               if self.expr_enabled and self.director is not None else None)
        if eff is not None:
            self.arbiter.request("idle", "expr", eff.get("expr", "normal"),
                                 eff.get("hold_ms"), now_ms)
            if eff.get("bubble"):
                self.arbiter.request("idle", "bubble", eff["bubble"],
                                     eff.get("hold_ms"), now_ms)
            g = self.director.gaze_at(time.monotonic())
            if g is not None:
                self.arbiter.request(
                    "idle", "gaze",
                    (g[0] * self.gaze_max, g[1] * self.gaze_max * 0.7),
                    eff.get("hold_ms"), now_ms)

        self._resolved = self.arbiter.resolve(now_ms)
        bub = self._resolved.get("bubble")
        if bub and bub != self._last_bubble:
            self._last_bubble = bub
            self._bubble(bub)

    def _timeline_blinks(self, t: float) -> dict[int, int]:
        """按眼组返回当前该显示的相位：{组号: 0 半闭 / 1 全闭}。

        相位统一由 `blink_phases()`（内部走 `arbiter.timeline_phase()`）计算：
        `half:false` 整段全闭；`half:true` 才在前半段出半闭，`dt == hold/2`
        归全闭。
        """
        return blink_phases(self.asset.blinks, self.asset.blink_cycle, t)

    def _manual_blink_index(self) -> int | None:
        """手动/点击/随机眨眼的状态机；时间轴模式下只保留点击连眨。"""
        if not self.blink_enabled or self.paused or not self.blink_rgba:
            return None
        t = self.elapsed
        if self.blink_start is not None:
            dt = t - self.blink_start
            if dt < 0.07:
                return 0
            if dt < 0.15:
                return len(self.blink_rgba) - 1
            self.blink_start = None
            if self.pending_blinks > 0:  # 点击时的连眨
                self.pending_blinks -= 1
                self.blink_start = t
                return 0
            if not self.asset.blinks:
                lo, hi = self.asset.motion.blink_interval
                self.next_blink = t + random.uniform(lo, hi)
            return None
        if self.asset.blinks:      # 时间轴模式不再随机眨眼
            return None
        if t >= self.next_blink:
            self.blink_start = t
            return 0
        return None

    def _compose_group_blink(self, active: dict[int, int]) -> QPixmap:
        """把各组正在眨眼的眼皮贴回基础帧；各组只改自己眼睛的包围盒。

        前段帧的取法：`half:true` 的组用 `ExprBank` 的遮挡式整帧（按包围盒裁剪），
        其余组仍用 `make_blink_frames` 的 0.55 遮挡过渡帧——默认观感不变。
        全闭固定取最后一帧（cover=1.0）。
        """
        front: dict[int, np.ndarray] = {}
        if 0 in active.values() and self.asset.expr_bank() is not None:
            half_groups = {b.group_no for b in self.asset.blinks if b.half}
            if any(gi in half_groups for gi, ph in active.items() if ph == 0):
                half = self._expr_frame('half')[0]
                front = {gi: half for gi, ph in active.items()
                         if ph == 0 and gi in half_groups}
        out = compose_group_blink(self.base_rgba, active, self.group_rgba,
                                  self.group_rects, front)
        return QPixmap.fromImage(to_qimage(out))

    def _pick_frame(self) -> QPixmap:
        """先选源帧（点击眨眼 -> 分组眨眼 -> 视线 -> 基础），再叠加头发摆动。

        头发是常驻的微动，不能像以前那样抢占整帧，否则眨眼和视线跟随
        会被永久压住。
        """
        bi = self._manual_blink_index()
        if bi is not None and bi < len(self.blink_rgba):
            src, pm = self.blink_rgba[bi], self.blink_pixmaps[bi]
        else:
            active = (self._timeline_blinks(self.elapsed)
                      if self.blink_enabled and not self.paused else {})
            if active:
                src, pm = self.base_rgba, self._compose_group_blink(active)
            elif self._expr_active():
                src, pm = self._expr_frame(self._resolved.get("expr") or "normal")
            elif self.gaze_enabled and self._gaze_eyes():
                gx, gy = int(round(self.gaze_vec[0])), int(round(self.gaze_vec[1]))
                if gx or gy:
                    src, pm = self._gaze_rgba(gx, gy), self._gaze_pixmap(gx, gy)
                else:
                    src, pm = self.base_rgba, self.base
            else:
                src, pm = self.base_rgba, self.base
        if self._hair_active():
            return self._hair_pixmap(src)
        return pm

    # ---------- 表情差分（半眼 / 惊讶 / 单侧眨眼） ----------
    def _expr_active(self) -> bool:
        """当前是否该显示表情贴片（normal 一律走原路径，保证零重影）。"""
        return (not self.paused and self.expr_enabled
                and (self._resolved.get("expr") or "normal") != "normal"
                and self.asset.expr_bank() is not None)

    def _expr_frame(self, state: str) -> tuple[np.ndarray, QPixmap]:
        """取该状态的显示尺寸帧；第一次用到才按显示高度重采样并缓存。

        全闭眼只有一份实现：'blink' 统一取 `make_blink_frames` 的 cover=1.0 帧
        （与时间轴 frames[-1] 逐像素相同），不再走 ExprBank 的 blink 贴片。
        预览端（ui_app）仍是另一套，留到 T6 收。
        """
        if state not in self._expr_pixmaps:
            if state == "blink" and self.asset.blink_frames():
                base = self.asset.blink_frames()[-1]
            else:
                base = self.asset.expr_bank().apply(self.asset.rgba, state)
            # 贴片是在素材分辨率上烘焙的，先在原尺寸合成再缩到显示高度
            f = resize_rgba(base, self.base.height())
            self._expr_rgba[state] = f
            self._expr_pixmaps[state] = to_pixmap(f, self.base.height())
        return self._expr_rgba[state], self._expr_pixmaps[state]

    def _gaze_eyes(self) -> list:
        """参与视线跟随的眼睛。

        手工框的眼全部参与（用户自己确认过）；自动检测的逐只过滤掉尺寸
        明显不像眼睛的框——以前要求所有框都合理，多眼组里只要有一个小框
        就把整只宠物的视线跟随关掉了。
        """
        return [e for e, _ in self._gaze_geo_pairs()]

    def _gaze_geo_pairs(self) -> list:
        """(眼睛, 几何) 配对；两个列表必须用同一套过滤，否则几何会串位。

        几何是按 asset.eyes 全量算的；自动检测时部分框会被尺寸过滤掉，
        直接 zip 全量 geos 就会把 A 眼的几何套到 B 眼上。
        """
        geos = self.asset.gaze_geos()
        if self.asset.eyes_trusted:
            return list(zip(self.asset.eyes, geos))
        w, h = self.asset.width, self.asset.height
        # 0.12 这个上限太紧：Q 版大眼的框半径本来就有 0.12~0.15 倍图宽，
        # 被误杀后那只眼就完全不跟视线（左眼不动就是这么来的）。放宽到
        # 0.16 只留下真正离谱的框（整颗脑袋那种，0.18 以上）。
        return [(e, g) for e, g in zip(self.asset.eyes, geos)
                if g is not None
                and 0.01 * w <= e.rx <= 0.16 * w
                and 0.01 * h <= e.ry <= 0.18 * h]

    def _pointer_xy(self) -> tuple[int, int]:
        """全局光标位置：只在这里归一化，gaze 与交互共用同一份读数。

        Wayland 不向普通窗口暴露全局光标位置，用窗口内最近一次鼠标事件。
        """
        cur = (self._pointer if self._wayland and self._pointer is not None
               else QCursor.pos())
        return cur.x(), cur.y()

    def _gaze_target(self) -> tuple[float, float]:
        """鼠标方向 -> 目标偏移（素材像素）。

        距离用 smoothstep 缓入，而不是一过死区就直接给满偏移，
        这样鼠标缓慢移动时虹膜也是缓慢跟着走。
        """
        eyes = self._gaze_eyes()
        if not self.gaze_enabled or not eyes:
            return 0.0, 0.0
        px, py = self._pointer_xy()
        s = self.display_height / max(1, self.asset.height)
        m = self.margin
        ex = sum(e.cx for e in eyes) / len(eyes)
        ey = sum(e.cy for e in eyes) / len(eyes)
        dx = px - (ex * s + m + self.x())
        dy = py - (ey * s + m + self.y())
        mag = math.hypot(dx, dy)
        dead = max(14.0, self.display_height * 0.03)
        if mag <= dead:
            return 0.0, 0.0
        # 眼睛能挪的像素本来就少（1~5px），用「固定缓入距离 + smoothstep」
        # 会把近处压成几乎不动、远处早早顶到饱和，看起来就是「离远了不跟」。
        # 改用指数缓入：整屏范围内都保持单调变化，越远越偏，方向始终指向光标。
        scale = max(110.0, self.display_height * 0.45)
        t = 1.0 - math.exp(-(mag - dead) / scale)
        return ((dx / mag) * t * self.gaze_max,
                (dy / mag) * t * self.gaze_max * 0.7)

    def _update_gaze(self, dt: float):
        """把虹膜偏移按一阶缓动逼近目标，避免一帧内从 0 跳到最大偏移。

        目标来源：arbiter.resolve()['gaze']（效果编排的「瞥一眼」）优先，
        否则用鼠标方向。两者共用 `_pointer_xy()` 的同一份归一化读数。
        """
        ov = self._resolved.get("gaze") or (0.0, 0.0)
        if ov[0] or ov[1]:
            tx, ty = float(ov[0]), float(ov[1])
        else:
            tx, ty = self._gaze_target()
        a = 1.0 - math.exp(-dt / 0.18)       # 时间常数 0.18s
        self.gaze_vec[0] += (tx - self.gaze_vec[0]) * a
        self.gaze_vec[1] += (ty - self.gaze_vec[1]) * a
        for i in (0, 1):
            if abs(self.gaze_vec[i]) < 0.06:
                self.gaze_vec[i] = 0.0

    # ---------- 头发飘动 ----------
    def _hair_active(self) -> bool:
        return (not self.paused
                and ((self.hair_enabled and self.hair_field is not None
                      and self.hair_field.ready)
                     or (self.cloth_enabled and self.cloth_field is not None
                         and self.cloth_field.ready)))

    def _update_hair(self, dt: float):
        """头发 / 衣服都是被动二阶系统：被拖拽速度、身体摇摆和落地冲击带着甩。

        平时由低频微风驱动，所以不发呆也有轻微摆动；拖得越猛，末端滞后越明显。
        """
        if self.paused:
            return
        fields = [f for f in (self.hair_field, self.cloth_field)
                  if f is not None and f.ready]
        if not fields:
            return
        if self.drag is not None and time.monotonic() - self._last_t > 0.08:
            k = math.exp(-dt / 0.12)
            self.vel[0] *= k
            self.vel[1] *= k
        tilt = 0.0
        if self.animate:
            tilt = self.asset.motion.at(self.elapsed)["angle"]
        for f in fields:
            f.step(dt, self.vel[0], tilt)

    def _hair_pixmap(self, rgba: np.ndarray) -> QPixmap:
        """在传入的源帧上绕发根旋转发缕；眨眼/视线帧不会被头发盖掉。"""
        out = rgba
        for f in (self.hair_field, self.cloth_field):
            if f is not None and f.ready:
                out = f.swing(out)
        return to_pixmap(out, None)

    def _gaze_rgba(self, gx: int, gy: int) -> np.ndarray:
        """显示尺寸的视线帧；与 Pixmap 缓存同一套键。

        形变在素材分辨率上做（眼睛几何只算一次，不会因显示尺寸不同而漂移），
        再按显示高度缩放一次，和基础帧走同一条缩放路径。
        """
        key = (gx, gy)
        arr = self.gaze_rgba_cache.get(key)
        if arr is None:
            pairs = self._gaze_geo_pairs()
            arr = resize_rgba(
                gaze_frame(self.asset.rgba, [e for e, _ in pairs], gx, gy,
                           geos=[g for _, g in pairs]),
                self.base.height())
            if len(self.gaze_rgba_cache) >= 16:
                self.gaze_rgba_cache.pop(next(iter(self.gaze_rgba_cache)))
            self.gaze_rgba_cache[key] = arr
        return arr

    def _gaze_pixmap(self, gx: int, gy: int) -> QPixmap:
        key = (gx, gy)
        pm = self.gaze_cache.get(key)
        if pm is None:
            if gx == 0 and gy == 0:
                pm = self.base
            else:
                # 复用上面的 RGBA，避免同一偏移算两遍形变
                pm = to_pixmap(self._gaze_rgba(gx, gy), None)
            if len(self.gaze_cache) >= 16:
                self.gaze_cache.pop(next(iter(self.gaze_cache)))  # 丢最老的
            self.gaze_cache[key] = pm
        return pm

    # ---------- 物理：甩动惯性 + 落地回弹 ----------
    def _step_physics(self, dt: float):
        if self.drag is not None:
            return
        if self._wayland:
            # Wayland 合成器忽略客户端的 move()：惯性/重力只会让内部坐标空转，
            # 与屏幕上的真实位置错开，下一次拖动算出的位移就全偏了
            self.vel = [0.0, 0.0]
            return
        vx, vy = self.vel
        # 开启落地回弹时离地就受重力：松手（哪怕轻放）都会落下并触地回弹；
        # 关闭后只有甩动惯性，停下就原地不动
        if not self.land_enabled and abs(vx) < 1e-3 and abs(vy) < 1e-3:
            return
        if self.land_enabled:
            vy += 1500.0 * dt          # 重力
        vx *= math.exp(-2.4 * dt)      # 水平阻尼
        x = self.x() + vx * dt
        y = self.y() + vy * dt

        scr = QApplication.primaryScreen()
        g = scr.availableGeometry() if scr else None
        if g is not None:
            left = g.left() - self.margin
            right = g.right() - self.width() + self.margin
            ground = self._ground(g)
            if x < left:
                x, vx = left, -vx * 0.45
            elif x > right:
                x, vx = right, -vx * 0.45
            if y + self.height() >= ground:
                y = ground - self.height()
                if vy > 80:                       # 触地回弹
                    vy = -vy * 0.45
                    self._bounce(0.7)
                else:
                    vy = 0.0
                if abs(vx) < 20:
                    vx = 0.0
            if y < g.top():
                y, vy = g.top(), abs(vy) * 0.3
        if not self.land_enabled and abs(vx) < 15 and abs(vy) < 15:
            vx = vy = 0.0

        self.vel = [vx, vy]
        self.move(int(round(x)), int(round(y)))

    def _bounce(self, amp: float = 1.0):
        if self.paused:  # 暂停时 elapsed 不推进，弹跳会卡在半途
            return
        self.bounce_start = self.elapsed
        self.bounce_amp = amp
        if self.hair_field is not None:
            self.hair_field.kick(7.0 * amp)

    def _bounce_scale(self) -> tuple[float, float]:
        """点击/落地时的挤压-拉伸，绕脚底锚点缩放。"""
        if self.bounce_start is None or self.paused:
            return 1.0, 1.0
        dt = self.elapsed - self.bounce_start
        if dt > 0.7:
            self.bounce_start = None
            return 1.0, 1.0
        w = self.bounce_amp * math.exp(-dt * 5.5) * math.cos(dt * 20.0)
        return 1.0 + 0.12 * w, 1.0 - 0.22 * w

    def paintEvent(self, _event):
        m = self.margin
        scale_y, angle = 1.0, 0.0
        if self.animate and not self.paused:
            p = self.asset.motion.at(self.elapsed)
            scale_y, angle = p["scale_y"], p["angle"]
        sx, sy = self._bounce_scale()

        tr = QTransform()
        tr.translate(m + self.ax, m + self.ay)
        tr.scale(sx, scale_y * sy)
        tr.rotate(angle)
        tr.translate(-(m + self.ax), -(m + self.ay))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setTransform(tr)
        # Source：整帧替换而不是叠加，否则上一帧残留会和当前帧混在一起越积越脏
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(QPointF(m, m), self.current)
        painter.end()

    # ---------- 交互 ----------
    def mousePressEvent(self, event):
        if self.director is not None:
            self.director.touch()      # 刚被摸过：待机效果先安静 3 秒
        self.arbiter.touch()           # 仲裁层同样进入 3 秒 idle guard
        if event.button() == Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
            self.drag = gp - self.frameGeometry().topLeft()
            self.vel = [0.0, 0.0]
            self._last_global = gp
            self._last_t = time.monotonic()
            self._press_global = gp
            self._press_moved = False
            self._sys_moving = False
            self.pending_blinks = 0
            event.accept()

    def mouseMoveEvent(self, event):
        gp = event.globalPosition().toPoint()
        self._pointer = gp       # Wayland 拿不到全局光标，只能用窗口内事件
        if self.drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            now = time.monotonic()
            dt = max(1e-3, now - self._last_t)
            if self._last_global is not None:
                vx = (gp.x() - self._last_global.x()) / dt
                vy = (gp.y() - self._last_global.y()) / dt
                self.vel[0] = 0.75 * self.vel[0] + 0.25 * vx
                self.vel[1] = 0.75 * self.vel[1] + 0.25 * vy
            self._last_global = gp
            self._last_t = now
            if self._press_global is not None:
                d = gp - self._press_global
                if abs(d.x()) + abs(d.y()) > 6:
                    self._press_moved = True
            if self._wayland:
                # Wayland 不接受客户端自己 move()：越过拖动阈值后交给合成器
                if self._sys_moving:
                    return
                wh = self.windowHandle()
                if wh is not None and wh.startSystemMove():
                    self._sys_moving = True
                    self.drag = None
                    self.vel = [0.0, 0.0]
                    self._last_global = None
                    event.accept()
                    return
            self.move(gp - self.drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.drag = None
        moved = self._press_moved
        self._sys_moving = False
        self._press_global = None
        self._last_global = None
        if not moved:
            # 单击：跳一下 + 连眨两下
            self._bounce(1.0)
            if self.blink_enabled:
                self.blink_start = self.elapsed
                self.pending_blinks = 1
            self.vel = [0.0, 0.0]
        else:
            # 松手：保留甩动速度，交给惯性 + 重力（落地回弹开启时必然落到地面）
            self.vel[0] = max(-1600.0, min(1600.0, self.vel[0]))
            self.vel[1] = max(-1600.0, min(1600.0, self.vel[1]))
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._press_moved:      # 拖动误触：两次按下之间动过就不算连击
                return
            aid = self.actions.binding("double_tap") if self.actions else None
            if aid:
                self.actions.dispatch(aid)
            # 没绑动作就什么都不做：暂停/继续在右键菜单里选，不再占用双击
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # ---------- 动作气泡 ----------
    def _bubble(self, text: str, state: str = "normal"):
        """桌宠头顶的小气泡：动作执行后的反馈（表情由状态机自己管）。"""
        if not text:
            return
        if self._bubble_lbl is None:
            lbl = QLabel(self)
            lbl.setStyleSheet(
                "QLabel{background:rgba(255,255,255,240);color:#4a5568;"
                "border:2px solid #ffe4b6;border-radius:12px;"
                "padding:6px 12px;font-size:13px;}")
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lbl.hide()
            self._bubble_lbl = lbl
        lbl = self._bubble_lbl
        lbl.setText(text)
        lbl.adjustSize()
        lbl.move(max(0, (self.width() - lbl.width()) // 2), 2)
        lbl.show()
        lbl.raise_()
        self._bubble_timer.start(2000)

    def _hide_bubble(self):
        if self._bubble_lbl is not None:
            self._bubble_lbl.hide()

    def _open_action_settings(self):
        """图形化配置动作：选程序、勾双击、保存后立即生效（不用重启）。"""
        from action_dialog import ActionDialog
        dlg = ActionDialog(self.actions.cfg if self.actions else {}, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.actions = ActionBridge.for_asset(
                self.asset, _ExprPort(self.arbiter, lambda: self.expr_enabled), self)
            self.actions.feedback.connect(self._bubble)
        except Exception:
            self.actions = None
            return
        tip = ("动作已保存，双击我试试 ~"
               if self.actions.binding("double_tap") else "动作已保存，右键菜单里能用了")
        self._bubble(tip, "surprise")

    def wheelEvent(self, event):
        """滚轮调整大小：向上放大、向下缩小，保持脚底位置。"""
        dy = event.angleDelta().y()
        if dy == 0:
            return
        self._scale_by(1.08 if dy > 0 else 0.92)
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        # 动作桥注册表里的动作排在最前（没有配置时这里是空的）
        bound = {}
        for label, aid in (self.actions.menu_items() if self.actions else []):
            bound[menu.addAction(label)] = aid
        if bound:
            menu.addSeparator()
        act_pause = menu.addAction("继续动画" if self.paused else "暂停动画")

        # 表情：随机开关 + 手动试一个（没有眼眶 mask 的素材整项禁用）
        has_bank = self.asset.expr_bank() is not None
        sub_expr = menu.addMenu("表情")
        sub_expr.setEnabled(has_bank)
        act_expr_rand = None
        expr_try = {}
        if has_bank:
            act_expr_rand = sub_expr.addAction("随机表情（半眼 / 惊讶 / 单侧眨眼）")
            act_expr_rand.setCheckable(True)
            act_expr_rand.setChecked(self.expr_enabled)
            sub_expr.addSeparator()
            for label, st in (("半眼", "half"), ("惊讶", "surprise"),
                              ("左眼眨一下", "wink_l"), ("右眼眨一下", "wink_r")):
                expr_try[sub_expr.addAction(label)] = st
        act_blink = None
        if self.raw_blink:
            act_blink = menu.addAction("关闭眨眼" if self.blink_enabled else "开启眨眼")
        act_gaze = menu.addAction("关闭视线跟随" if self.gaze_enabled else "开启视线跟随")
        act_land = menu.addAction("关闭落地回弹" if self.land_enabled else "开启落地回弹")
        act_hair = None
        if self.hair_field is not None and self.hair_field.ready:
            act_hair = menu.addAction("关闭头发飘动" if self.hair_enabled else "开启头发飘动")
        menu.addSeparator()
        act_bigger = menu.addAction("放大  +8%")
        act_smaller = menu.addAction("缩小  -8%")
        act_reset = menu.addAction("恢复初始大小")
        act_home = menu.addAction("复位到右下角")
        menu.addSeparator()
        act_quit = menu.addAction("退出这只桌宠")
        act_quit_all = menu.addAction("退出桌宠系统")
        menu.addSeparator()
        act_actions_cfg = menu.addAction("动作设置…")
        act = menu.exec(event.globalPos())

        if act is not None and act in bound:
            self.actions.dispatch(bound[act])
        elif act is not None and act in expr_try:
            self.arbiter.request("interaction", "expr", expr_try[act], 2500)
        elif act == act_expr_rand:
            self.expr_enabled = act_expr_rand.isChecked()
            if not self.expr_enabled:
                self.arbiter.request("interaction", "expr", "normal", 1)
        elif act == act_actions_cfg:
            self._open_action_settings()
        elif act == act_pause:
            self.paused = not self.paused
            self.update()
        elif act is not None and act == act_blink:
            self.blink_enabled = not self.blink_enabled
            if not self.blink_enabled:
                self.blink_start = None
        elif act == act_gaze:
            self.gaze_enabled = not self.gaze_enabled
            self.update()
        elif act == act_land:
            self.land_enabled = not self.land_enabled
        elif act is not None and act == act_hair:
            self.hair_enabled = not self.hair_enabled
            self.update()
        elif act == act_bigger:
            self._scale_by(1.08)
        elif act == act_smaller:
            self._scale_by(0.92)
        elif act == act_reset:
            self._rebuild(self.initial_height, keep_anchor=True)
        elif act == act_home:
            self.vel = [0.0, 0.0]
            self._place_bottom_right()
        elif act == act_quit:
            self.close()              # 只关这一只，其他桌宠继续留着
        elif act == act_quit_all:
            self._quit_all()

    def _quit_all(self):
        """退出桌宠系统：关掉所有桌宠窗口，再结束整个程序。"""
        pack = self.pack
        windows = list(pack.windows) if pack is not None else [self]
        for w in windows:
            w.close()
        QApplication.quit()


class PetPack:
    """同进程多只桌宠：一个定时器驱动全部，每帧动画做完后再两两碰撞分离。

    碰撞按手办主体（不透明像素包围盒）判断，不按整窗矩形——窗口留了透明
    边距，按窗口算会让两只明明没碰到就互相弹开。接触后沿较小的重叠轴分开：
    左右相挤互相推开并交换一点弹性；从上方砸下来则把上面那只顶回并横向
    带一点速度滑走，不允许站在对方头顶叠罗汉。拖动中的窗口视为被抓住，
    只推开别人，自己不被推开。
    """

    def __init__(self):
        self.windows: list[PetWindow] = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.started = False

    def add(self, w: PetWindow):
        w.pack = self
        self.windows.append(w)

    def remove(self, w: PetWindow):
        """关掉一只（比如重新生成后替换旧桌宠），不影响其他桌宠。"""
        if w in self.windows:
            self.windows.remove(w)
        if not self.windows:
            self.timer.stop()
            self.started = False

    def start(self):
        if not self.windows:
            return
        if not self.started:
            self.started = True
            self.place_all()
        # 全部关掉后定时器会停，再启动新宠物时要能重新拉起来
        self.timer.start(int(1000 / max(8, max(w.fps for w in self.windows))))

    def place_new(self, w: PetWindow):
        """运行中再加入一只：贴着现有队伍的空位落地，不挪动已有宠物。"""
        if not self.started or len(self.windows) <= 1:
            return
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        others = [x for x in self.windows if x is not w]
        if not others:
            return
        gap = 10
        right = max(others, key=lambda x: x.body_rect().right())
        left = min(others, key=lambda x: x.body_rect().left())
        bw = max(1, w.body_width())
        x = right.body_rect().right() + gap
        if x + bw > g.right() - 16:                     # 右边放不下就放左边
            x = left.body_rect().left() - gap - bw
        if x < g.left() + 16:                           # 两边都放不下就居中落地
            x = g.left() + max(16, int((g.width() - bw) / 2))
        w.move(int(round(x - w.margin - w.body_local[0])), int(self._ground_y(g, w)))
        w.vel = [0.0, 0.0]

    def place_all(self):
        """初始沿地面横向铺开，避免出生就叠在同一个位置。"""
        scr = QApplication.primaryScreen()
        if scr is None or not self.windows:
            return
        g = scr.availableGeometry()
        n = len(self.windows)
        if n == 1:
            self.windows[0]._place_bottom_right()
            return
        gap = 10
        widths = [max(1, w.body_width()) for w in self.windows]
        total = sum(widths) + gap * (n - 1)
        x = g.left() + max(16, int((g.width() - total) / 2))
        for w, bw in zip(self.windows, widths):
            # body_rect() 里加了窗口透明边距 margin，落位也要减掉，
            # 否则每只都会往右偏 margin，刚铺开就互相压上
            w.move(int(round(x - w.margin - w.body_local[0])),
                   int(self._ground_y(g, w)))
            w.vel = [0.0, 0.0]
            x += bw + gap

    @staticmethod
    def _ground_y(g, w: PetWindow) -> int:
        return PetWindow._ground(g) - w.height()

    def tick(self):
        self.windows = [w for w in self.windows if w.isVisible()]
        if not self.windows:
            self.timer.stop()
            return
        for w in self.windows:
            w.tick()
        self.resolve()

    def resolve(self):
        if len(self.windows) < 2:
            return
        # 原生 Wayland 下合成器不接受客户端定位窗口，碰撞推不动，直接跳过
        if any(w._wayland for w in self.windows):
            return
        for _ in range(2):          # 两轮，三只挤在一起时也能分开
            moved = False
            for i in range(len(self.windows) - 1):
                for j in range(i + 1, len(self.windows)):
                    moved |= self._pair(self.windows[i], self.windows[j])
            if not moved:
                break

    def _pair(self, a: PetWindow, b: PetWindow) -> bool:
        ra, rb = a.body_rect(), b.body_rect()
        if not ra.intersects(rb):
            return False
        ox = min(ra.right(), rb.right()) - max(ra.left(), rb.left()) + 1
        oy = min(ra.bottom(), rb.bottom()) - max(ra.top(), rb.top()) + 1
        if ox <= 0 or oy <= 0:
            return False
        ma, mb = a._movable(), b._movable()
        if not ma and not mb:
            return False
        if ox < oy:
            self._split_x(a, b, ox, ma, mb)
        else:
            self._split_y(a, b, ox, oy)
        return True

    @staticmethod
    def _split_x(a: PetWindow, b: PetWindow, ox: int,
                 ma: bool, mb: bool):
        """左右相撞：沿水平方向分开，并把接近速度换成弹开速度。"""
        ca = a.body_rect().center().x()
        cb = b.body_rect().center().x()
        d = 1 if cb >= ca else -1       # b 在右侧时 d=+1
        push = ox + 1
        if ma and mb:
            a.shift(-d * push * 0.5, 0)
            b.shift(d * push * 0.5, 0)
        elif ma:
            a.shift(-d * push, 0)
        else:
            b.shift(d * push, 0)
        closing = (a.vel[0] - b.vel[0]) * d
        if closing > 30:
            k = min(1200.0, closing * 0.45)
            if ma:
                a.vel[0] = -d * k
            if mb:
                b.vel[0] = d * k

    def _split_y(self, a: PetWindow, b: PetWindow, ox: int, oy: int):
        """上下相压：上面的弹回上方并横向滑走，下面那只只做挤压反馈。"""
        upper, lower = (a, b) if a.body_rect().center().y() <= b.body_rect().center().y() else (b, a)
        mu, ml = upper._movable(), lower._movable()
        ru, rl = upper.body_rect(), lower.body_rect()
        if mu:
            # 抬到对方顶面之上，并给一次向上的弹跳
            upper.shift(0, rl.top() - ru.bottom() - 1)
            upper.vel[1] = -max(140.0, abs(upper.vel[1]) * 0.6)
            # 顺着两只已有的左右偏移方向滑开；完全居中时往屏幕空的一侧滑
            dx = ru.center().x() - rl.center().x()
            if dx == 0:
                scr = QApplication.primaryScreen()
                mid = scr.availableGeometry().center().x() if scr else 0
                dx = 1 if ru.center().x() < mid else -1
            sx = 1 if dx > 0 else -1
            upper.shift(sx * 4, 0)
            upper.vel[0] = sx * 260.0
            upper._bounce(0.5)
            lower._bounce(0.35)
        elif ml:
            # 被拖着的宠物压在别人头上：把下面那只横向推开
            dx = rl.center().x() - ru.center().x()
            sx = 1 if dx >= 0 else -1
            lower.shift(sx * (ox + 2), 0)
            lower.vel[0] = sx * 220.0
            lower._bounce(0.35)


def resolve_asset(path: str | None, sku: str | None, stem: str | None):
    """返回 (图片路径, manifest 条目或 None)。"""
    if path:
        return Path(path), None
    if not sku:
        raise SystemExit("需要 --asset 或 --sku")

    man_path = ROOT / "assets" / sku / "manifest.json"
    folder = man_path.parent
    if not man_path.exists():
        raise SystemExit(f"找不到 manifest：{man_path}")

    # 统一走 read_manifest：group 归一化 + v2 派生视图都在这层完成，
    # 后面的渲染代码照旧读 eyes / blink / eyes_mask 这些键。
    items = read_manifest(man_path).get("items", [])
    if stem:
        hit = [it for it in items if it["stem"] == stem]
        if not hit:
            raise SystemExit(f"{sku} 里没有 stem={stem}")
        item = hit[0]
    else:
        item = items[0]
        print(f"未指定 --stem，使用 {item['stem']}（共 {len(items)} 个可选）")

    name = next((f for f in item.get("files", []) if f.endswith(".png")), None)
    if name is None:
        raise SystemExit(f"{item['stem']} 没有 PNG 产物")
    return folder / name, item


def main():
    ap = argparse.ArgumentParser(description="PyQt6 透明置顶桌宠")
    ap.add_argument("--asset", type=str, default=None, help="直接指定透明 PNG")
    ap.add_argument("--sku", type=str, default=None, help="素材包目录名，如 bgy")
    ap.add_argument("--stem", type=str, default=None, help="素材文件名（不含扩展名）")
    ap.add_argument("--stems", type=str, default=None,
                    help="同一 sku 下多只素材，逗号分隔（与 --count 二选一）")
    ap.add_argument("--count", type=int, default=1,
                    help="同一素材复制几只在屏幕上（默认 1）")
    ap.add_argument("--height", type=int, default=0, help="显示高度（像素），0 用 manifest 值")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--no-blink", action="store_true", help="关闭眨眼（检测不到眼睛时自动关闭）")
    ap.add_argument("--no-anim", action="store_true", help="关闭呼吸与摇摆")
    ap.add_argument("--no-gaze", action="store_true", help="关闭视线跟随")
    ap.add_argument("--no-land", action="store_true", help="关闭落地回弹（松手后原地停住，只有惯性）")
    args = ap.parse_args()

    if args.stems and args.count != 1:
        raise SystemExit("--stems 与 --count 只能用一个")
    if args.stems and not args.sku:
        raise SystemExit("--stems 需要配合 --sku 使用")
    count = max(1, min(8, args.count))
    if count != args.count:
        print(f"--count {args.count} 超出范围，按 {count} 只启动")

    if args.stems:
        specs = []
        for stem in (s.strip() for s in args.stems.split(",")):
            if stem:
                specs.append(resolve_asset(None, args.sku, stem))
        if not specs:
            raise SystemExit("--stems 里没有有效的素材名")
    else:
        specs = [resolve_asset(args.asset, args.sku, args.stem)]

    loaded = []
    for png, item in specs:
        eyes = (item or {}).get("eyes")
        mask_names = (item or {}).get("eyes_mask") or []
        asset = PetAsset.load(png, eyes=eyes, blink=(item or {}).get("blink"),
                              eye_masks=[png.parent / n for n in mask_names],
                              eye_level=(item or {}).get("eye_level"),
                              active_eyes=active_eye_indices(item or {}),
                              anim=(item or {}).get("anim"))
        if item:
            a = item.get("anchor") or {}
            asset.anchor = (float(a.get("x", 0.5)), float(a.get("y", 0.99)))
        # 逐素材覆盖待机效果：{enabled:[效果id...], mood:"sleepy"}，缺省回落全局表
        asset.effects_cfg = (item or {}).get("effects")
        height = args.height or (item or {}).get("display_height_px") or 0
        loaded.append((png, eyes, asset, height, item))
    if count > 1:
        loaded = loaded * count          # 同一素材复用，只加载一次

    prefer_xcb()
    app = QApplication(sys.argv)
    pack = PetPack() if len(loaded) > 1 else None
    wins = []
    for png, eyes, asset, height, item in loaded:
        # 逐素材也可以关掉视线（manifest 里 "gaze": false），眨眼不受影响
        gaze_ok = (item or {}).get("gaze", True) is not False
        win = PetWindow(asset, height, fps=args.fps,
                        blink=not args.no_blink, animate=not args.no_anim,
                        gaze=(not args.no_gaze) and gaze_ok,
                        land=not args.no_land, pack=pack)
        win.show()
        wins.append((win, png, eyes))
    if pack:
        pack.start()

    win, png, eyes = wins[0]
    print(f"素材 {png.name}  {asset.width}×{asset.height}  显示高度 {win.display_height}px")
    src = "自动检测" if eyes is None else ("手工标注" if eyes else "手工标注·不眨眼")
    print(f"眼睛 {len(asset.eyes)} 个（{src}），"
          f"眨眼帧 {len(win.blink_pixmaps)} 张，"
          f"视线跟随 {'开' if win.gaze_enabled else '关'}，"
          f"落地回弹 {'开' if win.land_enabled else '关'}，"
          f"眨眼时间轴 {'开' if asset.blinks else '关（随机）'}")
    if pack:
        names = "、".join(p.name for _, p, _ in wins)
        print(f"多只桌宠：{len(wins)} 只同屏（{names}），已启用相互碰撞")
        if QGuiApplication.platformName().startswith("wayland"):
            print("[平台] 原生 Wayland 下窗口无法自定位，多只桌宠不会散开也不会碰撞；"
                  "去掉 QT_QPA_PLATFORM=wayland 即可自动改走 XWayland")
    print("左键单击 = 跳一下，拖动甩出 = 惯性+落地回弹，滚轮缩放，双击暂停，右键菜单")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
