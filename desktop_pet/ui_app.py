#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyQt6 单窗口操作界面：选图 -> 框取 -> 选点 -> 预览 -> 框眼睛 -> 生成 -> 桌宠。

所有交互都在一个窗口里完成（图片缩放/拖动、拖框、左右键标点、四联预览切换），
不再弹 matplotlib 窗口。

单独运行：
    python3 desktop_pet/ui_app.py
从 launch.py 进入：
    python3 desktop_pet/launch.py --ui
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")  # UI 不用 pyplot，避免抢 Tk 后端

import numpy as np
from PIL import Image

from PyQt6.QtCore import QEventLoop, QLineF, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QPixmap,
                         QTextCursor)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                             QComboBox, QFileDialog, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch  # noqa: E402  （复用掩膜估计、manifest、眼睛标注等函数）
from cutout_to_pet import DEFAULT_CANON, DEFAULT_OUT, build_one  # noqa: E402
from anim import PetAsset  # noqa: E402
from pet_window import PetWindow  # noqa: E402

SKIP = object()


class Cancelled(Exception):
    """用户在交互中按了 ESC。"""


# ------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------
def qimage(arr: np.ndarray) -> QImage:
    """numpy -> QImage（灰度或 RGB8）。"""
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        fmt, bpl = QImage.Format.Format_Grayscale8, arr.strides[0]
    else:
        h, w = arr.shape[:2]
        fmt, bpl = QImage.Format.Format_RGB888, arr.strides[0]
    return QImage(arr.data, w, h, bpl, fmt).copy()


def point_arrays(points):
    pts = np.array([[p[0], p[1]] for p in points], np.float32)
    labels = np.array([p[2] for p in points], np.int32)
    return pts, labels


class LogWriter:
    def __init__(self, edit: QPlainTextEdit):
        self.edit = edit

    def write(self, text: str) -> int:
        text = text.rstrip("\n")
        if text:
            self.edit.appendPlainText(text)
            self.edit.moveCursor(QTextCursor.MoveOperation.End)
        return len(text)

    def flush(self):
        pass


