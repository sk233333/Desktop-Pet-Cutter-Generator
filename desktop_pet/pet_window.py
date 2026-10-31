#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyQt6 透明置顶桌宠窗口。

加载 cutout_to_pet.py 生成的透明素材，按 anim.py 的通用动作层做
呼吸（纵向缩放）、摇摆（绕脚底旋转）和随机眨眼，并支持：

- 视线跟随：眼珠朝鼠标方向偏移（需要眼睛位置）
- 点击反应：单击时弹跳一下并连眨两下
- 拖拽甩动：拖动时记录速度，松手后按惯性滑行
- 落地回弹：松手后受重力落到屏幕底部，触地回弹并轻微压缩

用法：
    python3 desktop_pet/pet_window.py --sku bgy --stem bgy_001_white
    python3 desktop_pet/pet_window.py --asset desktop_pet/assets/bgy/bgy_001_white.png
    python3 desktop_pet/pet_window.py --sku bgy --height 320 --no-blink

操作：左键拖动/单击，滚轮缩放（保持脚底位置）；双击暂停；
右键菜单可暂停、开关眨眼/视线/落地、放大/缩小/复位、退出。
注意：Wayland 下普通客户端无法自由定位与置顶，建议 X11 会话或改用
QT_QPA_PLATFORM=xcb 运行。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QCursor, QImage, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

try:
    from .anim import PetAsset, gaze_frame
except ImportError:  # 直接以脚本方式运行
    from anim import PetAsset, gaze_frame