# ------------------------------------------------------------------
# 画布：显示图片 + 框取/标点/预览
# ------------------------------------------------------------------
class ImageCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QColor("#2b2b2b"))

        self.pix_item = None
        self._base_pixmap: QPixmap | None = None
        self._previews: dict[str, QPixmap] = {}
        self.done = None          # 由外部赋值的回调（value -> None）
        self.mode = "view"
        self.allow_skip = False
        self.full_key = True
        self.box_color = "lime"

        self._box: tuple | None = None
        self._corners: list[list[int]] = []
        self._points: list[list[int]] = []
        self._overlays: list = []
        self._drag_start = None
        self._drag_rect = None
        self._panning = False
        self._pan_pos = QPointF()

    # ---------- 图片 ----------
    def set_image(self, rgb: np.ndarray):
        self._base_pixmap = QPixmap.fromImage(qimage(rgb))
        self._scene.clear()
        self._overlays = []
        self.pix_item = self._scene.addPixmap(self._base_pixmap)
        h, w = rgb.shape[:2]
        self._scene.setSceneRect(0, 0, w, h)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def fit(self):
        if self.pix_item is not None:
            self.fitInView(self._scene.sceneRect(),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        """滚轮缩放（以鼠标为锚点），缩放范围 0.05x ~ 12x。"""
        dy = event.angleDelta().y()
        if dy == 0:
            return
        factor = 1.15 if dy > 0 else 1 / 1.15
        scale = self.transform().m11()
        if not 0.05 <= scale * factor <= 12:
            return
        self.scale(factor, factor)
        event.accept()

    def handle_preview_key(self, key: str):
        if key in self._previews and self.pix_item is not None:
            self.pix_item.setPixmap(self._previews[key])

    # ---------- 阶段 ----------
    def enter_mode(self, mode: str, box=None, points=None,
                   allow_skip=False, full_key=True, color="lime", on_done=None):
        """清空旧标注，进入指定交互模式。on_done(value) 在确认/取消/跳过时调用。"""
        self.mode = mode
        self.allow_skip = allow_skip
        self.full_key = full_key
        self.box_color = color
        self.done = on_done
        self._corners = []
        self._points = [list(p) for p in points] if points else []
        self._drag_start = None
        self._drag_rect = None
        self._clear_overlays()
        if mode != "preview" and self._base_pixmap is not None:
            self.pix_item.setPixmap(self._base_pixmap)
        if box:
            self._box = tuple(int(v) for v in box)
            self._draw_box(self._box)
        else:
            self._box = None
        if mode == "points":
            for i, p in enumerate(self._points, 1):
                self._draw_point(p[0], p[1], p[2], i)
        if mode == "view":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        elif mode == "preview":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFocus()

    def set_idle(self):
        self.mode = "view"
        self.done = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    # ---------- 标注绘制 ----------
    def _clear_overlays(self):
        for it in self._overlays:
            if it.scene() is not None:
                self._scene.removeItem(it)
        self._overlays = []
        self._drag_rect = None

    def _draw_box(self, rect, dashed=False):
        x0, y0, x1, y1 = rect
        pen = QPen(QColor(self.box_color), 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        item = self._scene.addRect(QRectF(x0, y0, x1 - x0, y1 - y0), pen)
        self._overlays.append(item)

    def _draw_corner(self, x, y):
        item = self._scene.addEllipse(QRectF(x - 5, y - 5, 10, 10),
                                      QPen(QColor("yellow"), 1),
                                      QColor("yellow"))
        self._overlays.append(item)

    def _draw_point(self, x, y, label, idx):
        color = QColor("#ff3b30") if label == 1 else QColor("#2f7bff")
        pen = QPen(color, 2)
        r = 6
        for a, b in (((x - r, y - r), (x + r, y + r)),
                     ((x - r, y + r), (x + r, y - r))):
            item = self._scene.addLine(QLineF(a[0], a[1], b[0], b[1]), pen)
            self._overlays.append(item)
        txt = QGraphicsSimpleTextItem(("F" if label == 1 else "B") + str(idx))
        txt.setBrush(color)
        txt.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        txt.setPos(x + 8, y - 20)
        self._scene.addItem(txt)
        self._overlays.append(txt)

    def _redraw_points(self):
        # 只保留框，重画所有点
        self._clear_overlays()
        if self._box:
            self._draw_box(self._box, dashed=True)
        for i, p in enumerate(self._points, 1):
            self._draw_point(p[0], p[1], p[2], i)

    # ---------- 鼠标 ----------
    def _pos(self, event):
        p = self.mapToScene(event.position().toPoint())
        return int(round(p.x())), int(round(p.y()))

    def _flash(self, msg: str):
        """画布左上角弹一条短提示（不随缩放变形）。"""
        if not hasattr(self, "_flash_label"):
            self._flash_label = QLabel(self.viewport())
            self._flash_label.setStyleSheet(
                "background:rgba(0,0,0,190);color:#ffd166;"
                "padding:4px 8px;border-radius:4px;")
            self._flash_label.hide()
        self._flash_label.setText(msg)
        self._flash_label.adjustSize()
        self._flash_label.move(10, 10)
        self._flash_label.show()
        self._flash_label.raise_()
        QTimer.singleShot(1600, self._flash_label.hide)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.mode == "points":
            if event.button() in (Qt.MouseButton.LeftButton,
                                  Qt.MouseButton.RightButton):
                x, y = self._pos(event)
                if self._box and not (self._box[0] <= x <= self._box[2]
                                      and self._box[1] <= y <= self._box[3]):
                    self._flash("这点在绿框外，会被裁掉 —— 请在绿框内标点")
                    return
                label = 1 if event.button() == Qt.MouseButton.LeftButton else 0
                self._points.append([x, y, label])
                self._draw_point(x, y, label, len(self._points))
            return
        if self.mode in ("box", "eyes") and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._pos(event)
            self._drag_rect = self._scene.addRect(
                QRectF(self._drag_start[0], self._drag_start[1], 0, 0),
                QPen(QColor(self.box_color), 1, Qt.PenStyle.DashLine))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_pos
            self._pan_pos = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            return
        if self.mode in ("box", "eyes") and self._drag_start and self._drag_rect:
            x, y = self._pos(event)
            self._drag_rect.setRect(QRectF(QPointF(*self._drag_start),
                                           QPointF(x, y)).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            return
        if self.mode in ("box", "eyes") and self._drag_start:
            start = self._drag_start
            self._drag_start = None
            if self._drag_rect is not None:
                self._scene.removeItem(self._drag_rect)
                self._drag_rect = None
            end = self._pos(event)
            if abs(end[0] - start[0]) > 6 or abs(end[1] - start[1]) > 6:
                self._corners = []
                self._box = (min(start[0], end[0]), min(start[1], end[1]),
                             max(start[0], end[0]), max(start[1], end[1]))
                self._clear_overlays()
                self._draw_box(self._box)
            else:
                self._corners.append([start[0], start[1]])
                if len(self._corners) >= 2:
                    (ax, ay), (bx, by) = self._corners[-2:]
                    self._corners = []
                    self._box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
                    self._clear_overlays()
                    self._draw_box(self._box)
                else:
                    self._draw_corner(start[0], start[1])
            return
        super().mouseReleaseEvent(event)

    # ---------- 键盘 ----------
    def keyPressEvent(self, event):
        k = event.key()
        if self.mode == "preview":
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish("ok")
            elif k == Qt.Key.Key_A:
                self._finish("refine")
            elif k == Qt.Key.Key_Escape:
                self._finish("cancel")
            return
        if self.mode not in ("box", "eyes", "points"):
            super().keyPressEvent(event)
            return

        if k == Qt.Key.Key_Escape:
            self._finish(None)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.mode == "box" or self.mode == "eyes":
                if self._box:
                    self._finish(self._box)
            elif self.mode == "points":
                self._finish([list(p) for p in self._points])
        elif k == Qt.Key.Key_R:
            if self.mode == "points":
                self._points.clear()
                self._redraw_points()
            else:
                self._corners.clear()
                self._box = None
                self._clear_overlays()
        elif k == Qt.Key.Key_U:
            if self.mode == "points":
                if self._points:
                    self._points.pop()
                self._redraw_points()
            else:
                if self._box:
                    self._box = None
                    self._clear_overlays()
                elif self._corners:
                    self._corners.pop()
                    self._clear_overlays()
                    for p in self._corners:
                        self._draw_corner(*p)
        elif k == Qt.Key.Key_S and self.allow_skip:
            self._finish(SKIP)
        elif k == Qt.Key.Key_F and self.full_key and self.mode != "points":
            w = int(self._scene.sceneRect().width())
            h = int(self._scene.sceneRect().height())
            self._box = (0, 0, w, h)
            self._clear_overlays()
            self._draw_box(self._box)
            self._finish(self._box)
        else:
            super().keyPressEvent(event)

    def _finish(self, value):
        cb = self.done
        self.done = None
        if cb:
            cb(value)


# ------------------------------------------------------------------
# 主窗口
# ------------------------------------------------------------------
class LaunchUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("手办 → 桌宠")
        self.resize(1280, 860)
        self._running = False
        self._image_path: str = ""
        self._out_dir: Path | None = None
        self._pets: list[PetWindow] = []
        self._preview_buttons: list[QWidget] = []

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 1)

        # ---- 左：标题 / 提示 / 预览工具条 / 画布 ----
        self.step_label = QLabel("准备就绪")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self.step_label.setFont(f)
        self.hint_label = QLabel("选择图片后点「开始」，依次框取 → 选点 → 预览 → 框眼睛。")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color:#777;")
        left.addWidget(self.step_label)
        left.addWidget(self.hint_label)

        self.preview_bar = QWidget()
        pb = QHBoxLayout(self.preview_bar)
        pb.setContentsMargins(0, 4, 0, 4)
        self.radio_group = QButtonGroup(self)
        for key, text in (("overlay", "叠加"), ("mask", "掩膜"),
                          ("white", "白底"), ("original", "原图+框")):
            rb = QRadioButton(text)
            rb.setProperty("key", key)
            rb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if key == "overlay":
                rb.setChecked(True)
            self.radio_group.addButton(rb)
            pb.addWidget(rb)
            rb.toggled.connect(self._on_preview_radio)
        pb.addSpacing(16)
        self.btn_accept = QPushButton("采用 (回车)")
        self.btn_refine = QPushButton("返回补点 (a)")
        self.btn_abort = QPushButton("取消 (ESC)")
        for b in (self.btn_accept, self.btn_refine, self.btn_abort):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            pb.addWidget(b)
            self._preview_buttons.append(b)
        self.btn_accept.clicked.connect(lambda: self.canvas._finish("ok"))
        self.btn_refine.clicked.connect(lambda: self.canvas._finish("refine"))
        self.btn_abort.clicked.connect(lambda: self.canvas._finish("cancel"))
        self.preview_bar.setVisible(False)
        left.addWidget(self.preview_bar)

        self.canvas = ImageCanvas()
        left.addWidget(self.canvas, 1)

        # ---- 右：参数面板 ----
        right.setContentsMargins(8, 0, 0, 0)

        g_img = QGroupBox("图片")
        v = QVBoxLayout(g_img)
        self.btn_pick = QPushButton("选择图片…")
        self.btn_pick.clicked.connect(self._pick_image)
        self.lbl_image = QLabel("尚未选择")
        self.lbl_image.setWordWrap(True)
        self.lbl_image.setStyleSheet("color:#555;")
        v.addWidget(self.btn_pick)
        v.addWidget(self.lbl_image)
        right.addWidget(g_img)

        g_out = QGroupBox("输出")
        grid = QVBoxLayout(g_out)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("SKU"))
        self.ed_sku = QLineEdit("custom")
        row1.addWidget(self.ed_sku)
        grid.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("名称"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("默认：图片名_pet")
        row2.addWidget(self.ed_name)
        grid.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("素材边长"))
        self.sp_size = QSpinBox()
        self.sp_size.setRange(64, 1024)
        self.sp_size.setValue(512)
        row3.addWidget(self.sp_size)
        row3.addWidget(QLabel("显示高度"))
        self.sp_height = QSpinBox()
        self.sp_height.setRange(0, 2000)
        self.sp_height.setSpecialValueText("自动")
        row3.addWidget(self.sp_height)
        grid.addLayout(row3)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("引擎"))
        self.cb_engine = QComboBox()
        self.cb_engine.addItems(["auto", "sam", "grabcut"])
        row4.addWidget(self.cb_engine)
        row4.addWidget(QLabel("设备"))
        self.cb_device = QComboBox()
        self.cb_device.addItems(["cuda", "cpu"])
        row4.addWidget(self.cb_device)
        grid.addLayout(row4)
        right.addWidget(g_out)

        g_opt = QGroupBox("步骤开关")
        ov = QVBoxLayout(g_opt)
        self.ck_points = QCheckBox("补点（左前景 / 右背景）")
        self.ck_points.setChecked(True)
        self.ck_preview = QCheckBox("分割预览（可返回补点）")
        self.ck_preview.setChecked(True)
        self.ck_eyes = QCheckBox("定位左/右眼（跳过的眼不眨眼、不跟随视线）")
        self.ck_eyes.setChecked(True)
        self.ck_launch = QCheckBox("完成后启动桌宠")
        self.ck_launch.setChecked(True)
        for w in (self.ck_points, self.ck_preview, self.ck_eyes, self.ck_launch):
            ov.addWidget(w)
        right.addWidget(g_opt)

        self.btn_start = QPushButton("开始处理")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self._start)
        right.addWidget(self.btn_start)

        self.btn_open = QPushButton("打开素材目录")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_out_dir)
        right.addWidget(self.btn_open)

        g_log = QGroupBox("日志")
        lv = QVBoxLayout(g_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet("font-family: monospace;")
        lv.addWidget(self.log)
        right.addWidget(g_log, 1)

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        right_wrap.setFixedWidth(330)
        root.addWidget(right_wrap, 0)

    # ---------- 外部入口 ----------
    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择手办照片", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if not path:
            return
        self._image_path = path
        self.lbl_image.setText(Path(path).name)
        self.lbl_image.setToolTip(path)
        if not self.ed_name.text():
            self.ed_name.setPlaceholderText(Path(path).stem + "_pet")
        # 先预览源图
        try:
            im = Image.open(path)
            rgba = np.array(self._rotated_rgba(im))
            self.canvas.set_image(rgba[..., :3].copy())
        except Exception as exc:
            self._log(f"[错误] 读图失败：{exc}")

    def _rotated_rgba(self, im: Image.Image):
        exif = im.getexif().get(274, 1)
        rgba = im.convert("RGBA")
        if exif in (3, 6, 8):
            rgba = rgba.transpose({3: Image.ROTATE_180,
                                   6: Image.ROTATE_270,
                                   8: Image.ROTATE_90}[exif])
        return rgba

    def _open_out_dir(self):
        if not self._out_dir:
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._out_dir)))

    def _log(self, text: str):
        self.log.appendPlainText(text)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _step(self, title: str, hint: str = ""):
        self.step_label.setText(title)
        if hint:
            self.hint_label.setText(hint)
        QApplication.processEvents()

    # ---------- 交互等待 ----------
    def _run_phase(self, mode: str, hint: str, box=None, points=None,
                   allow_skip=False, full_key=True, color="lime"):
        loop = QEventLoop()
        result = {"v": None}

        def on_done(value):
            result["v"] = value
            if loop.isRunning():
                loop.quit()

        self.canvas.enter_mode(mode, box=box, points=points,
                               allow_skip=allow_skip, full_key=full_key,
                               color=color, on_done=on_done)
        self.hint_label.setText(hint)
        self.canvas.setFocus()
        loop.exec()
        self.canvas.set_idle()
        return result["v"]

    # ---------- 预览 ----------
    def _preview(self, rgb, box, mask):
        self._build_previews(rgb, box, mask)
        self.preview_bar.setVisible(True)
        self.step_label.setText("第 3 / 4 步 · 预览分割")
        act = self._run_phase(
            "preview",
            "回车 = 采用并继续；a = 返回补点；ESC = 取消；上方可切换四种视图",
            box=box)
        self.preview_bar.setVisible(False)
        return act

    def _build_previews(self, rgb, box, mask):
        x0, y0, x1, y1 = box

        def with_box(pix: QPixmap) -> QPixmap:
            out = pix.copy()
            p = QPainter(out)
            p.setPen(QPen(QColor("#39ff14"), 2))
            p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            p.end()
            return out

        base = QPixmap.fromImage(qimage(rgb))
        mask_img = qimage((mask * 255).astype(np.uint8))
        tint = np.zeros_like(rgb)
        tint[..., 1] = 255
        lum = (rgb.astype(np.float32) * np.array([0.299, 0.587, 0.114])).sum(axis=2)
        dim = np.repeat((lum * 0.45)[..., None], 3, axis=2)  # 背景压暗成灰，一眼能分清
        ov = np.where(mask[..., None] > 0,
                      rgb.astype(np.float32) * 0.55 + tint * 0.45, dim)
        white = np.full_like(rgb, 255)
        white[mask > 0] = rgb[mask > 0]
        self.canvas._previews = {
            "original": with_box(base),
            "mask": QPixmap.fromImage(mask_img),
            "overlay": QPixmap.fromImage(qimage(ov.clip(0, 255).astype(np.uint8))),
            "white": QPixmap.fromImage(qimage(white)),
        }
        self.canvas.handle_preview_key("overlay")
        self.radio_group.buttons()[0].setChecked(True)

    def _on_preview_radio(self, checked: bool):
        if not checked:
            return
        rb = self.sender()
        self.canvas.handle_preview_key(rb.property("key"))

    # ---------- 主流程 ----------
    def _start(self):
        if self._running:
            return
        if not self._image_path:
            self._log("[提示] 请先选择图片")
            return
        self._running = True
        self.btn_start.setEnabled(False)
        self.log.clear()
        try:
            with contextlib.redirect_stdout(LogWriter(self.log)):
                self._flow()
        except Cancelled:
            self._log("[取消] 已停止")
            self.step_label.setText("已取消")
        except Exception as exc:  # 界面里出错不要闪退
            self._log(f"[错误] {exc}")
            self._log(traceback.format_exc())
        finally:
            self._running = False
            self.btn_start.setEnabled(True)
            self.canvas.set_idle()
            self.preview_bar.setVisible(False)

    def _require(self, value):
        if value is None:
            raise Cancelled()
        return value

    def _flow(self):
        path = self._image_path
        sku = launch.safe_stem(self.ed_sku.text().strip() or "custom")
        stem = launch.safe_stem(self.ed_name.text().strip()
                                or (Path(path).stem + "_pet"))
        source_dir = DEFAULT_CANON / sku
        out_dir = DEFAULT_OUT / sku
        source_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir = out_dir

        self._step("第 1 / 4 步 · 读图")
        print(f"[1/6] 读图 {Path(path).name}")
        im = Image.open(path)
        rgba = np.array(self._rotated_rgba(im))
        rgb = rgba[..., :3].copy()
        has_alpha = int(rgba[..., 3].min()) < 250
        self.canvas.set_image(rgb)
        print(f"    尺寸 {rgb.shape[1]}×{rgb.shape[0]}"
              + ("  含 alpha，直接用透明通道" if has_alpha else ""))

        # ---- 框取 ----
        self._step("第 1 / 4 步 · 框取手办",
                   "拖拽或左键点两个对角圈住手办；回车确认（r 重框 / u 撤销 / f 整图 / ESC 取消）")
        print("[2/6] 第 1 / 4 步 · 框取手办")
        box = self._require(self._run_phase(
            "box", "拖拽或点两个对角圈住手办，回车确认"))
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = [int(v) for v in box]
        x0, y0 = max(0, min(x0, w - 2)), max(0, min(y0, h - 2))
        x1, y1 = min(w, max(x1, x0 + 2)), min(h, max(y1, y0 + 2))
        if x1 - x0 < 8 or y1 - y0 < 8:
            self._log("[提示] 框太小了，请重新开始")
            return
        print(f"    框 [{x0},{y0},{x1},{y1}]  {x1 - x0}×{y1 - y0}")

        # ---- 选点 ----
        pts, labels = None, None
        prev_pts = []
        print("[3/6] 第 2 / 4 步 · 选点")
        if has_alpha:
            print("    已有 alpha，跳过选点")
        elif self.ck_points.isChecked():
            self._step("第 2 / 4 步 · 选点",
                       "只算绿框内的点：左键 = 前景(红)   右键 = 背景(蓝)   回车 = 分割（r / u / s / ESC）")
            res = self._run_phase(
                "points", "绿框内：左键=前景(红)，右键=背景(蓝)，回车分割",
                box=(x0, y0, x1, y1))
            if res is None:
                raise Cancelled()
            if res is not SKIP:
                prev_pts = [list(p) for p in res]
                if prev_pts:
                    pts, labels = point_arrays(prev_pts)
                    print(f"    前景 {(labels == 1).sum()} 个，"
                          f"背景 {(labels == 0).sum()} 个")
        else:
            print("    未勾选补点，跳过")

        # ---- 掩膜 + 预览循环 ----
        cache = {}
        print("[4/6] 第 3 / 4 步 · 预览分割结果")
        self._step("计算掩膜…", "正在估计前景掩膜，请稍候")
        mask = launch.estimate_mask(rgb, rgba, has_alpha, (x0, y0, x1, y1),
                                    pts, labels, self.cb_engine.currentText(),
                                    self.cb_device.currentText(), cache)
        print(f"    掩膜覆盖率 {mask.mean() * 100:.1f}%（预览里绿色=前景）")

        if self.ck_preview.isChecked():
            while True:
                act = self._preview(rgb, (x0, y0, x1, y1), mask)
                if act == "ok":
                    break
                if act != "refine":
                    raise Cancelled()
                self._step("第 3 / 4 步 · 补点精修",
                           "只算绿框内的点：左键 = 前景(红)   右键 = 背景(蓝)   回车 = 重新分割（r / u / s / ESC）")
                res = self._run_phase(
                    "points", "绿框内补点后回车重新分割",
                    box=(x0, y0, x1, y1), points=prev_pts)
                if res is None:
                    raise Cancelled()
                if res is SKIP:
                    print("    不再补点，沿用当前掩膜")
                    break
                prev_pts = [list(p) for p in res]
                if prev_pts:
                    pts, labels = point_arrays(prev_pts)
                    print(f"    前景 {(labels == 1).sum()} 个，"
                          f"背景 {(labels == 0).sum()} 个")
                else:
                    pts, labels = None, None
                self._step("重新计算掩膜…", "正在按补点重新估计")
                mask = launch.estimate_mask(rgb, rgba, has_alpha,
                                            (x0, y0, x1, y1), pts, labels,
                                            self.cb_engine.currentText(),
                                            self.cb_device.currentText(), cache)
                print(f"    掩膜覆盖率 {mask.mean() * 100:.1f}%"
                      f"（补点后重新计算）")
        else:
            print("    未勾选预览，直接采用当前掩膜")

        # ---- 定位左眼 / 右眼 ----
        print("[5/6] 第 4 / 4 步 · 定位左眼 / 右眼（用于视线与眨眼）")
        eyes_boxes = []
        eyes_manual = False
        if self.ck_eyes.isChecked():
            eyes_manual = True
            for idx, name in ((1, "左眼"), (2, "右眼")):
                self._step(f"第 4 / 4 步 · 定位{name}（{idx}/2）",
                           f"框住{name}的整个眼窝（含眼白）回车确认；s = 跳过{name}；r 重框 / u 撤销 / ESC 取消")
                res = self._run_phase(
                    "eyes", f"框住{name}的整个眼窝（拖拽或点两个对角），回车确认",
                    allow_skip=True, full_key=False, color="cyan")
                if res is None:
                    raise Cancelled()
                if res is SKIP:
                    print(f"    {name}：跳过，这只眼不眨眼、不跟随视线")
                    continue
                eyes_boxes.append(tuple(int(v) for v in res))
                print(f"    {name}框 {res}")
        else:
            print("    未勾选定位眼睛，跳过（自动检测或沿用已有标注）")

        # ---- 生成 ----
        print("[6/6] 生成透明素材")
        self._step("生成素材…", "裁切、去白边、缩放并写 manifest")
        crop = rgb[y0:y1, x0:x1]
        white = np.full_like(crop, 255)
        white[mask[y0:y1, x0:x1] > 0] = crop[mask[y0:y1, x0:x1] > 0]
        src_png = source_dir / f"{stem}.png"
        src_mask = source_dir / f"{stem}_mask.png"
        Image.fromarray(white).save(src_png)
        Image.fromarray(mask[y0:y1, x0:x1] * 255).save(src_mask)

        if eyes_manual:
            eyes_path = launch.save_eyes_json(
                source_dir, stem, (x1 - x0, y1 - y0), eyes_boxes, (x0, y0),
                force=True)
            print(f"    ✓ 眼睛标注 {eyes_path.name}（{len(eyes_boxes)} 只，"
                  f"未框的眼睛不眨眼）")
        elif (source_dir / f"{stem}_eyes.json").exists():
            print(f"    · 沿用已有 {stem}_eyes.json")

        item = build_one(src_png, src_mask, source_dir / f"{stem}.json", out_dir,
                         self.sp_size.value(), edge_px=4, margin=8,
                         use_matting=True, preview=True)
        manifest = launch.update_manifest(out_dir, sku, item)
        total = len(json.loads(manifest.read_text(encoding="utf-8"))["items"])
        print(f"    ✓ {out_dir / (stem + '.png')}")
        print(f"    ✓ {manifest}（共 {total} 个素材）")
        print(f"    掩膜覆盖率 {item['coverage'] * 100:.1f}%  "
              f"手工眼睛 {len(item.get('eyes') or [])} 个")
        self.btn_open.setEnabled(True)
        self._step("完成", f"素材已生成：{out_dir}")
        self._log(f"[完成] 素材目录：{out_dir}")

        if self.ck_launch.isChecked():
            print("[启动] 桌宠窗口")
            asset = PetAsset.load(out_dir / f"{stem}.png", eyes=item.get("eyes"))
            a = item.get("anchor") or {}
            asset.anchor = (float(a.get("x", 0.5)), float(a.get("y", 0.99)))
            height = self.sp_height.value() or item.get("display_height_px") or 0
            win = PetWindow(asset, height)
            win.show()
            self._pets.append(win)
            print(f"    眼睛 {len(asset.eyes)} 个，眨眼帧 {len(win.blink_pixmaps)} 张")


def run_ui():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = LaunchUI()
    win.show()
    app.exec()


def main():
    run_ui()


if __name__ == "__main__":
    main()