ROOT = Path(__file__).resolve().parent


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
                 gaze: bool = True, land: bool = True):
        super().__init__(None,
                         Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("桌宠")

        self.asset = asset
        self.raw_blink = asset.blink_frames() if blink else []
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
        self.gaze_enabled = gaze
        self.land_enabled = land
        self.gaze_cache: dict[tuple[int, int], QPixmap] = {}
        self.gaze_vec = [0.0, 0.0]     # 缓动后的虹膜偏移（素材像素）
        self.gaze_max = 3.0
        self.vel = [0.0, 0.0]          # 松手后的速度（像素/秒）
        self.bounce_start: float | None = None
        self.bounce_amp = 0.0
        self._last_global = None
        self._last_t = 0.0
        self._press_global = None
        self._press_moved = False

        self._rebuild(self.initial_height)

        self.fps = max(8, fps)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / self.fps))

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
        self.blink_pixmaps = [to_pixmap(f, height) for f in self.raw_blink]
        self.ax = self.asset.anchor[0] * self.base.width()
        self.ay = self.asset.anchor[1] * self.base.height()
        self.margin = max(20, min(80, height // 8))
        self.resize(self.base.width() + self.margin * 2,
                    self.base.height() + self.margin * 2)
        self.current = self.base
        self.gaze_cache.clear()  # 尺寸变了，视线帧要重新生成
        self.gaze_max = min(2.5, max(1.5, min(self.asset.width, self.asset.height) * 0.007))
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

    def showEvent(self, event):
        super().showEvent(event)
        self._place_bottom_right()

    # ---------- 动画 ----------
    def tick(self):
        dt = 1.0 / self.fps
        if not self.paused:
            self.elapsed += dt
            self._step_physics(dt)
            self._update_gaze(dt)
        self.current = self._pick_frame()
        self.update()

    def _blink_frame(self) -> QPixmap | None:
        """眨眼状态机；不在眨眼阶段时返回 None。"""
        if not self.blink_enabled or self.paused:
            return None
        t = self.elapsed
        if self.blink_start is None:
            if t >= self.next_blink:
                self.blink_start = t
                return self.blink_pixmaps[0]
            return None
        dt = t - self.blink_start
        if dt < 0.07:
            return self.blink_pixmaps[0]
        if dt < 0.15:
            return self.blink_pixmaps[-1]
        self.blink_start = None
        if self.pending_blinks > 0:  # 点击时的连眨
            self.pending_blinks -= 1
            self.blink_start = t
            return self.blink_pixmaps[0]
        lo, hi = self.asset.motion.blink_interval
        self.next_blink = t + random.uniform(lo, hi)
        return None

    def _pick_frame(self) -> QPixmap:
        """眨眼优先（闭眼时视线没有意义），否则用逐帧缓动后的虹膜偏移。"""
        frame = self._blink_frame()
        if frame is not None:
            return frame
        if self.gaze_enabled and self.asset.eyes and self._eyes_ok():
            return self._gaze_pixmap(int(round(self.gaze_vec[0])),
                                     int(round(self.gaze_vec[1])))
        return self.base

    def _eyes_ok(self) -> bool:
        """自动检测偶尔会把一大块头发当成眼睛，尺寸不合理时不做视线跟随。"""
        w, h = self.asset.width, self.asset.height
        return all(0.01 * w <= e.rx <= 0.12 * w and 0.01 * h <= e.ry <= 0.18 * h
                   for e in self.asset.eyes)

    def _gaze_target(self) -> tuple[float, float]:
        """鼠标方向 -> 目标偏移（素材像素）。

        距离用 smoothstep 缓入，而不是一过死区就直接给满偏移，
        这样鼠标缓慢移动时虹膜也是缓慢跟着走。
        """
        if not self.gaze_enabled or not self.asset.eyes or not self._eyes_ok():
            return 0.0, 0.0
        cur = QCursor.pos()
        s = self.display_height / max(1, self.asset.height)
        m = self.margin
        ex = sum(e.cx for e in self.asset.eyes) / len(self.asset.eyes)
        ey = sum(e.cy for e in self.asset.eyes) / len(self.asset.eyes)
        dx = cur.x() - (ex * s + m + self.x())
        dy = cur.y() - (ey * s + m + self.y())
        mag = math.hypot(dx, dy)
        dead = max(14.0, self.display_height * 0.06)
        if mag <= dead:
            return 0.0, 0.0
        ramp = max(60.0, self.display_height * 0.45)
        t = min(1.0, (mag - dead) / ramp)
        t = t * t * (3.0 - 2.0 * t)          # smoothstep
        return ((dx / mag) * t * self.gaze_max,
                (dy / mag) * t * self.gaze_max * 0.7)

    def _update_gaze(self, dt: float):
        """把虹膜偏移按一阶缓动逼近目标，避免一帧内从 0 跳到最大偏移。"""
        tx, ty = self._gaze_target()
        a = 1.0 - math.exp(-dt / 0.18)       # 时间常数 0.18s
        self.gaze_vec[0] += (tx - self.gaze_vec[0]) * a
        self.gaze_vec[1] += (ty - self.gaze_vec[1]) * a
        for i in (0, 1):
            if abs(self.gaze_vec[i]) < 0.06:
                self.gaze_vec[i] = 0.0

    def _gaze_pixmap(self, gx: int, gy: int) -> QPixmap:
        key = (gx, gy)
        pm = self.gaze_cache.get(key)
        if pm is None:
            if gx == 0 and gy == 0:
                pm = self.base
            else:
                pm = to_pixmap(gaze_frame(self.asset.rgba, self.asset.eyes,
                                          gx, gy),
                               self.display_height)
            if len(self.gaze_cache) >= 32:
                self.gaze_cache.pop(next(iter(self.gaze_cache)))  # 丢最老的
            self.gaze_cache[key] = pm
        return pm

    # ---------- 物理：甩动惯性 + 落地回弹 ----------
    def _step_physics(self, dt: float):
        if self.drag is not None:
            return
        vx, vy = self.vel
        if abs(vx) < 1e-3 and abs(vy) < 1e-3:
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
        painter.drawPixmap(QPointF(m, m), self.current)
        painter.end()

    # ---------- 交互 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
            self.drag = gp - self.frameGeometry().topLeft()
            self.vel = [0.0, 0.0]
            self._last_global = gp
            self._last_t = time.monotonic()
            self._press_global = gp
            self._press_moved = False
            self.pending_blinks = 0
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
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
            self.move(gp - self.drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.drag = None
        moved = self._press_moved
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
            # 甩出去：保留甩动速度，交给惯性 + 重力
            self.vel[0] = max(-1600.0, min(1600.0, self.vel[0]))
            self.vel[1] = max(-1600.0, min(1600.0, self.vel[1]))
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.paused = not self.paused
            self.update()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        """滚轮调整大小：向上放大、向下缩小，保持脚底位置。"""
        dy = event.angleDelta().y()
        if dy == 0:
            return
        self._scale_by(1.08 if dy > 0 else 0.92)
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_pause = menu.addAction("继续动画" if self.paused else "暂停动画")
        act_blink = None
        if self.raw_blink:
            act_blink = menu.addAction("关闭眨眼" if self.blink_enabled else "开启眨眼")
        act_gaze = menu.addAction("关闭视线跟随" if self.gaze_enabled else "开启视线跟随")
        act_land = menu.addAction("关闭落地回弹" if self.land_enabled else "开启落地回弹")
        menu.addSeparator()
        act_bigger = menu.addAction("放大  +8%")
        act_smaller = menu.addAction("缩小  -8%")
        act_reset = menu.addAction("恢复初始大小")
        act_home = menu.addAction("复位到右下角")
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act = menu.exec(event.globalPos())

        if act == act_pause:
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
            self.close()
            QApplication.quit()


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

    items = json.loads(man_path.read_text(encoding="utf-8")).get("items", [])
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
    ap.add_argument("--height", type=int, default=0, help="显示高度（像素），0 用 manifest 值")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--no-blink", action="store_true", help="关闭眨眼（检测不到眼睛时自动关闭）")
    ap.add_argument("--no-anim", action="store_true", help="关闭呼吸与摇摆")
    ap.add_argument("--no-gaze", action="store_true", help="关闭视线跟随")
    ap.add_argument("--no-land", action="store_true", help="关闭落地回弹（松手后原地停住，只有惯性）")
    args = ap.parse_args()

    png, item = resolve_asset(args.asset, args.sku, args.stem)
    eyes = (item or {}).get("eyes")
    asset = PetAsset.load(png, eyes=eyes)
    if item:
        a = item.get("anchor") or {}
        asset.anchor = (float(a.get("x", 0.5)), float(a.get("y", 0.99)))
    height = args.height or (item or {}).get("display_height_px") or 0

    app = QApplication(sys.argv)
    win = PetWindow(asset, height, fps=args.fps,
                    blink=not args.no_blink, animate=not args.no_anim,
                    gaze=not args.no_gaze, land=not args.no_land)
    win.show()

    print(f"素材 {png.name}  {asset.width}×{asset.height}  显示高度 {win.display_height}px")
    src = "自动检测" if eyes is None else ("手工标注" if eyes else "手工标注·不眨眼")
    print(f"眼睛 {len(asset.eyes)} 个（{src}），"
          f"眨眼帧 {len(win.blink_pixmaps)} 张，"
          f"视线跟随 {'开' if win.gaze_enabled else '关'}，"
          f"落地回弹 {'开' if win.land_enabled else '关'}")
    print("左键单击 = 跳一下，拖动甩出 = 惯性+落地回弹，滚轮缩放，双击暂停，右键菜单")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
