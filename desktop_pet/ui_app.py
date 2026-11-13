#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyQt6 单窗口操作界面：选图 -> 框取 -> 选点 -> 预览 -> 多边形眼眶 -> 多边形发缕 -> 生成 -> 桌宠。

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
import math
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")  # UI 不用 pyplot，避免抢 Tk 后端

import cv2
import numpy as np
from PIL import Image

from PyQt6.QtCore import (QEventLoop, QLineF, QPointF, QRectF, QSize, Qt, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QImage, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                         QTextCursor)
from PyQt6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication,
                             QCheckBox,
                             QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                             QFrame, QGridLayout, QGraphicsItem, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView, QGroupBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea,
                             QSizePolicy, QSlider, QSpinBox, QStackedWidget, QStyle,
                             QStyleOptionButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch  # noqa: E402  （复用掩膜估计、manifest、眼睛标注等函数）
from cutout_to_pet import (DEFAULT_CANON, DEFAULT_OUT, build_one,  # noqa: E402
                           boxes_from_eye_masks,
                           eye_highlight_mask, fallback_alpha,
                           save_eye_masks, sharpen_cut_edges)
from anim import (Eye, HairField, HairWisp, blink_group_counts,  # noqa: E402
                  blink_phases, blinks_from_plan, build_expr_bank,
                  build_gaze_geos,
                  checkerboard, eye_group_bbox, gaze_frame, group_eyes,
                  load_eye_masks, make_blink_frames)
from anim import PetAsset  # noqa: E402
from expr_state import ExprState  # noqa: E402
from pet_window import PetPack, PetWindow, prefer_xcb  # noqa: E402

SKIP = object()

# 眼睛处理档位：当前档位下拉框与时间轴单元格共用这套取值
EYE_LEVELS = (
    ("自动判定（按眼睛层级）", "auto"),
    ("L2 · 只移虹膜（有眼白/瞳孔）", "L2"),
    ("L1 · 整块微移（无眼白）", "L1"),
    ("L0 · 整块 1.2px（单色块）", "L0"),
)
# 时间轴单元格按「档位-眼号」写，如 L1-1、L0-2
EYE_LEVEL_SHORT = {"auto": "自动", "L2": "L2", "L1": "L1", "L0": "L0"}

# 右侧面板样式：卡通圆角卡片 + 胶囊分段控件；描边用虚线断开留呼吸感
_UI_ASSETS = Path(__file__).resolve().parent / "ui_assets"
PANEL_QSS = """
QGroupBox {
    background: #ffffff;
    border: 2px dashed #dfeaf8;
    border-radius: 22px;
    margin-top: 20px;
    padding: 18px 14px 14px 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 3px 14px;
    background: #fff1d6;
    border-radius: 12px;
    color: #9b6a1c;
    font-weight: 700;
}
QPushButton {
    background: #ffffff;
    border: 2px solid #e3ecf9;
    border-radius: 16px;
    padding: 7px 14px;
    color: #4a5568;
    font-weight: 600;
}
QPushButton:hover { background: #f3fbff; border-color: #b9ddf6; }
QPushButton:pressed { background: #e8f5ff; border-color: #a6d1ef; }
QPushButton:disabled {
    color: #b9c2d0; background: #f8fafd; border-color: #eef2f8;
}
QPushButton#primary {
    background: #ffb057; color: #ffffff; border: 2px solid #f29a3f;
    font-weight: 700;
}
QPushButton#primary:hover { background: #ffa445; border-color: #e98c2f; }
QPushButton#primary:pressed { background: #f79736; }
QPushButton#primary:disabled {
    background: #ffe2c2; border-color: #ffd4a8; color: #fffaf2;
}
QFrame#tabShell {
    background: #f0f9ff;
    border: 2px solid #dbeeff;
    border-radius: 24px;
}
QPushButton#tabPill {
    border-radius: 19px;
    padding: 4px 18px;
    font-weight: 700;
    font-size: 14px;
    color: #58708a;
    background: transparent;
    border: 2px solid transparent;
}
QPushButton#tabPill:hover { background: #e3f4ff; }
QPushButton#tabPill:checked {
    background: #62cdb8;
    color: #ffffff;
    border: 2px solid #56bda8;
}
QPushButton#tabPill:checked:hover { background: #58c1ab; }
QFrame#sideRail {
    background: #ffffff;
    border: 2px solid #eaf1fb;
    border-radius: 28px;
}
QPushButton#railBtn {
    background: transparent;
    border: 2px solid transparent;
    border-radius: 22px;
    padding: 0px;
}
QPushButton#railBtn:hover { background: #f1f9ff; border-color: #d7ecfb; }
QPushButton#railBtn:pressed { background: #e6f5ff; }
QPushButton#railBtn:checked {
    background: #e6f9f4;
    border-color: #9adccd;
}
QPushButton#railBtn:disabled { background: transparent; border-color: transparent; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #ffffff;
    border: 2px solid #e6eef9;
    border-radius: 14px;
    padding: 4px 10px;
    min-height: 22px;
    color: #4a5568;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: #cbe4f6;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: #62cdb8;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0; border: none; background: transparent;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url(@CHEVRON@);
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 2px solid #e6eef9;
    border-radius: 14px;
    padding: 4px;
    selection-background-color: #e4f7f1;
    selection-color: #2f6e64;
    outline: none;
}
QSlider::groove:horizontal {
    height: 8px;
    background: #e8f2fb;
    border-radius: 4px;
}
QSlider::sub-page:horizontal {
    background: #9adccd;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: #ffffff;
    border: 2px solid #56bda8;
}
QSlider::handle:horizontal:hover { background: #eafaf6; border-color: #3fa894; }
QSlider::handle:horizontal:pressed { background: #d8f4ed; }
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7fcff;
    border: 2px solid #e6f0fa;
    border-radius: 16px;
    gridline-color: #eff6fc;
    selection-background-color: #e4f7f1;
    selection-color: #2f6e64;
}
QHeaderView::section {
    background: #f3faff;
    border: none;
    border-bottom: 1px dashed #d8e9f6;
    padding: 6px 4px;
    font-weight: 700;
    font-size: 12px;
    color: #58708a;
}
QTableCornerButton::section { background: #f3faff; border: none; }
QProgressBar {
    background: #eaf3fb;
    border: none;
    border-radius: 5px;
    height: 10px;
}
QProgressBar::chunk {
    background: #62cdb8;
    border-radius: 5px;
}
QCheckBox { color: #4a5568; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px;
    border-radius: 9px;
    border: 2px solid #dde9f7;
    background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #9fdcce; }
QCheckBox::indicator:checked {
    background: #62cdb8;
    border-color: #56bda8;
}
QLabel { color: #4a5568; }
QPlainTextEdit {
    background: #fbfeff;
    border: 2px dashed #e2edf9;
    border-radius: 16px;
    padding: 6px;
    color: #5b6778;
}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 3px;
}
QScrollBar::handle:vertical {
    background: #d5e9f7; border-radius: 5px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #bfdcee; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
""".replace("@CHEVRON@", (_UI_ASSETS / "chevron_down.png").as_posix())

# 桌宠空间 / 回收箱共用的样式
SPACE_QSS = PANEL_QSS + """
QWidget#petSpaceWin, QWidget#petTrashWin { background: transparent; }
QFrame#spaceCard { background: #ffffff; border: 2px solid #eaf1fb; border-radius: 24px; }
QPushButton#spaceThumb { border: 2px dashed #dbe9f7; border-radius: 20px; background: #fbfeff; }
QPushButton#spaceThumb:hover { border: 2px solid #93dccb; background: #f5fdfa; }
QPushButton#spaceThumb:checked { border: 2px solid #56bda8; background: #eaf9f4; }
QPushButton#spaceBatch:checked {
    background: #e6f9f4; border-color: #9adccd; color: #2f9c88;
}
QPushButton#spaceTrashBtn {
    background: #fff8e6; border-color: #ffe4b6; color: #9b6a1c;
}
QPushButton#spaceTrashBtn:hover { background: #fff2d2; border-color: #f7d595; }
QPushButton#spaceDel { color: #e2645b; border: 2px solid #f8dad6; background: #fff8f7; padding: 3px 12px; border-radius: 12px; }
QPushButton#spaceDel:hover { background: #fdeeec; border-color: #eaada7; }
QPushButton#spaceRename { color: #4a7fa5; border: 2px solid #d9e6f5; background: #f7fbff; padding: 3px 10px; border-radius: 12px; }
QPushButton#spaceRename:hover { background: #eef7ff; border-color: #b9d6ef; }
QPushButton#trashRestore { color: #2f9c88; border: 2px solid #cbece5; background: #f4fdfa; padding: 3px 12px; border-radius: 12px; }
QPushButton#trashRestore:hover { background: #e6f9f4; border-color: #9adccd; }
QPushButton#trashClear { color: #d9534a; border: 2px solid #f6d3cf; background: #fff7f6; padding: 5px 14px; border-radius: 16px; font-weight: 700; }
QPushButton#trashClear:hover { background: #fdecea; border-color: #eaada7; }
QLabel#spaceTitle { font-size: 22px; font-weight: 800; color: #3e4a5c; }
QLabel#spaceSub { color: #8d99a8; }
"""

# 卡通插画底：奶油天空 + 云朵/山丘 + 马卡龙色斑 + 断点圆环 + 小星星爪印
SKY_TOP = QColor("#e9f7ff")
SKY_MID = QColor("#fffaf3")
SKY_BOT = QColor("#ffefdd")


def _star_path(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        x, y = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _draw_paw(p: QPainter, cx: float, cy: float, s: float, color: QColor):
    """小爪印：一个肉垫 + 四颗脚趾，作为背景里的低对比装饰。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy + s * 0.35), s * 0.62, s * 0.50)
    for dx, dy in ((-0.82, -0.28), (-0.32, -0.70), (0.32, -0.70), (0.82, -0.28)):
        p.drawEllipse(QPointF(cx + dx * s, cy + dy * s), s * 0.28, s * 0.33)


def _rail_icon(kind: str, color: str, size: int = 48) -> QIcon:
    """右侧竖条的线性小图标：tune 动作参数 / crop 框取 / eye 眼睛 / hair 头发 / spark 生成 / folder 目录。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    k = size / 48.0
    p.scale(k, k)
    if kind == "tune":
        # 三根滑杆：对应「动作参数」里的飘动 / 回弹 / 摇摆
        for y, dx in ((13, 8), (24, -6), (35, 4)):
            p.drawLine(QPointF(8, y), QPointF(40, y))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(24 + dx, y), 3.8, 3.8)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == "cloth":
        # 小衣服：短袖 + 领口，代表衣摆 / 小物件的飘动
        body = QPainterPath(QPointF(18, 11))
        body.lineTo(30, 11)
        body.lineTo(36, 15)
        body.lineTo(33, 21)
        body.lineTo(30, 19)
        body.lineTo(30, 38)
        body.lineTo(18, 38)
        body.lineTo(18, 19)
        body.lineTo(15, 21)
        body.lineTo(12, 15)
        body.closeSubpath()
        p.drawPath(body)
        neck = QPainterPath(QPointF(21, 11))
        neck.lineTo(24, 15)
        neck.lineTo(27, 11)
        p.drawPath(neck)
    elif kind == "crop":
        p.drawLine(QPointF(17, 7), QPointF(17, 31))
        p.drawLine(QPointF(17, 31), QPointF(41, 31))
        p.drawLine(QPointF(7, 17), QPointF(31, 17))
        p.drawLine(QPointF(31, 17), QPointF(31, 41))
    elif kind == "eye":
        path = QPainterPath(QPointF(6, 24))
        path.quadTo(QPointF(24, 7), QPointF(42, 24))
        path.quadTo(QPointF(24, 41), QPointF(6, 24))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(24, 24), 5.4, 5.4)
    elif kind == "hair":
        for dx in (-11, 0, 11):
            path = QPainterPath(QPointF(24 + dx, 9))
            path.cubicTo(QPointF(24 + dx - 7, 19),
                         QPointF(24 + dx + 7, 27), QPointF(24 + dx, 39))
            p.drawPath(path)
    elif kind == "table":
        # 小桌子 + 向下的箭头：桌子模式（重力）
        top = QPainterPath()
        top.addRoundedRect(QRectF(6, 18, 36, 6), 3, 3)
        p.drawPath(top)
        p.drawLine(QPointF(14, 24), QPointF(14, 39))
        p.drawLine(QPointF(34, 24), QPointF(34, 39))
        p.drawLine(QPointF(24, 5), QPointF(24, 13))
        p.drawLine(QPointF(20, 9), QPointF(24, 13))
        p.drawLine(QPointF(28, 9), QPointF(24, 13))
    elif kind == "save":
        # 常用软盘保存图标：生成并保存素材
        body = QPainterPath(QPointF(9, 8))
        body.lineTo(31, 8)
        body.lineTo(39, 16)
        body.lineTo(39, 40)
        body.lineTo(9, 40)
        body.closeSubpath()
        p.drawPath(body)
        p.drawRect(QRectF(17, 8, 13, 8))
        p.drawRect(QRectF(16, 27, 16, 13))
    elif kind == "spark":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(_star_path(23, 26, 15))
        p.setBrush(QColor(color).lighter(135))
        p.drawEllipse(QPointF(38, 11), 3.4, 3.4)
    elif kind == "folder":
        p.drawLine(QPointF(7, 14), QPointF(19, 14))
        p.drawLine(QPointF(19, 14), QPointF(23, 18))
        path = QPainterPath()
        path.addRoundedRect(QRectF(7, 18, 34, 21), 4, 4)
        p.drawPath(path)
    elif kind == "house":
        # 小房子：屋顶 + 墙体 + 门，代表「桌宠空间」
        roof = QPainterPath(QPointF(6, 23))
        roof.lineTo(24, 7)
        roof.lineTo(42, 23)
        p.drawPath(roof)
        p.drawLine(QPointF(13, 20), QPointF(13, 41))
        p.drawLine(QPointF(35, 20), QPointF(35, 41))
        p.drawLine(QPointF(13, 41), QPointF(35, 41))
        p.drawRect(QRectF(21, 31, 7, 10))
    p.end()
    icon = QIcon(pm)
    # 禁用态自动变淡，和按钮的其他灰态一致
    dim = QPixmap(pm.size())
    dim.fill(Qt.GlobalColor.transparent)
    dp = QPainter(dim)
    dp.setOpacity(0.32)
    dp.drawPixmap(0, 0, pm)
    dp.end()
    icon.addPixmap(dim, QIcon.Mode.Disabled)
    return icon


def _save_icon(color: str = "#4a7fa5", size: int = 40) -> QIcon:
    """常用的「保存」软盘图标：给预览工具条的保存按钮用。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 2.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.scale(size / 32.0, size / 32.0)
    body = QPainterPath(QPointF(6, 5))
    body.lineTo(21, 5)
    body.lineTo(27, 11)
    body.lineTo(27, 27)
    body.lineTo(6, 27)
    body.closeSubpath()
    p.drawPath(body)
    p.drawRect(QRectF(11, 5, 7, 6))      # 上方滑盖
    p.drawRect(QRectF(10, 17, 13, 10))   # 下方标签
    p.end()
    return QIcon(pm)


def paint_cute_backdrop(p: QPainter, rect, accented: bool = True):
    """给整窗铺一层卡通插画底。

    元素都压在 15%~40% 透明度，只负责「蓬松感」，不抢卡片里的信息；
    圆环用 RoundCap + 大间隔的虚线，对应「描边不画满、留呼吸感」。
    """
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.save()
    grad = QLinearGradient(QPointF(rect.left(), rect.top()),
                           QPointF(rect.left(), rect.bottom()))
    grad.setColorAt(0.0, SKY_TOP)
    grad.setColorAt(0.55, SKY_MID)
    grad.setColorAt(1.0, SKY_BOT)
    p.fillRect(rect, grad)
    w, h = max(1, rect.width()), max(1, rect.height())
    base = float(min(w, h))
    p.setPen(Qt.PenStyle.NoPen)

    # 大块柔和色斑（马卡龙蓝 / 奶油黄 / 樱花粉 / 薄荷）
    for cx, cy, rr, col in (
        (0.12, 0.10, 0.30, QColor(126, 200, 242, 40)),
        (0.90, 0.16, 0.22, QColor(255, 209, 102, 34)),
        (0.05, 0.90, 0.26, QColor(255, 160, 176, 28)),
        (0.94, 0.86, 0.20, QColor(98, 205, 184, 30)),
    ):
        p.setBrush(col)
        p.drawEllipse(QPointF(rect.left() + w * cx, rect.top() + h * cy),
                      base * rr, base * rr * 0.86)

    # 云朵
    p.setBrush(QColor(255, 255, 255, 128))
    for cx, cy, s in ((0.10, 0.14, 0.10), (0.72, 0.07, 0.075), (0.42, 0.94, 0.085)):
        x, y = rect.left() + w * cx, rect.top() + h * cy
        for dx, dy, rs in ((-1.1, 0.15, 0.62), (0.0, -0.10, 0.85), (1.1, 0.15, 0.60)):
            p.drawEllipse(QPointF(x + dx * base * s, y + dy * base * s),
                          base * s * rs, base * s * rs * 0.8)

    # 底部圆润山丘
    p.setBrush(QColor(146, 219, 173, 40))
    p.drawEllipse(QPointF(rect.left() + w * 0.16, rect.bottom() + base * 0.12),
                  w * 0.52, base * 0.26)
    p.setBrush(QColor(126, 200, 242, 32))
    p.drawEllipse(QPointF(rect.left() + w * 0.86, rect.bottom() + base * 0.14),
                  w * 0.48, base * 0.24)

    if accented:
        # 断点圆环：粗圆线 + 大间隔虚线，给角落留白而不是一圈实线
        ring = QPen(QColor(255, 255, 255, 170), max(5.0, base * 0.012))
        ring.setCapStyle(Qt.PenCapStyle.RoundCap)
        ring.setDashPattern([3.6, 3.0])
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rr = base * 0.16
        p.drawArc(QRectF(rect.left() + w * 0.72 - rr, rect.top() + h * 0.16 - rr,
                         rr * 2, rr * 2), 40 * 16, 250 * 16)
        rr2 = base * 0.11
        p.drawArc(QRectF(rect.left() + w * 0.16 - rr2,
                         rect.bottom() - h * 0.14 - rr2, rr2 * 2, rr2 * 2),
                  200 * 16, 220 * 16)

        # 星星与圆点
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, s, col in (
            (0.06, 0.28, 0.030, QColor(255, 209, 102, 150)),
            (0.93, 0.42, 0.021, QColor(255, 158, 173, 130)),
            (0.28, 0.86, 0.023, QColor(126, 200, 242, 140)),
            (0.66, 0.90, 0.030, QColor(146, 219, 173, 140)),
            (0.84, 0.70, 0.017, QColor(255, 209, 102, 160)),
        ):
            p.setBrush(col)
            r = base * s
            x, y = rect.left() + w * cx, rect.top() + h * cy
            if s >= 0.028:
                p.drawPath(_star_path(x, y, r))
            else:
                p.drawEllipse(QPointF(x, y), r, r)

        # 小爪印（点题：桌宠）
        _draw_paw(p, rect.left() + w * 0.20, rect.bottom() - h * 0.16,
                  base * 0.030, QColor(255, 255, 255, 150))
        _draw_paw(p, rect.left() + w * 0.82, rect.bottom() - h * 0.30,
                  base * 0.024, QColor(255, 255, 255, 150))
    p.restore()


def cute_dot_tile(size: int = 176, scale: float = 2.0) -> QPixmap:
    """画布底纹：淡蓝底 + 稀疏的马卡龙小圆点，生成一次后交给 QBrush 平铺。

    画布在桌宠预览时是逐帧重绘的，平铺一张预渲染贴图比每帧现画几十个
    圆点便宜得多；圆点位置写死，避免每次启动图案都不一样。
    """
    k = int(size * scale)
    pm = QPixmap(k, k)
    pm.fill(QColor("#f7fbff"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    pink = QColor(255, 209, 222, 235)
    sky = QColor(199, 230, 251, 235)
    mint = QColor(196, 238, 222, 230)
    cream = QColor(255, 231, 188, 235)
    lilac = QColor(218, 211, 250, 220)
    dots = (
        (0.10, 0.12, 3.2, pink), (0.36, 0.07, 2.0, sky),
        (0.62, 0.18, 4.2, mint), (0.86, 0.09, 2.6, cream),
        (0.18, 0.33, 2.6, lilac), (0.48, 0.30, 3.4, cream),
        (0.78, 0.40, 2.2, sky), (0.07, 0.55, 4.0, mint),
        (0.33, 0.60, 2.8, pink), (0.60, 0.55, 2.2, lilac),
        (0.93, 0.66, 3.0, pink), (0.22, 0.80, 3.4, cream),
        (0.52, 0.84, 2.4, sky), (0.72, 0.74, 4.0, mint),
        (0.90, 0.92, 2.6, lilac), (0.40, 0.94, 3.0, pink),
    )
    for fx, fy, r, col in dots:
        p.setBrush(col)
        p.drawEllipse(QPointF(fx * k, fy * k), r * scale, r * scale)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def paint_mascot(p: QPainter, rect):
    """空状态插画：一只圆滚滚的小猫（纯矢量，不依赖图片素材）。"""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = float(min(rect.width(), rect.height()))
    cx = rect.center().x()
    by = rect.bottom() - s * 0.04
    outline = QPen(QColor("#f0b25c"), max(2.0, s * 0.012))
    outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    # 地面阴影
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(88, 122, 148, 34))
    p.drawEllipse(QPointF(cx, by), s * 0.30, s * 0.055)

    # 尾巴
    tail = QPen(QColor("#f0b25c"), max(6.0, s * 0.055))
    tail.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(tail)
    path = QPainterPath(QPointF(cx + s * 0.16, by - s * 0.16))
    path.cubicTo(QPointF(cx + s * 0.40, by - s * 0.18),
                 QPointF(cx + s * 0.40, by - s * 0.40),
                 QPointF(cx + s * 0.26, by - s * 0.44))
    p.drawPath(path)

    # 耳朵（先画，压在头后面）
    p.setPen(outline)
    p.setBrush(QColor("#ffcf90"))
    for sx in (-1, 1):
        ear = QPolygonF([QPointF(cx + sx * s * 0.15, by - s * 0.58),
                         QPointF(cx + sx * s * 0.22, by - s * 0.74),
                         QPointF(cx + sx * s * 0.05, by - s * 0.62)])
        p.drawPolygon(ear)
        p.setBrush(QColor("#ffb9c6"))
        inner = QPolygonF([QPointF(cx + sx * s * 0.15, by - s * 0.60),
                           QPointF(cx + sx * s * 0.19, by - s * 0.69),
                           QPointF(cx + sx * s * 0.09, by - s * 0.62)])
        p.drawPolygon(inner)
        p.setBrush(QColor("#ffcf90"))

    # 身体 + 肚皮
    p.setPen(outline)
    p.setBrush(QColor("#ffd79b"))
    p.drawRoundedRect(QRectF(cx - s * 0.18, by - s * 0.34,
                             s * 0.36, s * 0.31), s * 0.14, s * 0.14)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#fff0d8"))
    p.drawEllipse(QRectF(cx - s * 0.12, by - s * 0.28, s * 0.24, s * 0.21))

    # 头
    p.setPen(outline)
    p.setBrush(QColor("#ffd79b"))
    p.drawEllipse(QRectF(cx - s * 0.22, by - s * 0.63,
                         s * 0.44, s * 0.39))

    # 眼睛 + 高光
    ey = by - s * 0.455
    p.setPen(Qt.PenStyle.NoPen)
    for sx in (-1, 1):
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(cx + sx * s * 0.088, ey), s * 0.053, s * 0.060)
        p.setBrush(QColor("#3e4a5c"))
        p.drawEllipse(QPointF(cx + sx * s * 0.088 + s * 0.008, ey + s * 0.006),
                      s * 0.031, s * 0.037)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(cx + sx * s * 0.088 + s * 0.017, ey - s * 0.008),
                      s * 0.010, s * 0.010)

    # 腮红
    p.setBrush(QColor(255, 160, 176, 120))
    for sx in (-1, 1):
        p.drawEllipse(QPointF(cx + sx * s * 0.140, ey + s * 0.070),
                      s * 0.036, s * 0.023)

    # 鼻子 + 微笑
    p.setBrush(QColor("#ff9f8a"))
    p.drawEllipse(QPointF(cx, ey + s * 0.043), s * 0.015, s * 0.011)
    smile = QPen(QColor("#e88f5c"), max(1.5, s * 0.009))
    smile.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(smile)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(cx - s * 0.055, ey + s * 0.045, s * 0.11, s * 0.055),
              200 * 16, 140 * 16)

    # 头顶呆毛
    sprout = QPen(QColor("#f0b25c"), max(2.0, s * 0.012))
    sprout.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(sprout)
    p.drawArc(QRectF(cx - s * 0.035, by - s * 0.72, s * 0.07, s * 0.06),
              90 * 16, -220 * 16)


class MascotArt(QWidget):
    """空状态里的小猫插画（固定尺寸，纯 paint 绘制）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(240, 172)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        p = QPainter(self)
        paint_mascot(p, self.rect())


class CuteCheckBox(QCheckBox):
    """勾选时在绿色圆点里补一个白色对号：只靠底色变化区分度不够。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        box = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, opt, self)
        if box.isEmpty():
            return
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(max(2.0, box.width() * 0.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        w, h = box.width(), box.height()
        tick = QPainterPath()
        tick.moveTo(box.left() + w * 0.25, box.top() + h * 0.52)
        tick.lineTo(box.left() + w * 0.42, box.top() + h * 0.71)
        tick.lineTo(box.left() + w * 0.76, box.top() + h * 0.30)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(pen)
        p.drawPath(tick)
        p.end()


class ClickOnlyMixin:
    """滚轮不修改取值：滚过时只让外层面板滚动，值只能点选或键入。

    QComboBox / QSpinBox 默认滚轮直接改值，在可滚动的右侧面板里手一滑就会把
    「当前档位」从 L2 滚成 L1、把边长滚小，所以这里把滚轮事件交回给父级。
    """

    def wheelEvent(self, event):
        event.ignore()


class ClickComboBox(ClickOnlyMixin, QComboBox):
    pass


class ClickSpinBox(ClickOnlyMixin, QSpinBox):
    pass


class ClickDoubleSpinBox(ClickOnlyMixin, QDoubleSpinBox):
    pass


class AnimSlider(QWidget):
    """动作参数：0~3.0× 拖动条 + 可手输的数字框（手输可以超过 3.0，例如 5.0×）。

    滑杆只覆盖常用区间（0 = 停止，3.0 = 上限）；数字框仍可键入更大的值，
    这时滑杆停在最右端，实际取值一律以数字框为准。
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, lo: float = 0.0, hi: float = 3.0, value: float = 1.0,
                 step: float = 0.1, suffix: str = " ×", decimals: int = 2,
                 log: bool = False, parent=None):
        super().__init__(parent)
        self._lo = float(lo)
        self._hi = float(hi)
        # log=True：滑杆按对数分布，跨越几十倍的范围也好拖（平台宽度要 10%~2000%）
        self._log = bool(log) and self._lo > 0 and self._hi > self._lo
        self._raw_max = (1000 if self._log
                         else max(1, int(round((self._hi - self._lo) * 100))))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._raw_max)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(max(1, int(round(
            self._raw_max / 20.0 if self._log else step * 100 * 2))))
        self.slider.setMinimumWidth(110)
        self.spin = ClickDoubleSpinBox()
        self.spin.setRange(self._lo, max(99.0, self._hi))   # 手输不设 3.0 的上限
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(92)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin, 0)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.setValue(value)

    def _raw_of(self, v: float) -> int:
        v = min(self._hi, max(self._lo, float(v)))
        if self._log:
            return int(round(math.log(v / self._lo) / math.log(self._hi / self._lo)
                             * self._raw_max))
        return int(round((v - self._lo) * 100))

    def _value_of(self, raw: int) -> float:
        if self._log:
            return round(self._lo * (self._hi / self._lo) ** (raw / float(self._raw_max)), 2)
        return round(self._lo + raw / 100.0, 2)

    def _from_slider(self, raw: int):
        v = self._value_of(raw)
        if abs(v - self.spin.value()) > 1e-6:
            self.spin.setValue(v)          # 触发 _from_spin -> valueChanged
        else:
            self.valueChanged.emit(float(v))

    def _from_spin(self, v: float):
        raw = self._raw_of(v)
        if self.slider.value() != raw:
            self.slider.blockSignals(True)
            self.slider.setValue(raw)
            self.slider.blockSignals(False)
        self.valueChanged.emit(float(v))

    def value(self) -> float:
        return float(self.spin.value())

    def setValue(self, v: float):
        v = float(v)
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        raw = self._raw_of(v)
        self.slider.blockSignals(True)
        self.slider.setValue(raw)
        self.slider.blockSignals(False)


class RoundIconButton(QPushButton):
    """36px 圆形图标按钮：白底浅边、悬停变薄荷绿（齿轮 / 房子共用）。"""

    def __init__(self, tip: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setObjectName("roundIcon")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tip)
        self.setStyleSheet(
            "QPushButton#roundIcon{background:#ffffff;border:2px solid #e3ecf9;"
            "border-radius:18px;}"
            "QPushButton#roundIcon:hover{background:#f3fbff;border-color:#9fdcce;}"
            "QPushButton#roundIcon:pressed{background:#e8f5ff;}")

    def _glyph(self, p: QPainter, c: QPointF, r: float):
        raise NotImplementedError

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QRectF(self.rect()).center()
        r = min(self.width(), self.height()) * 0.5
        p.setPen(QPen(QColor("#3f9c8b") if self.underMouse()
                      else QColor("#58708a")))
        self._glyph(p, c, r)
        p.end()


class GearButton(RoundIconButton):
    """齿轮：打开「设置」弹窗（输出参数都收在里面）。"""

    def __init__(self, parent=None):
        super().__init__(
            "设置：SKU / 素材名 / 素材边长 / 显示高度 / 引擎 / 设备", parent)

    def _glyph(self, p: QPainter, c: QPointF, r: float):
        # 8 颗实心方齿绕一圈 + 粗齿环 + 中心孔：经典齿轮造型。
        # 早前的「八根辐条」看着像太阳，小尺寸下还容易糊成一朵花
        x, y = c.x(), c.y()
        col = p.pen().color()
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        for i in range(8):
            a = math.radians(i * 45.0)
            p.save()
            p.translate(x + math.cos(a) * r * 0.60, y + math.sin(a) * r * 0.60)
            p.rotate(i * 45.0)
            p.drawRoundedRect(
                QRectF(-r * 0.17, -r * 0.13, r * 0.34, r * 0.26),
                r * 0.07, r * 0.07)
            p.restore()
        p.restore()
        pen = QPen(col)
        pen.setWidthF(max(2.6, r * 0.26))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, r * 0.42, r * 0.42)


def make_empty_state(text: str, hint: str = "") -> QWidget:
    """空状态引导：小猫插画 + 一行说明，别堆元素抢信息。"""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 18, 0, 18)
    lay.setSpacing(2)
    lay.addStretch(1)
    lay.addWidget(MascotArt(), 0, Qt.AlignmentFlag.AlignHCenter)
    lbl = QLabel(text)
    lbl.setObjectName("spaceSub")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    if hint:
        sub = QLabel(hint)
        sub.setObjectName("spaceSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(sub)
    lay.addStretch(1)
    return box


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


def qimage_rgba(arr: np.ndarray) -> QImage:
    """numpy RGBA -> QImage（透明通道不丢）。"""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, arr.strides[0],
                  QImage.Format.Format_RGBA8888).copy()


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


class PreviewBoard(QWidget):
    """右侧白底预览：把当前抠图结果合成到白底上。

    滚轮放大（以鼠标为中心）、左键拖动平移、双击复位，方便检查边缘。
    """

    MAX_ZOOM = 32.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self._drag = None
        self.setMinimumSize(220, 220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("滚轮放大 / 缩小，拖动平移，双击复位")

    def set_rgba(self, rgba: np.ndarray | None):
        if rgba is None:
            self.clear()
            return
        rgb = rgba[..., :3].astype(np.float32)
        a = rgba[..., 3:4].astype(np.float32) / 255.0
        out = np.clip(rgb * a + 255.0 * (1.0 - a), 0, 255).astype(np.uint8)
        self._pix = QPixmap.fromImage(qimage(out))
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self.update()

    def clear(self):
        self._pix = None
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self.update()

    def _board_rect(self):
        return self.rect().adjusted(1, 1, -1, -1)

    def _fit_scale(self, r):
        if self._pix is None or self._pix.isNull():
            return 0.0
        return min(r.width() / self._pix.width(),
                   r.height() / self._pix.height(), 1.0)

    def _geom(self, r):
        """返回 (缩放比, 左上角 x, y, 宽, 高)：先适应窗口，再叠加用户缩放。"""
        k_fit = self._fit_scale(r)
        k = k_fit * self._zoom
        w = self._pix.width() * k
        h = self._pix.height() * k
        x = r.x() + (r.width() - w) / 2.0 + self._off.x()
        y = r.y() + (r.height() - h) / 2.0 + self._off.y()
        return k, x, y, w, h

    def wheelEvent(self, event):
        if self._pix is None or self._pix.isNull():
            return
        dy = event.angleDelta().y() or event.pixelDelta().y()
        if not dy:
            return
        factor = 1.25 if dy > 0 else 0.8
        new_zoom = min(self.MAX_ZOOM, max(1.0, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        r = self._board_rect()
        k, x, y, _w, _h = self._geom(r)
        m = event.position()
        ix, iy = (m.x() - x) / k, (m.y() - y) / k   # 鼠标下的图片坐标
        self._zoom = new_zoom
        k2, cx, cy, _, _ = self._geom(r)
        self._off = QPointF(self._off.x() + (m.x() - ix * k2 - cx),
                            self._off.y() + (m.y() - iy * k2 - cy))
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if (event.button() in (Qt.MouseButton.LeftButton,
                               Qt.MouseButton.RightButton)
                and self._zoom > 1.001):
            self._drag = (event.position(), QPointF(self._off))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        p0, off0 = self._drag
        self._off = QPointF(off0.x() + event.position().x() - p0.x(),
                            off0.y() + event.position().y() - p0.y())
        self.update()

    def mouseReleaseEvent(self, _event):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, _event):
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self._board_rect()
        p.fillRect(r, QColor("#fffdf9"))
        p.setPen(QPen(QColor("#e2edf9"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 18, 18)
        if self._pix is None or self._pix.isNull():
            p.setPen(QColor("#aab0c0"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "白底预览\n（抠图后显示）")
            return
        _k, x, y, w, h = self._geom(r)
        if self._zoom > 1.001:
            p.setClipRect(r)
        p.drawPixmap(QRectF(x, y, w, h), self._pix, QRectF(self._pix.rect()))
        if self._zoom > 1.001:
            p.setClipping(False)
            p.setPen(QColor("#8f96a8"))
            p.drawText(r.adjusted(6, 4, -6, -4),
                       Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignTop,
                       f"{int(self._zoom * 100)}%")



# ------------------------------------------------------------------
# 画布：显示图片 + 框取/标点/预览
# ------------------------------------------------------------------
class AreaGlowItem(QGraphicsItem):
    """在选区上盖一层循环流动的绿光（点击表格行时标出这只眼 / 这缕头发）。

    形状用 mask 的轮廓多边形，填充一条斜向渐变亮带；相位每帧前移，
    亮带就沿固定方向扫过选区。压在桌宠 pixmap 下面当子项，跟着摇摆一起转。
    """

    def __init__(self, path: QPainterPath):
        super().__init__()
        self._path = path
        self._rect = path.boundingRect().adjusted(-1.5, -1.5, 1.5, 1.5)
        self._phase = 0.0

    def boundingRect(self) -> QRectF:
        return self._rect

    def step(self, delta: float = 0.035):
        self._phase = (self._phase + delta) % 1.0
        self.update()

    def paint(self, p: QPainter, _opt, _widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self._rect
        if r.width() < 1 or r.height() < 1:
            return
        # 斜向亮带：起点随相位从左侧扫到右侧，宽度约占选区 80%
        band = r.width() * 0.8
        x = r.left() - band + self._phase * (r.width() + band * 2)
        g = QLinearGradient(QPointF(x, r.top()),
                            QPointF(x + band, r.top() + r.height() * 0.55))
        g.setColorAt(0.0, QColor(60, 230, 145, 45))
        g.setColorAt(0.40, QColor(105, 255, 180, 135))
        g.setColorAt(0.52, QColor(220, 255, 238, 215))
        g.setColorAt(0.64, QColor(105, 255, 180, 135))
        g.setColorAt(1.0, QColor(60, 230, 145, 45))
        p.setPen(Qt.PenStyle.NoPen)
        p.save()
        p.setClipPath(self._path)
        p.fillRect(r, QBrush(g))
        p.restore()
        pen = QPen(QColor(130, 255, 195, 170))
        pen.setWidthF(max(1.0, min(r.width(), r.height()) * 0.02))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._path)


class PetPreview:
    """编辑器里那只桌宠：目光跟随鼠标 + 按时间轴眨眼 + 头发随风摆动。

    和桌面上的桌宠共用 anim 里的同一套逻辑，只是把结果直接画在画布上。
    每调一次 frame() 前进一帧：先取当前视线方向的底图（按 2px 量化缓存），
    再把闭眼补丁贴回眼框，最后过一遍发缕位移场。
    """

    def __init__(self, rgba, eyes, sockets, levels, wisps, blinks, cycle,
                 gaze_on: bool = True, hair_speed: float = 1.0,
                 hair_amp: float = 1.0, hair_stiff: float = 1.0,
                 hair_damp: float = 1.0, cloth_wisps=None):
        self.rgba = rgba
        self.eyes = eyes
        self.cycle = max(0.2, float(cycle))
        self.blinks = list(blinks)
        self.groups = group_eyes(eyes, blink_group_counts(blinks) or None)
        self.geos = []
        if gaze_on and eyes:
            try:
                self.geos = build_gaze_geos(rgba, eyes, sockets=sockets,
                                            levels=levels)
            except Exception as exc:  # 几何失败就只做眨眼与飘发
                print(f"[桌宠] 视线几何构建失败：{exc}")
        # 每个眼组的半闭 / 全闭补丁只渲一次（转灰度找肤色挺贵）
        self.patches = {}
        h, w = rgba.shape[:2]
        for gi, group in enumerate(self.groups):
            gx0, gy0, gx1, gy1 = eye_group_bbox(group)
            gx0, gy0 = max(0, int(gx0)), max(0, int(gy0))
            gx1, gy1 = min(w, int(gx1) + 1), min(h, int(gy1) + 1)
            for cover in (0.55, 1.0):
                one = make_blink_frames(rgba, group, covers=(cover,))[0]
                self.patches[(gi, cover)] = (gx0, gy0, one[gy0:gy1, gx0:gx1])
        self.hair = (HairField(wisps, rgba, w, h, wind_speed=hair_speed,
                               amp=hair_amp, stiffness=hair_stiff,
                               damping=hair_damp) if wisps else None)
        self.cloth = (HairField(cloth_wisps, rgba, w, h, wind_speed=hair_speed,
                                amp=hair_amp, stiffness=hair_stiff,
                                damping=hair_damp) if cloth_wisps else None)
        self._gaze_cache: dict[tuple[int, int], np.ndarray] = {}
        self._gaze_order: list[tuple[int, int]] = []
        # 表情状态机：半眼 / 惊讶 / 单侧眨眼偶尔来一下，和桌面版同一个池子
        self.expr = None
        self.expr_bank = None
        self._expr_cache: dict[str, np.ndarray] = {}
        try:
            if self.geos:
                self.expr_bank = build_expr_bank(rgba, self.geos)
            if self.expr_bank is not None:
                self.expr = ExprState(idle_pool=('half', 'wink_l', 'wink_r'),
                                      idle_ms=(3500, 9000), hold_ms=1500)
        except Exception as exc:
            print(f"[桌宠] 表情贴片构建失败：{exc}")
        self._nx = self._ny = 0.0
        self._t = 0.0

    def look(self, nx: float, ny: float):
        """鼠标在画布里的归一化位置：(-1, -1) 左上，(1, 1) 右下。"""
        self._nx, self._ny = float(nx), float(ny)

    def _gaze_base(self) -> np.ndarray:
        if not self.geos:
            return self.rgba
        dx = int(round(self._nx * 6.0 / 2)) * 2
        dy = int(round(self._ny * 6.0 / 2)) * 2
        key = (dx, dy)
        base = self._gaze_cache.get(key)
        if base is None:
            base = gaze_frame(self.rgba, self.eyes, dx, dy, geos=self.geos)
            self._gaze_cache[key] = base
            self._gaze_order.append(key)
            if len(self._gaze_order) > 9:
                self._gaze_cache.pop(self._gaze_order.pop(0), None)
        return base

    def frame(self, dt: float = 0.05) -> np.ndarray:
        """前进一帧，返回这一帧的 RGBA。"""
        if self.expr is not None:
            self.expr.tick()
        t = self._t
        if self.blinks:
            self._t = (self._t + dt) % self.cycle
        out = None
        # 相位统一走 blink_phases()（同一套 timeline_phase 切分），预览不再自算
        phases = blink_phases(self.blinks, self.cycle, t) if self.blinks else {}
        half_groups = {b.group_no for b in self.blinks if b.half}
        half_img = None
        if 0 in phases.values() and self.expr_bank is not None:
            half_img = self.expr_bank.apply(self.rgba, 'half')
        for gi, ph in phases.items():
            if gi > len(self.groups):
                continue
            if out is None:
                out = self._gaze_base().copy()
            gx0, gy0, ref = self.patches[(gi - 1, 1.0)]
            if ph == 0 and gi in half_groups and half_img is not None:
                ph_h, pw = ref.shape[:2]
                piece = half_img[gy0:gy0 + ph_h, gx0:gx0 + pw]
            else:
                _, _, piece = self.patches[(gi - 1, 0.55 if ph == 0 else 1.0)]
            ph_h, pw = piece.shape[:2]
            out[gy0:gy0 + ph_h, gx0:gx0 + pw] = piece
        if out is None:
            st = self.expr.state if self.expr is not None else 'normal'
            if st != 'normal':
                out = self._expr_frame(st)     # 整块盖住眼睛，不再叠视线
            else:
                out = self._gaze_base()
        for f in (self.hair, self.cloth):
            if f is not None:
                f.step(dt, 0.0, 0.0)
                out = f.swing(out)
        return out

    def _expr_frame(self, state: str) -> np.ndarray:
        """某个表情状态的整帧（按状态缓存，normal 不走这里）。"""
        if state not in self._expr_cache:
            self._expr_cache[state] = self.expr_bank.apply(self.rgba, state)
        return self._expr_cache[state]


class ImageCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        # 缩放锚点由 zoom_by 自己算（见那里注释），这里关掉 QGraphicsView 的
        # AnchorUnderMouse，免得它用内部记录的鼠标位置把画面再挪一次
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QColor("#2b2b2b"))

        self.pix_item = None
        self._base_pixmap: QPixmap | None = None
        self.done = None          # 由外部赋值的回调（value -> None）
        self.mode = "view"
        self._auto_fit = True      # 桌宠预览自动居中；用户手动放大后不再被 resize 拽回去
        self.allow_skip = False
        self.full_key = True
        self.box_color = "lime"

        self._box: tuple | None = None
        self._corners: list[list[int]] = []
        self._points: list[list[int]] = []
        self._overlays: list = []
        self._eye_boxes: list = []      # 已确认的眼睛框（旧拖框模式，保留兼容）
        self._eye_masks: list[np.ndarray] = []   # 已确认的眼眶 mask（整幅图坐标）
        self._eye_levels: list[str] = []         # 与 mask 一一对应的处理档位
        self.eye_level = "auto"                  # 当前选择：新画的眼用哪一档
        self._eye_pts: list[list[int]] = []      # 当前眼眶多边形的顶点 [x, y]
        self._drag_start = None
        self._drag_rect = None
        self._panning = False
        self._pan_pos = QPointF()
        self._pan_moved = 0.0    # 本次拖动累计位移，用来区分「右键拖动」和「右键单击」

        # 小桌子模式：左手光标抓起桌宠，像桌面桌宠一样拖着走
        self._hand_drag = False
        self._hand_dx = 0.0
        self._hand_dy = 0.0      # 按下时鼠标与桌宠的偏移，拖动不跳一下
        self._hand_vy = 0.0
        self._hand_prev_y = 0.0
        self._hand_prev_t = 0.0

        # 多边形发缕：mask + 发根（旋转中心）+ 发梢
        self._hair_polys: list[dict] = []
        self._hair_pts: list[list[int]] = []

        # 效果预览：多帧循环播放，u = 回退、s = 完成
        self._frames: list[QPixmap] = []
        self._frame_i = 0
        self._preview_live = None     # 可选：按鼠标实时渲染帧 (idx, nx, ny) -> QPixmap
        self._preview_n = 0
        self._gaze_nx = 0.0
        self._gaze_ny = 0.0
        self._ptimer = QTimer(self)
        self._ptimer.timeout.connect(self._next_frame)

        # 桌宠原尺寸预览：mode == "pet" 时按身体摇摆角度旋转
        self._pet_rots: list[float] = []   # 非空 = 正在摇摆（只当开关用）
        self._pet_i = 0
        # 摇摆按真实时间取值：固定 30fps 出场，慢速时每帧只挪一点，不会一格一格跳
        self._sway_amp = 0.0
        self._sway_period = 5.6
        self._sway_t0 = 0.0
        # 小桌子 + 重力：只在托盘模式下生效，桌宠从上方落到桌面上
        self._table_on = False
        self._table_items: list = []
        self._fall = 1.0                 # 下落系数：越大掉得越快，0 = 悬空不落
        self._bounce = 0.45              # 回弹系数：落到桌面反弹多少，0 = 不弹
        self._plat = 0.6                 # 平台宽度：画布宽度（自适应时能看到的范围）的 60%
        self._grav_y = 0.0               # 相对落点的竖直偏移（负数 = 还在空中）
        self._grav_vy = 0.0
        self._grav_landed = False
        self._grav_x = 0.0               # 在平台上的横向偏移（0 = 平台正中心）
        self._grav_vx = 0.0              # 横向速度：甩一下会顺着平台滑行（和桌面版一样）
        self._plat_w = 0.0               # 平台宽度（场景坐标）
        # 选区高亮：点表格行时在桌宠上盖一层流动绿光
        self._glow_item: AreaGlowItem | None = None
        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._tick_glow)
        # 桌宠逐帧渲染：目光 / 眨眼 / 头发飘动，和摇摆定时器分开
        self._pet_live = None
        self._pet_h = 0
        self._ftimer = QTimer(self)
        self._ftimer.timeout.connect(self._next_pet_frame)
        # 画布底色：淡天空蓝 + 稀疏彩色小圆点，桌宠居中时四周柔和又不单调
        self.setBackgroundBrush(QBrush(cute_dot_tile()))

    # ---------- 选区高亮 ----------
    def show_highlight(self, mask: np.ndarray | None):
        """在桌宠预览上高亮一块选区（mask 与素材同尺寸，裁切图坐标）。

        只在 mode == "pet" 时生效：标注界面里画的是裁切原图，坐标尺度不同。
        """
        self.clear_highlight()
        if mask is None or self.pix_item is None or self.mode != "pet":
            return
        base = self._base_pixmap
        if base is None or base.isNull() or mask.shape[1] == 0:
            return
        m = np.ascontiguousarray(mask.astype(np.uint8))
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return
        k = base.width() / float(mask.shape[1])
        path = QPainterPath()
        for c in contours:
            if len(c) < 3:
                continue
            poly = QPolygonF([QPointF(float(pt[0][0]) * k,
                                      float(pt[0][1]) * k) for pt in c])
            path.addPolygon(poly)
            path.closeSubpath()
        if path.isEmpty():
            return
        item = AreaGlowItem(path)
        item.setParentItem(self.pix_item)
        self._glow_item = item
        self._glow_timer.start(45)

    def clear_highlight(self):
        self._glow_timer.stop()
        if self._glow_item is not None:
            if self._glow_item.scene() is not None:
                self._scene.removeItem(self._glow_item)
            self._glow_item = None

    def _tick_glow(self):
        if self._glow_item is None:
            self._glow_timer.stop()
            return
        self._glow_item.step()

    # ---------- 图片 ----------
    def set_image(self, rgb: np.ndarray):
        self.stop_preview()
        self.clear_highlight()
        self._pet_rots = []
        self._ptimer.stop()
        self._table_on = False
        self._table_items = []
        self._grav_y = self._grav_vy = 0.0
        self._grav_landed = False
        self._grav_x = 0.0
        self._plat_w = 0.0
        self._hand_drag = False
        self.mode = "view"
        self._base_pixmap = QPixmap.fromImage(qimage(rgb))
        self._scene.clear()
        self._overlays = []
        self.pix_item = self._scene.addPixmap(self._base_pixmap)
        h, w = rgb.shape[:2]
        self._scene.setSceneRect(0, 0, w, h)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._sync_table_cursor()
        self.setFocus()
        self._zoom_notify()

    def fit(self):
        if self.pix_item is not None:
            pm = self._base_pixmap
            r = (QRectF(0, 0, pm.width(), pm.height()) if pm is not None
                 and not pm.isNull() else self._scene.sceneRect())
            self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
            self._auto_fit = True
            self._zoom_notify()

    def _zoom_notify(self):
        hook = getattr(self, "zoom_hook", None)
        if callable(hook):
            hook(int(round(self.transform().m11() * 100)))

    def zoom_by(self, factor: float, anchor: QPointF | None = None):
        """放大/缩小，anchor 是视口坐标（默认视口中心），最小缩到 5%。

        QGraphicsView 自带的 AnchorUnderMouse 依赖它内部记录的鼠标位置，
        滚轮缩放时经常失效（内容刚比视口小、滚动条没有行程时尤其明显），
        表现就是放大后图片一直往一个角落跑。这里自己算锚点：缩放前后把
        锚点下的场景坐标挪回同一屏幕位置。
        """
        cur = self.transform().m11()
        if cur * factor < 0.05:
            return
        vp = self.viewport()
        if anchor is None:
            anchor = QPointF(vp.width() / 2.0, vp.height() / 2.0)
        old = self.mapToScene(anchor.toPoint())
        self.scale(factor, factor)
        # 滚动条行程/视口尺寸都是缩放后才更新的（滚动条一出现视口还会变窄），
        # 一遍常常对不齐，重复几遍把锚点下的场景坐标挪回原来的屏幕位置
        for _ in range(3):
            k = self.transform().m11()
            cur = self.mapToScene(anchor.toPoint())
            dx, dy = cur.x() - old.x(), cur.y() - old.y()
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                break
            self.horizontalScrollBar().setValue(int(round(
                self.horizontalScrollBar().value() - dx * k)))
            self.verticalScrollBar().setValue(int(round(
                self.verticalScrollBar().value() - dy * k)))
        self._auto_fit = False
        self._zoom_notify()        # 桌子在世界里，缩放只动镜头，不用重画

    def wheelEvent(self, event):
        """滚轮缩放（以鼠标位置为锚点）；触控板没有 angleDelta 时用 pixelDelta。"""
        dy = event.angleDelta().y() or event.pixelDelta().y()
        if dy == 0:
            return
        self.zoom_by(1.15 if dy > 0 else 1 / 1.15, anchor=event.position())
        event.accept()

    # ---------- 阶段 ----------
    def enter_mode(self, mode: str, box=None, points=None,
                   allow_skip=False, full_key=True, color="lime", on_done=None,
                   band=24, boxes=None, masks=None, levels=None, hair=None):
        """清空旧标注，进入指定交互模式。on_done(value) 在确认/取消/跳过时调用。

        boxes：已确认的眼睛框列表（旧拖框模式）。
        masks：已确认的眼眶 mask 列表；levels 与之一一对应的处理档位。
        """
        self.mode = mode
        self.clear_highlight()
        self.allow_skip = allow_skip
        self.full_key = full_key
        self.box_color = color
        self.band = int(band)
        self.done = on_done
        self._corners = []
        self._points = [list(p) for p in points] if points else []
        self._eye_boxes = boxes if boxes is not None else []
        self._eye_masks = list(masks) if masks else []
        self._eye_levels = ([str(v) for v in levels] if levels
                            else ["auto"] * len(self._eye_masks))
        self._eye_pts = []
        self._hair_polys = list(hair) if hair else []
        self._hair_pts = []
        self._drag_start = None
        self._drag_rect = None
        self._clear_overlays()
        if self._base_pixmap is not None:
            self.pix_item.setPixmap(self._base_pixmap)
        if box:
            self._box = tuple(int(v) for v in box)
            self._draw_box(self._box)
        else:
            self._box = None
        if mode == "points":
            for i, p in enumerate(self._points, 1):
                self._draw_point(p[0], p[1], p[2], i)
        if mode == "eyes":
            self._redraw_eye_mode()
            self._notify_eye_rows()
        if mode in ("hair", "cloth"):
            self._redraw_hair_mode()
        if mode == "view":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if mode in ("hair", "cloth"):
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().unsetCursor()
        self.setFocus()

    def set_idle(self):
        self._ptimer.stop()
        self._frames = []
        self.mode = "view"
        self.done = None
        self.viewport().unsetCursor()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    # ---------- 效果预览 ----------
    def start_preview(self, frames: list[QPixmap], on_done, interval: int = 80,
                      live=None):
        """循环播放一组帧；u = 回退、s = 完成、ESC = 取消。

        live(idx, nx, ny) -> QPixmap：按鼠标位置实时渲染（用于视线跟随预览），
        nx/ny 为 -1~1 的归一化偏移；传 None 则只播静态帧。
        """
        self.stop_preview()
        self.clear_highlight()
        self.mode = "preview"
        self.done = on_done
        self._frames = list(frames)
        self._frame_i = 0
        self._preview_live = live
        self._preview_n = len(self._frames)
        self._gaze_nx = 0.0
        self._gaze_ny = 0.0
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setMouseTracking(live is not None)
        self.viewport().unsetCursor()
        pm = None
        if self._frames:
            pm = live(0, 0.0, 0.0) if live is not None else self._frames[0]
            if pm is not None and not pm.isNull():
                self.pix_item.setPixmap(pm)
            if self._preview_n > 1:
                self._ptimer.start(max(20, int(interval)))
        self.setFocus()

    def stop_preview(self):
        self._ptimer.stop()
        self._ftimer.stop()
        self._pet_live = None
        self._pet_h = 0
        self._frames = []
        self._preview_live = None
        self._preview_n = 0
        self.viewport().setMouseTracking(False)

    def _next_frame(self):
        if self.mode == "pet":
            if self._table_on and not self._grav_landed and not self._hand_drag:
                self._step_gravity()
            if self._pet_rots and self._sway_period > 0:
                # 按真实时间算角度：同一套 30fps 定时器下，快慢都连续
                t = time.monotonic() - self._sway_t0
                self.pix_item.setRotation(
                    self._sway_amp * math.sin(2 * math.pi * t / self._sway_period))
            if (not self._pet_rots and not self._hand_drag
                    and (not self._table_on or self._grav_landed)):
                self._ptimer.stop()     # 不摇、也落稳了，就别空转
            return
        if not self._frames:
            return
        n = max(1, self._preview_n or len(self._frames))
        self._frame_i = (self._frame_i + 1) % n
        if self._preview_live is not None:
            pm = self._preview_live(self._frame_i, self._gaze_nx, self._gaze_ny)
            if pm is not None and not pm.isNull():
                self.pix_item.setPixmap(pm)
            return
        self.pix_item.setPixmap(self._frames[self._frame_i])

    def _next_pet_frame(self):
        """桌宠逐帧渲染：目光 / 眨眼 / 头发飘动都由 live 每次现算。"""
        if self.mode != "pet" or self._pet_live is None:
            self._ftimer.stop()
            return
        pm = self._pet_pixmap(self._pet_live.frame())
        if pm is not None:
            self.pix_item.setPixmap(pm)

    def _pet_pixmap(self, arr):
        if arr is None:
            return None
        pm = QPixmap.fromImage(qimage_rgba(arr))
        if pm.isNull():
            return None
        if self._pet_h and pm.height() > self._pet_h:
            pm = pm.scaledToHeight(max(24, int(self._pet_h)),
                                   Qt.TransformationMode.SmoothTransformation)
        return pm

    # ---------- 桌宠原尺寸预览 ----------
    def show_pet(self, rgba: np.ndarray, height_px: int = 380,
                 animate: bool = True, live=None, frame_ms=None,
                 sway_deg: float = 2.4, sway_speed: float = 1.0):
        """按桌宠实际显示高度居中显示；animate 时绕脚底左右摇摆。

        live 是逐帧渲染器（带 .frame() / .look()），给了就按 frame_ms
        循环出图：目光跟随鼠标、眨眼、头发飘动都在桌宠身上直接生效。
        """
        pm = QPixmap.fromImage(qimage_rgba(rgba))
        if pm.isNull():
            return
        # 重建前记下重力状态：调动作参数时宠物接着往下掉，不要回到桌面上方重落
        prev_grav = ((self._grav_x, self._grav_y, self._grav_vy,
                      self._grav_landed, self._grav_vx)
                     if self._table_on else None)
        # 先定显示高度，再让 live 出首帧：_pet_pixmap 靠 _pet_h 缩放，
        # 顺序反了（stop_preview 会把 _pet_h 清 0）首帧就会按素材原始尺寸铺开
        target_h = pm.height()
        if height_px and target_h > height_px:
            target_h = max(24, int(height_px))
        self.stop_preview()
        self._pet_h = target_h
        if live is not None:
            pm = self._pet_pixmap(live.frame()) or pm
        if pm.height() != target_h:
            pm = pm.scaledToHeight(
                target_h, Qt.TransformationMode.SmoothTransformation)
        self.clear_highlight()
        self._scene.clear()
        self._overlays = []
        self._table_items = []          # 旧桌子随 scene.clear() 一起没了
        if (self._base_pixmap is None
                or self._base_pixmap.size() != pm.size()):
            self._plat_w = 0.0          # 换了一张图 = 换了个世界，平台按新尺寸重定
        self._base_pixmap = pm
        self.pix_item = self._scene.addPixmap(pm)
        self.pix_item.setTransformOriginPoint(pm.width() / 2.0,
                                              pm.height() * 0.94)
        self._scene.setSceneRect(0, 0, pm.width(), pm.height())
        self.mode = "pet"
        self.done = None
        self._auto_fit = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().unsetCursor()
        self._center_pet()
        self._pet_h = pm.height()
        self._pet_live = live
        amp = max(0.0, float(sway_deg))
        speed = max(0.0, float(sway_speed))
        was_swaying = bool(self._pet_rots)
        self._sway_amp = amp
        self._sway_period = max(0.6, 5.6 / speed) if speed > 0 else 0.0
        self._pet_rots = [amp] if animate and amp > 0 and speed > 0 else []
        self._pet_i = 0
        if self._pet_rots:
            # 接着原来的相位摆：调参数重建时不要从 0° 重新起步，
            # 否则小角度 + 长周期会看着像「卡住不动」
            if not was_swaying:
                self._sway_t0 = time.monotonic()
            self.pix_item.setRotation(self._sway_amp * math.sin(
                2 * math.pi * (time.monotonic() - self._sway_t0) / self._sway_period))
        # 小桌子：重建时沿用落点 / 速度，桌子一直待在脚下
        if self._table_on:
            if prev_grav is None:
                self._grav_x = 0.0
                self._grav_y = -self._drop_dist()
                self._grav_vy = 0.0
                self._grav_vx = 0.0
                self._grav_landed = False
            else:
                (self._grav_x, self._grav_y,
                 self._grav_vy, self._grav_landed, self._grav_vx) = prev_grav
            self._build_table()
            self.pix_item.setPos(self._grav_x, self._grav_y)
        if self._pet_rots or (self._table_on and not self._grav_landed):
            self._ptimer.start(33)      # 固定 30fps：慢速也平滑，快速也不会丢帧
        else:
            self._ptimer.stop()
        if live is not None:
            self.viewport().setMouseTracking(True)
            self._ftimer.start(max(30, int(frame_ms or 50)))
        self._hand_drag = False
        self._sync_table_cursor()
        self.setFocus()

    def _center_pet(self):
        """桌宠原尺寸居中：放得下就 1:1，放不下才等比缩小。"""
        pm = self._base_pixmap
        if pm is None or pm.isNull():
            return
        vs = self.viewport().size()
        k = min(1.0, vs.width() / pm.width(), vs.height() / pm.height())
        self.resetTransform()
        for _ in range(4):  # 缩放后滚动范围才准，重复几次保证真正居中
            self.scale(k, k)
            self.centerOn(pm.width() / 2.0, pm.height() / 2.0)
        self._zoom_notify()

    # ---------- 小桌子 / 重力 ----------
    @property
    def table_on(self) -> bool:
        return self._table_on

    def set_fall(self, fall: float):
        """下落系数：加速度倍率，0 = 悬空不落。"""
        try:
            self._fall = max(0.0, float(fall))
        except (TypeError, ValueError):
            self._fall = 1.0

    def set_bounce(self, bounce: float):
        """回弹系数：落地时反弹速度的倍率，0 = 直接停住，1 = 原速弹回。"""
        try:
            self._bounce = max(0.0, min(1.0, float(bounce)))
        except (TypeError, ValueError):
            self._bounce = 0.45

    def set_plat(self, plat: float):
        """平台宽度：占画布宽度的比例（0.1 ~ 20.0），世界坐标里定死，不随缩放变。"""
        try:
            v = max(0.1, min(20.0, float(plat)))
        except (TypeError, ValueError):
            v = 0.6
        if abs(v - self._plat) < 1e-6:
            return
        self._plat = v
        self._plat_w = 0.0         # 宽度变了，重新按新比例定一次世界尺寸
        if self._table_on:
            self._build_table(center=False)   # 开着桌子时立刻按新宽度重画

    def set_table(self, on: bool, fall: float | None = None) -> bool:
        """开 / 关小桌子重力。返回 False 表示当前没有桌宠可放。"""
        if fall is not None:
            self.set_fall(fall)
        if on and (self.mode != "pet" or self.pix_item is None):
            return False
        if bool(on) == self._table_on:
            return True
        self._table_on = bool(on)
        if self._table_on:
            # 初始悬在桌面上方、正中心，松手即落
            self._grav_x = 0.0
            self._grav_y = -self._drop_dist()
            self._grav_vy = 0.0
            self._grav_vx = 0.0
            self._grav_landed = False
            self._plat_w = 0.0         # 开桌时按当前世界尺寸定一次平台宽度
            self._build_table()
            self.pix_item.setPos(self._grav_x, self._grav_y)
            self._ptimer.start(33)
            self._sync_table_cursor()
        else:
            self._clear_table()
            self._plat_w = 0.0
            self._grav_x = 0.0
            self._grav_y = self._grav_vy = 0.0
            self._grav_vx = 0.0
            self._grav_landed = False
            self._hand_drag = False
            if self.pix_item is not None:
                self.pix_item.setPos(0.0, 0.0)
            if not self._pet_rots:
                self._ptimer.stop()
            self._sync_table_cursor()
        return True

    def _drop_dist(self) -> float:
        """起落高度：和小桌子一起按桌宠身高缩放。"""
        h = self._base_pixmap.height() if self._base_pixmap else 320
        return max(48.0, h * 0.42)

    def _step_gravity(self):
        """一帧重力积分：撞到平台按回弹系数反弹；掉出平台落到画布底部再跳回正中心。"""
        dt = 0.033
        acc = max(500.0, (self._base_pixmap.height() if self._base_pixmap else 320) * 5.0)
        acc *= self._fall
        # 横向：甩一下顺着平台滑行，阻尼和桌面版一致（exp(-2.4*dt)），慢下来就停住
        if self._grav_vx:
            self._grav_x += self._grav_vx * dt
            self._grav_vx *= math.exp(-2.4 * dt)
            if abs(self._grav_vx) < 12.0:
                self._grav_vx = 0.0
        if acc > 0:
            self._grav_vy += acc * dt
            self._grav_y += self._grav_vy * dt
        if self._grav_y >= 0.0:
            if self._on_platform():
                impact = self._grav_vy
                if impact > 140.0 and self._bounce > 0.02:
                    # 撞平台：速度反向并打折扣，弹起来再落，几次后自然停稳
                    self._grav_y = 0.0
                    self._grav_vy = -impact * self._bounce
                    self._grav_landed = False
                else:
                    self._grav_y = 0.0
                    self._grav_vy = 0.0
                    self._grav_landed = True
            elif self._grav_y >= self._fall_bottom():
                # 已经掉出画布底部：这时才跳回平台正中心，再从一点点高度落稳
                self._respawn_center()
                self._ptimer.start(33)
                return
        self.pix_item.setPos(self._grav_x, self._grav_y)

    def _on_platform(self) -> bool:
        """脚底中心是否还压在平台上（留一点边缘余量，能站在边上）。"""
        if self._plat_w <= 0.0:
            return True
        w = self._base_pixmap.width() if self._base_pixmap else 320
        margin = min(w * 0.06, self._plat_w * 0.2)
        return abs(self._grav_x) <= self._plat_w / 2.0 - margin

    def _fall_bottom(self) -> float:
        """画布可视底部的场景 y：桌宠整个掉到这下面才算「落到底」。"""
        h = self._base_pixmap.height() if self._base_pixmap else 320.0
        try:
            bottom = self.mapToScene(self.viewport().rect()).boundingRect().bottom()
        except RuntimeError:
            bottom = 0.0
        return max(float(bottom), h * 1.15, 1.0)

    def _respawn_center(self):
        """落到画布底部后回到平台正中心：留一小段落差，落地有个「跳回来」的反馈。"""
        h = self._base_pixmap.height() if self._base_pixmap else 320
        self._grav_x = 0.0
        self._grav_y = -min(36.0, h * 0.12)
        self._grav_vy = 0.0
        self._grav_vx = 0.0
        self._grav_landed = False
        if self.pix_item is not None:
            self.pix_item.setPos(0.0, self._grav_y)

    def _build_table(self, center: bool = True):
        """在桌宠脚下画一张木平台（桌板 + 两条腿）。

        桌宠和桌子都画在同一个固定世界里（全部用场景坐标）：滚轮缩放只是镜头拉近拉远，
        桌宠和桌子一起放大缩小，比例永远不变；改窗口大小同理。
        平台的世界宽度只在开桌 / 拖「平台宽度」/ 换图时按
        「100% 缩放时的画布宽度 × 百分比」定一次，之后不再重算。
        """
        pm = self._base_pixmap
        if pm is None or pm.isNull() or self.pix_item is None:
            return
        self._clear_table()
        w, h = float(pm.width()), float(pm.height())
        if self._plat_w <= 0:
            # 100% 的基准 = 默认视角（桌宠 1:1 或缩小到刚好放下）时画布能看到的整个画面宽度。
            # 用它而不是当前视口，滚轮缩放到 5% 还是 300% 都不影响平台该多宽。
            try:
                vp = self.viewport()
                vw, vh = float(vp.width()), float(vp.height())
            except RuntimeError:
                vw, vh = float(self.width()), float(self.height())
            if vh <= 0:
                vh = 1.0
            base = max(vw, w, vw * h / vh)
            # 最窄也略宽过桌宠，否则桌板会被桌宠整个挡住、只剩两条腿
            self._plat_w = max(w * 0.55, base * self._plat)
        tw = self._plat_w
        th = min(h * 0.10, max(8.0, h * 0.026, tw * 0.055))
        leg_h = max(26.0, h * 0.10, th * 1.6)
        leg_w = max(10.0, th * 1.2)
        x = (w - tw) / 2.0          # 平台中心 = 桌宠脚下中心

        wood = QColor("#e7b98c")
        wood_dark = QColor("#c9946a")
        # 脚底大约在 94% 高度（与旋转原点一致），平台面就画在这条线上，略高一点像踩住
        surf = h * 0.94 - th * 0.15

        top = QPainterPath()
        top.addRoundedRect(QRectF(x, surf, tw, th), th / 2.0, th / 2.0)
        it = self._scene.addPath(top, QPen(wood_dark, 2.0), QBrush(wood))
        it.setZValue(-1)
        self._table_items.append(it)

        for lx in (x + tw * 0.14, x + tw * 0.86 - leg_w):
            leg = QPainterPath()
            leg.addRoundedRect(
                QRectF(lx, surf + th * 0.9, leg_w, leg_h), leg_w / 2.0, leg_w / 2.0)
            it = self._scene.addPath(leg, QPen(wood_dark, 1.5), QBrush(wood_dark))
            it.setZValue(-1)
            self._table_items.append(it)

        if center:
            self.centerOn(w / 2.0, h / 2.0)     # 平台变宽后别把桌宠挤出视野

    def _clear_table(self):
        for it in self._table_items:
            with contextlib.suppress(RuntimeError, ValueError):
                if it.scene() is not None:
                    self._scene.removeItem(it)
        self._table_items = []
        if self._base_pixmap is not None and not self._base_pixmap.isNull():
            self._scene.setSceneRect(
                0, 0, self._base_pixmap.width(), self._base_pixmap.height())

    def _sync_table_cursor(self):
        """小桌子模式用一手光标：平时张开，抓起桌宠时握紧。"""
        if self.mode == "pet" and self._table_on:
            self.setCursor(Qt.CursorShape.ClosedHandCursor if self._hand_drag
                           else Qt.CursorShape.OpenHandCursor)
        elif not self._panning:
            self.unsetCursor()

    def _grab_pet(self, event):
        """左手按下：抓住桌宠，断开重力，记下鼠标与它的纵向偏移。"""
        if not (self.mode == "pet" and self._table_on
                and self.pix_item is not None):
            return False
        p = self.mapToScene(event.position().toPoint())
        self._hand_drag = True
        self._hand_dx = self._grav_x - p.x()   # 按住哪儿都不跳一下
        self._hand_dy = self._grav_y - p.y()
        self._hand_vy = 0.0
        self._hand_vx = 0.0
        self._hand_prev_y = self._grav_y
        self._hand_prev_x = self._grav_x
        self._hand_prev_t = time.monotonic()
        self._grav_landed = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def _drag_pet(self, event):
        """拖动桌宠：可以左右挪到平台任意位置，往上拎；最低停在平台面。"""
        if not self._hand_drag:
            return False
        p = self.mapToScene(event.position().toPoint())
        w = self._base_pixmap.width() if self._base_pixmap else 320.0
        lim = self._plat_w / 2.0 + w * 0.8    # 能拖出平台边缘，松手再掉回去
        x = max(-lim, min(lim, p.x() + self._hand_dx))
        y = min(0.0, p.y() + self._hand_dy)
        now = time.monotonic()
        dt = max(1e-3, now - self._hand_prev_t)
        inst = (y - self._hand_prev_y) / dt
        self._hand_vy = 0.6 * self._hand_vy + 0.4 * inst   # 松手时的初速度
        inst_x = (x - self._hand_prev_x) / dt
        self._hand_vx = 0.6 * self._hand_vx + 0.4 * inst_x   # 左右甩的速度
        self._hand_prev_x, self._hand_prev_y = x, y
        self._hand_prev_t = now
        self._grav_x, self._grav_y = x, y
        self.pix_item.setPos(x, y)
        return True

    def _release_pet(self):
        """松手：把拖动速度交给重力；左右甩出去会顺着平台滑行，滑出边缘就掉下去。"""
        if not self._hand_drag:
            return False
        self._hand_drag = False
        # 停住不动再松手不该甩出去：最后一次移动已过去一会儿就把速度归零
        if time.monotonic() - self._hand_prev_t > 0.12:
            self._hand_vx = self._hand_vy = 0.0
        self._grav_vy = max(-2400.0, min(2400.0, self._hand_vy))
        self._grav_vx = max(-2400.0, min(2400.0, self._hand_vx))
        if self._grav_y >= 0.0 and self._on_platform():
            self._grav_y = 0.0
            self._grav_vy = 0.0
            self._grav_landed = True
            if abs(self._grav_vx) > 20.0:
                self._ptimer.start(33)      # 还带着横向速度：接着滑
        else:
            # 悬空或在平台外：交给重力，落到底部再 _respawn_center()
            self._grav_landed = False
            self._ptimer.start(33)
        self.pix_item.setPos(self._grav_x, self._grav_y)
        self._sync_table_cursor()
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 只有还没手动缩放时才重新居中；否则放大后滚动条一出现就被拽回适应大小
        if self.mode == "pet" and self._auto_fit:
            self._center_pet()
        # 桌子画在世界坐标里，窗口变化只影响镜头，不用重画

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

    def _draw_cut_frame(self):
        """在裁切图上描一圈绿色虚线，标出「这一段是剪切范围」。"""
        r = self._scene.sceneRect()
        if r.isNull() or r.width() < 6 or r.height() < 6:
            return
        pen = QPen(QColor(self.box_color), 1, Qt.PenStyle.DashLine)
        item = self._scene.addRect(r.adjusted(2, 2, -2, -2), pen)
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
        self._draw_cut_frame()
        for i, p in enumerate(self._points, 1):
            self._draw_point(p[0], p[1], p[2], i)

    def _draw_mask_contour(self, mask: np.ndarray, color: str, dashed=False):
        """把眼眶 mask 的轮廓画到场景里（只画外轮廓，不填色，画面不挡原图）。"""
        cnts, _ = cv2.findContours(mask.astype(np.uint8),
                                   cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        pen = QPen(QColor(color), 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        for c in cnts:
            pts = c.reshape(-1, 2)
            if len(pts) < 3:
                continue
            path = QPainterPath()
            path.moveTo(float(pts[0][0]), float(pts[0][1]))
            for x, y in pts[1:]:
                path.lineTo(float(x), float(y))
            path.closeSubpath()
            self._overlays.append(self._scene.addPath(path, pen))

    def _draw_mask_fill(self, mask: np.ndarray, color="#2ecc71",
                        alpha: int = 70):
        """绿色半透明 = 抠图落到的范围（一眼看出哪些会被保留）。"""
        if mask is None or not mask.any():
            return
        h, w = mask.shape[:2]
        rgba = np.zeros((h, w, 4), np.uint8)
        c = QColor(color)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = c.red(), c.green(), c.blue()
        rgba[..., 3] = np.where(mask, alpha, 0)
        item = self._scene.addPixmap(QPixmap.fromImage(qimage_rgba(rgba)))
        self._overlays.insert(0, item)

    def _redraw_eye_mode(self):
        """重画眼睛：已确认的眼眶 mask（虚线 + 序号）、当前多边形的边与顶点。"""
        self._clear_overlays()
        self._draw_cut_frame()
        for i, b in enumerate(self._eye_boxes, 1):
            self._draw_box(b, dashed=True)
            txt = QGraphicsSimpleTextItem(f"眼{i}")
            txt.setBrush(QColor(self.box_color))
            txt.setFont(QFont("Sans", 10, QFont.Weight.Bold))
            txt.setPos(b[0] + 4, b[1] + 4)
            self._scene.addItem(txt)
            self._overlays.append(txt)
        for i, m in enumerate(self._eye_masks):
            self._draw_mask_contour(m, self.box_color, dashed=True)
            ys, xs = np.where(m)
            if len(xs):
                lv = self._eye_levels[i] if i < len(self._eye_levels) else "auto"
                tag = "自动" if lv == "auto" else lv
                txt = QGraphicsSimpleTextItem(f"眼{i + 1}·{tag}")
                txt.setBrush(QColor(self.box_color))
                txt.setFont(QFont("Sans", 10, QFont.Weight.Bold))
                txt.setPos(int(xs.min()) + 4, int(ys.min()) + 4)
                self._scene.addItem(txt)
                self._overlays.append(txt)
        if self._eye_pts:
            self._draw_eye_polygon()
        for i, p in enumerate(self._eye_pts, 1):
            self._draw_vertex(p[0], p[1], i)

    def _draw_vertex(self, x, y, idx, color: str = "#39ff14"):
        """多边形顶点：小圆点 + 序号（区别于第 2 步的前景/背景叉号）。"""
        col = QColor(color)
        item = self._scene.addEllipse(QRectF(x - 3, y - 3, 6, 6),
                                      QPen(col, 1), col)
        self._overlays.append(item)
        txt = QGraphicsSimpleTextItem(str(idx))
        txt.setBrush(col)
        txt.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        txt.setPos(x + 5, y - 16)
        self._scene.addItem(txt)
        self._overlays.append(txt)

    def _draw_eye_polygon(self):
        """当前眼眶多边形：实线连边，≥3 个顶点时用虚线闭合到起点。"""
        pen = QPen(QColor("#39ff14"), 2)
        path = QPainterPath()
        path.moveTo(float(self._eye_pts[0][0]), float(self._eye_pts[0][1]))
        for x, y in self._eye_pts[1:]:
            path.lineTo(float(x), float(y))
        self._overlays.append(self._scene.addPath(path, pen))
        if len(self._eye_pts) >= 3:
            close = QPen(QColor("#39ff14"), 1, Qt.PenStyle.DashLine)
            cp = QPainterPath()
            cp.moveTo(float(self._eye_pts[-1][0]), float(self._eye_pts[-1][1]))
            cp.lineTo(float(self._eye_pts[0][0]), float(self._eye_pts[0][1]))
            self._overlays.append(self._scene.addPath(cp, close))

    def _close_eye_polygon(self) -> bool:
        """把当前多边形填成眼眶 mask 并确认（至少 3 个顶点）。"""
        if len(self._eye_pts) < 3:
            self._flash("至少点 3 个顶点圈住眼眶；左键加点，回车或「闭合眼眶」闭合")
            return False
        r = self._scene.sceneRect()
        w, h = int(r.width()), int(r.height())
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.array(self._eye_pts, np.int32)], 1)
        self._eye_masks.append(m.astype(bool))
        self._eye_levels.append(self.eye_level)
        self._eye_pts = []
        self._redraw_eye_mode()
        self._notify_eye_rows()
        tag = "自动判定" if self.eye_level == "auto" else self.eye_level
        self._flash(f"已确认第 {len(self._eye_masks)} 只眼（{tag}）；"
                    "继续点下一只，切换右侧档位可同图混用，或再按回车完成")
        return True

    def _notify_eye_rows(self):
        """眼眶数量/档位变了：让主界面把眨眼时间轴同步成「一只眼一行」。"""
        hook = getattr(self, "eye_rows_hook", None)
        if callable(hook):
            hook([str(lv) for lv in self._eye_levels])

    # ---------- 多边形发缕 / 衣服 ----------
    def _wisp_style(self) -> tuple[str, str]:
        """当前标注的是头发还是衣服：返回 (主色, 序号前缀)。"""
        if self.mode == "cloth":
            return "#4fc3f7", "衣"
        return "#ff9800", "发"

    def _draw_hair_polygon(self):
        """当前发缕/衣物多边形：实线连边，≥3 个顶点时虚线闭合到起点。"""
        color, _ = self._wisp_style()
        pen = QPen(QColor(color), 2)
        path = QPainterPath()
        path.moveTo(float(self._hair_pts[0][0]), float(self._hair_pts[0][1]))
        for x, y in self._hair_pts[1:]:
            path.lineTo(float(x), float(y))
        self._overlays.append(self._scene.addPath(path, pen))
        if len(self._hair_pts) >= 3:
            close = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
            cp = QPainterPath()
            cp.moveTo(float(self._hair_pts[-1][0]), float(self._hair_pts[-1][1]))
            cp.lineTo(float(self._hair_pts[0][0]), float(self._hair_pts[0][1]))
            self._overlays.append(self._scene.addPath(cp, close))

    def _redraw_hair_mode(self):
        """重画发缕/衣物：已确认的 mask（虚线 + 序号 + 发根标记）、当前多边形的边与顶点。"""
        color, tag = self._wisp_style()
        self._clear_overlays()
        self._draw_cut_frame()
        for i, w in enumerate(self._hair_polys, 1):
            self._draw_mask_contour(w["mask"], color, dashed=True)
            px, py = w["pivot"]
            root = self._scene.addEllipse(QRectF(px - 4, py - 4, 8, 8),
                                          QPen(QColor(color), 1),
                                          QColor(color))
            self._overlays.append(root)
            ys, xs = np.where(w["mask"])
            if len(xs):
                txt = QGraphicsSimpleTextItem(f"{tag}{i}")
                txt.setBrush(QColor(color))
                txt.setFont(QFont("Sans", 10, QFont.Weight.Bold))
                txt.setPos(int(xs.min()) + 4, int(ys.min()) + 4)
                self._scene.addItem(txt)
                self._overlays.append(txt)
        if self._hair_pts:
            self._draw_hair_polygon()
        for i, p in enumerate(self._hair_pts, 1):
            # 第一个顶点是发根（旋转中心），用不同颜色标出来
            self._draw_vertex(p[0], p[1], i,
                              "#ff5722" if i == 1 else color)

    def _close_hair_polygon(self) -> bool:
        """把当前多边形填成发缕/衣物 mask；第一个顶点是发根，最远像素是发梢。"""
        label = "衣物" if self.mode == "cloth" else "头发"
        if len(self._hair_pts) < 3:
            self._flash(f"至少点 3 个顶点圈住一块{label}；第一个顶点要点在固定端")
            return False
        r = self._scene.sceneRect()
        w, h = int(r.width()), int(r.height())
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.array(self._hair_pts, np.int32)], 1)
        m = m.astype(bool)
        pivot = (float(self._hair_pts[0][0]), float(self._hair_pts[0][1]))
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            self._flash("多边形没有圈到像素，请重新画")
            self._hair_pts = []
            self._redraw_hair_mode()
            return False
        d = (xs - pivot[0]) ** 2 + (ys - pivot[1]) ** 2
        k = int(np.argmax(d))
        self._hair_polys.append({"mask": m, "pivot": pivot,
                                 "tip": (float(xs[k]), float(ys[k]))})
        self._hair_pts = []
        self._redraw_hair_mode()
        self._flash(f"已确认第 {len(self._hair_polys)} 块{label}；继续画下一块，"
                    "或再按回车完成")
        return True

    def _confirm_hair_polys(self):
        """回车闭合当前多边形；没有待闭合顶点时回车 = 完成返回。"""
        if self._hair_pts:
            self._close_hair_polygon()
            return
        if self._hair_polys:
            self._finish([w for w in self._hair_polys])
            return
        label = "衣物" if self.mode == "cloth" else "发缕"
        self._flash(f"左键沿{label}轮廓点一圈（第一个顶点=固定端），回车或点「闭合」闭合")

    # ---------- 鼠标 ----------
    def _pos(self, event):
        p = self.mapToScene(event.position().toPoint())
        return int(round(p.x())), int(round(p.y()))

    def _flash(self, msg: str):
        """画布左上角弹一条短提示（不随缩放变形）。"""
        if not hasattr(self, "_flash_label"):
            self._flash_label = QLabel(self.viewport())
            self._flash_label.setStyleSheet(
                "background:#fff6e6;color:#8a5b16;border:2px dashed #ffdca8;"
                "padding:4px 10px;border-radius:12px;font-weight:600;")
            self._flash_label.hide()
        self._flash_label.setText(msg)
        self._flash_label.adjustSize()
        self._flash_label.move(10, 10)
        self._flash_label.show()
        self._flash_label.raise_()
        QTimer.singleShot(1600, self._flash_label.hide)

    def mousePressEvent(self, event):
        # 右键 / 中键按住拖动 = 平移图片，所有阶段通用（放大后挪到想看的区域）
        if event.button() in (Qt.MouseButton.MiddleButton,
                              Qt.MouseButton.RightButton):
            self._panning = True
            self._pan_pos = event.position()
            self._pan_moved = 0.0
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self._grab_pet(event)):
            return
        if self.mode in ("hair", "cloth") and event.button() == Qt.MouseButton.LeftButton:
            label = "衣物" if self.mode == "cloth" else "发缕"
            x, y = self._pos(event)
            r = self._scene.sceneRect()
            if not (0 <= x < r.width() and 0 <= y < r.height()):
                self._flash(f"点在图片外，请沿{label}轮廓点")
                return
            self._hair_pts.append([x, y])
            self._redraw_hair_mode()
            return
        if self.mode == "points" and event.button() == Qt.MouseButton.LeftButton:
            # 右键单击（没拖动）= 背景点，在 mouseReleaseEvent 里补上
            self._add_point(event, 1)
            return
        if self.mode == "eyes" and event.button() == Qt.MouseButton.LeftButton:
            x, y = self._pos(event)
            r = self._scene.sceneRect()
            if not (0 <= x < r.width() and 0 <= y < r.height()):
                self._flash("点在图片外，请在眼眶边界上点")
                return
            self._eye_pts.append([x, y])
            self._redraw_eye_mode()
            return
        if self.mode == "box" and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._pos(event)
            self._drag_rect = self._scene.addRect(
                QRectF(self._drag_start[0], self._drag_start[1], 0, 0),
                QPen(QColor(self.box_color), 1, Qt.PenStyle.DashLine))
            return
        super().mousePressEvent(event)

    def _add_point(self, event, label: int):
        """补点：label=1 前景（红），0 背景（蓝）；只认绿框内的点。"""
        x, y = self._pos(event)
        if self._box and not (self._box[0] <= x <= self._box[2]
                              and self._box[1] <= y <= self._box[3]):
            self._flash("这点在绿框外，会被裁掉 —— 请在绿框内标点")
            return
        self._points.append([x, y, label])
        self._draw_point(x, y, label, len(self._points))

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_pos
            self._pan_pos = event.position()
            self._pan_moved += abs(delta.x()) + abs(delta.y())
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            return
        if self._drag_pet(event):
            return
        if self.mode == "pet" and self._pet_live is not None:
            # 桌宠跟着鼠标看：以画布中心为原点归一化，交给渲染器去算位移
            vp = self.viewport()
            vw, vh = max(1, vp.width()), max(1, vp.height())
            self._pet_live.look(
                max(-1.0, min(1.0, (event.position().x() - vw / 2) / (vw / 2))),
                max(-1.0, min(1.0, (event.position().y() - vh / 2) / (vh / 2))))
            pm = self._pet_pixmap(self._pet_live.frame(0.0))
            if pm is not None:
                self.pix_item.setPixmap(pm)
            return
        if self.mode == "preview" and self._preview_live is not None:
            vp = self.viewport()
            vw, vh = max(1, vp.width()), max(1, vp.height())
            self._gaze_nx = max(-1.0, min(1.0, (event.position().x() - vw / 2)
                                          / (vw / 2)))
            self._gaze_ny = max(-1.0, min(1.0, (event.position().y() - vh / 2)
                                          / (vh / 2)))
            pm = self._preview_live(self._frame_i, self._gaze_nx, self._gaze_ny)
            if pm is not None and not pm.isNull():
                self.pix_item.setPixmap(pm)
            return
        if self.mode == "box" and self._drag_start and self._drag_rect:
            x, y = self._pos(event)
            self._drag_rect.setRect(QRectF(QPointF(*self._drag_start),
                                           QPointF(x, y)).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._release_pet()):
            return
        if self._panning and event.button() in (Qt.MouseButton.MiddleButton,
                                                Qt.MouseButton.RightButton):
            self._panning = False
            self._sync_table_cursor()
            # 补点阶段的右键单击（没拖动）= 背景点；拖动了就只当平移
            if (event.button() == Qt.MouseButton.RightButton
                    and self.mode == "points" and self._pan_moved < 4.0):
                self._add_point(event, 0)
            return
        if self.mode == "box" and self._drag_start:
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

    # ---------- 键盘 / 按钮 ----------
    def keyPressEvent(self, event):
        act = {
            Qt.Key.Key_U: "undo", Qt.Key.Key_R: "reset",
            Qt.Key.Key_S: "skip", Qt.Key.Key_F: "full",
            Qt.Key.Key_Escape: "escape",
            Qt.Key.Key_Return: "confirm", Qt.Key.Key_Enter: "confirm",
        }.get(event.key())
        if act is None:
            super().keyPressEvent(event)
            return
        self.run_action(act)

    def run_action(self, act: str):
        """一步交互的动作，屏幕按钮与键盘共用同一套逻辑。"""
        if self.mode == "preview":
            if act == "undo":
                self._finish("back")
            elif act in ("ok", "skip"):
                self._finish("ok")
            elif act == "escape":
                self._finish(None)
            return
        if self.mode not in ("box", "eyes", "points", "hair", "cloth"):
            return

        if act == "escape":
            self._finish(None)
        elif act == "confirm":
            if self.mode == "box":
                if self._box:
                    self._finish(self._box)
            elif self.mode == "eyes":
                self._confirm_eye_mask()
            elif self.mode == "points":
                self._finish([list(p) for p in self._points])
            elif self.mode in ("hair", "cloth"):
                self._confirm_hair_polys()
        elif act == "done":
            # 「完成」：把还没闭合的那一个先闭合，再交差
            if self.mode == "eyes":
                if self._eye_pts:
                    self._close_eye_polygon()
                if self._eye_masks:
                    self._finish(self._eye_result())
                else:
                    self._flash("还没有画好的眼眶：左键点一圈，回车或点「闭合眼眶」闭合")
            elif self.mode in ("hair", "cloth"):
                if self._hair_pts:
                    self._close_hair_polygon()
                if self._hair_polys:
                    self._finish([w for w in self._hair_polys])
                else:
                    label = "衣物" if self.mode == "cloth" else "发缕"
                    self._flash(f"还没有画好的{label}：左键点一圈，回车或点「闭合」闭合")
            elif self.mode == "points":
                self._finish([list(p) for p in self._points])
            elif self.mode == "box":
                if self._box:
                    self._finish(self._box)
        elif act == "reset":
            if self.mode == "points":
                self._points.clear()
                self._redraw_points()
            elif self.mode in ("hair", "cloth"):
                # 先清当前这块的顶点，再按一次清掉全部已确认的区域
                if self._hair_pts:
                    self._hair_pts = []
                else:
                    self._hair_polys.clear()
                self._redraw_hair_mode()
            elif self.mode == "eyes":
                # 先清当前这只眼的多边形，再按一次清掉全部已确认的眼
                if self._eye_pts:
                    self._eye_pts = []
                else:
                    self._eye_masks.clear()
                    self._eye_levels.clear()
                self._redraw_eye_mode()
                self._notify_eye_rows()
            elif self.mode == "box":
                self._corners.clear()
                self._box = None
                self._clear_overlays()
        elif act == "undo":
            if self.mode == "points":
                if self._points:
                    self._points.pop()
                self._redraw_points()
            elif self.mode in ("hair", "cloth"):
                # 先撤销当前多边形的最后一个顶点，没有顶点就撤销上一块
                if self._hair_pts:
                    self._hair_pts.pop()
                elif self._hair_polys:
                    self._hair_polys.pop()
                self._redraw_hair_mode()
            elif self.mode == "eyes":
                # 先撤销当前多边形的最后一个顶点，没有顶点就撤销上一只已确认的眼
                if self._eye_pts:
                    self._eye_pts.pop()
                    self._redraw_eye_mode()
                elif self._eye_masks:
                    self._eye_masks.pop()
                    if self._eye_levels:
                        self._eye_levels.pop()
                    self._redraw_eye_mode()
                    self._notify_eye_rows()
            else:
                if self._box:
                    self._box = None
                    self._clear_overlays()
                elif self._corners:
                    self._corners.pop()
                    self._clear_overlays()
                    for p in self._corners:
                        self._draw_corner(*p)
        elif act == "skip" and self.allow_skip:
            if self.mode == "eyes":
                self._finish(self._eye_result() if self._eye_masks else SKIP)
            elif self.mode in ("hair", "cloth"):
                self._finish([w for w in self._hair_polys] if self._hair_polys
                             else SKIP)
            else:
                self._finish(SKIP)
        elif act == "full" and self.full_key and self.mode != "points":
            w = int(self._scene.sceneRect().width())
            h = int(self._scene.sceneRect().height())
            self._box = (0, 0, w, h)
            self._clear_overlays()
            self._draw_box(self._box)
            self._finish(self._box)

    def _eye_result(self) -> list[dict]:
        """已确认的眼眶 mask + 各自的处理档位（同图混用两种方法）。"""
        return [{"mask": m,
                 "level": (self._eye_levels[i]
                           if i < len(self._eye_levels) else "auto")}
                for i, m in enumerate(self._eye_masks)]

    def _confirm_eye_mask(self):
        """回车闭合当前多边形；没有待闭合顶点时回车 = 完成返回。"""
        if self._eye_pts:
            self._close_eye_polygon()
            return
        if self._eye_masks:
            self._finish(self._eye_result())
            return
        self._flash("左键沿眼眶边界点一圈（至少 3 个顶点），"
                    "回车或点「闭合眼眶」闭合；右键拖动图片")

    def _finish(self, value):
        cb = self.done
        self.done = None
        if cb:
            cb(value)


# ------------------------------------------------------------------
# 主窗口
# ------------------------------------------------------------------
class PetSpaceWindow(QWidget):
    """独立的「桌宠空间」窗口：大缩略图，点图直接召唤到桌面，删除带确认。"""

    def __init__(self, owner):
        super().__init__(None)
        self.owner = owner
        self.setObjectName("petSpaceWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("桌宠空间")
        self.resize(1000, 720)
        self.setStyleSheet(SPACE_QSS)
        self._batch = False           # 批量删除模式
        self._selected: set[str] = set()
        self._pets_data: list[tuple[Path, dict, Path]] = []
        self._trash_win = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        title = QLabel("桌宠空间")
        title.setObjectName("spaceTitle")
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("spaceSub")
        self.btn_batch = QPushButton("批量删除")
        self.btn_batch.setObjectName("spaceBatch")
        self.btn_batch.setCheckable(True)
        self.btn_batch.setFixedHeight(34)
        self.btn_batch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_batch.toggled.connect(self._toggle_batch)
        self.btn_trash = QPushButton("回收箱")
        self.btn_trash.setObjectName("spaceTrashBtn")
        self.btn_trash.setFixedHeight(34)
        self.btn_trash.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_trash.clicked.connect(self._open_trash)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setFixedHeight(34)
        self.btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_refresh.clicked.connect(self.reload)
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        head.addWidget(self.btn_batch)
        head.addWidget(self.btn_trash)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        tip = QLabel("点图片直接把桌宠召唤到桌面；「改名」只换个显示名、不动素材文件；"
                     "「删除」会先移入回收箱，可随时恢复。")
        tip.setObjectName("spaceSub")
        root.addWidget(tip)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 4, 0, 4)
        self._grid.setSpacing(20)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 1)

        self._bar = QWidget()
        bl = QHBoxLayout(self._bar)
        bl.setContentsMargins(0, 0, 0, 0)
        self.lbl_sel = QLabel("已选 0 只")
        self.lbl_sel.setObjectName("spaceSub")
        self.btn_del_sel = QPushButton("删除选中")
        self.btn_del_sel.setObjectName("primary")
        self.btn_del_sel.setFixedHeight(34)
        self.btn_del_sel.setEnabled(False)
        self.btn_del_sel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_del_sel.clicked.connect(self._delete_selected)
        bl.addWidget(self.lbl_sel)
        bl.addStretch(1)
        bl.addWidget(self.btn_del_sel)
        self._bar.setVisible(False)
        root.addWidget(self._bar)
        self.reload()

    def paintEvent(self, _event):
        p = QPainter(self)
        paint_cute_backdrop(p, self.rect())

    def reload(self, *_):
        """重新扫描并重建所有卡片。"""
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        pets = self.owner._scan_saved_pets()
        self._pets_data = pets
        keys = {str(png) for _d, _it, png in pets}
        self._selected &= keys        # 已被删/移走的选中项自动丢弃
        self._update_sel_label()
        self.lbl_count.setText(f"已保存 {len(pets)} 只" if pets
                               else "还没有保存的桌宠")
        cols = 3
        for i, (out_dir, it, png) in enumerate(pets):
            self._grid.addWidget(self._make_card(out_dir, it, png),
                                 i // cols, i % cols)
        if not pets:
            self._grid.addWidget(make_empty_state(
                "还没有桌宠住进来～",
                "在主界面完成处理并点「生成素材并启动桌宠」，就会出现在这里。"),
                0, 0, 1, cols)
        rows = (len(pets) + cols - 1) // cols
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
        # 有卡片时把余量留到最后一行下方（卡片保持原高度）；
        # 空状态则让第 0 行撑开，小猫插画自然居中
        self._grid.setRowStretch(0 if not pets else max(rows, 1), 1)

    def _make_card(self, out_dir: Path, item: dict, png: Path) -> QWidget:
        stem = str(item.get("stem") or png.stem)
        name = str(item.get("label") or stem)     # label = 用户改过的显示名
        card = QFrame()
        card.setObjectName("spaceCard")
        card.setFixedWidth(286)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 14, 14, 12)
        cv.setSpacing(10)
        btn = QPushButton()
        btn.setObjectName("spaceThumb")
        btn.setFixedSize(250, 222)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pm = QPixmap(str(png))
        if not pm.isNull():
            pm = pm.scaled(240, 212, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            btn.setIcon(QIcon(pm))
            btn.setIconSize(pm.size())
        key = str(png)
        if self._batch:
            # 批量模式：点图片是“勾选”，不再召唤
            btn.setCheckable(True)
            btn.setChecked(key in self._selected)
            btn.setToolTip(f"勾选「{name}」")
            btn.clicked.connect(lambda on, k=key: self._pick(k, on))
        else:
            btn.setToolTip(f"点击把「{name}」召唤到桌面")
            btn.clicked.connect(
                lambda _=False, d=out_dir, it=item: self.owner._summon_pet(d, it))
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel()
        lbl.setToolTip(f"{name}\n（文件：{stem}.png）")
        lbl.setStyleSheet("color:#3e4a5c;font-weight:600;")
        lbl.setText(lbl.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, 78))
        edit_btn = QPushButton("修改")
        edit_btn.setObjectName("spaceRename")
        edit_btn.setFixedHeight(28)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit_btn.setToolTip("把这只桌宠放回主界面继续改；保存时覆盖同一份素材")
        edit_btn.clicked.connect(
            lambda _=False, d=out_dir, it=item: self._edit_one(d, it))
        rename_btn = QPushButton("改名")
        rename_btn.setObjectName("spaceRename")
        rename_btn.setFixedHeight(28)
        rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rename_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rename_btn.setToolTip("改一个显示名（只改名字，不动素材文件）")
        rename_btn.clicked.connect(
            lambda _=False, d=out_dir, it=item: self._rename_one(d, it))
        del_btn = QPushButton("删除")
        del_btn.setObjectName("spaceDel")
        del_btn.setFixedHeight(28)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.setToolTip("删除这只桌宠（会先确认）")
        del_btn.clicked.connect(
            lambda _=False, d=out_dir, it=item: self._delete_one(d, it))
        row.addWidget(lbl, 1)
        row.addWidget(edit_btn)
        row.addWidget(rename_btn)
        row.addWidget(del_btn)
        cv.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        cv.addLayout(row)
        return card

    def _toggle_batch(self, on: bool):
        self._batch = bool(on)
        self.btn_batch.setText("退出批量" if on else "批量删除")
        self._selected.clear()
        self._update_sel_label()
        self._bar.setVisible(bool(on))
        self.reload()

    def _pick(self, key: str, on: bool):
        if on:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._update_sel_label()

    def _update_sel_label(self):
        n = len(self._selected)
        self.lbl_sel.setText(f"已选 {n} 只")
        self.btn_del_sel.setEnabled(n > 0)
        self.btn_del_sel.setText(f"删除选中（{n}）" if n else "删除选中")

    def _delete_selected(self):
        pairs = [(d, it) for d, it, png in self._pets_data
                 if str(png) in self._selected]
        if not pairs:
            return
        ok = QMessageBox.question(
            self, "批量删除",
            f"确定把选中的 {len(pairs)} 只桌宠移入回收箱吗？\n之后可以在回收箱里恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.owner._trash_pets(pairs)
        self._selected.clear()
        self._update_sel_label()
        self.reload()
        self._refresh_trash()

    def _delete_one(self, out_dir: Path, item: dict):
        self.owner._delete_pet(out_dir, item, parent=self)
        self._refresh_trash()

    def _rename_one(self, out_dir: Path, item: dict):
        old = str(item.get("label") or item.get("stem") or "")
        new, ok = QInputDialog.getText(
            self, "重命名桌宠", "给它起个新名字：", QLineEdit.EchoMode.Normal, old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        if self.owner._rename_pet(out_dir, item, new):
            self.reload()

    def _edit_one(self, out_dir: Path, item: dict):
        """「修改」：把桌宠还原到主界面画布，改完点竖条保存就覆盖原素材。"""
        if self.owner._edit_pet(out_dir, item):
            self.hide()

    def _refresh_trash(self):
        win = self._trash_win
        try:
            if win is not None and win.isVisible():
                win.reload()
        except RuntimeError:
            self._trash_win = None

    def _open_trash(self):
        win = self._trash_win
        try:
            if win is not None:
                win.isVisible()
        except RuntimeError:
            win = None
        if win is None:
            win = PetTrashWindow(self.owner, self)
            self._trash_win = win
        win.reload()
        win.show()
        win.raise_()
        win.activateWindow()


class PetTrashWindow(QWidget):
    """回收箱：被删除的桌宠先放这里，可恢复，也可永久清空。"""

    def __init__(self, owner, space):
        super().__init__(None)
        self.owner = owner
        self.space = space
        self.setObjectName("petTrashWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("回收箱")
        self.resize(1000, 720)
        self.setStyleSheet(SPACE_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        title = QLabel("回收箱")
        title.setObjectName("spaceTitle")
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("spaceSub")
        self.btn_clear = QPushButton("清空回收箱")
        self.btn_clear.setObjectName("trashClear")
        self.btn_clear.setFixedHeight(34)
        self.btn_clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_clear.clicked.connect(self._clear)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedHeight(34)
        btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_refresh.clicked.connect(self.reload)
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        head.addWidget(self.btn_clear)
        head.addWidget(btn_refresh)
        root.addLayout(head)

        tip = QLabel("点「恢复」把桌宠放回桌宠空间；「清空回收箱」会永久删除这里的全部数据，无法恢复。")
        tip.setObjectName("spaceSub")
        tip.setWordWrap(True)
        root.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 4, 0, 4)
        self._grid.setSpacing(20)
        scroll.setWidget(self._host)
        root.addWidget(scroll, 1)
        self.reload()

    def paintEvent(self, _event):
        p = QPainter(self)
        paint_cute_backdrop(p, self.rect())

    def reload(self, *_):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        rows_data = self.owner._scan_trash()
        self.lbl_count.setText(f"{len(rows_data)} 只待处理" if rows_data
                               else "回收箱是空的")
        self.btn_clear.setEnabled(bool(rows_data))
        cols = 3
        for i, (tdir, meta, png) in enumerate(rows_data):
            self._grid.addWidget(self._make_card(tdir, meta, png),
                                 i // cols, i % cols)
        if not rows_data:
            self._grid.addWidget(make_empty_state(
                "回收箱是空的",
                "在桌宠空间删除的桌宠会先放到这里，可随时恢复。"),
                0, 0, 1, cols)
        rows = (len(rows_data) + cols - 1) // cols
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
        self._grid.setRowStretch(0 if not rows_data else max(rows, 1), 1)

    def _make_card(self, tdir: Path, meta: dict, png: Path | None) -> QWidget:
        name = str((meta.get("item") or {}).get("label")
                   or meta.get("stem") or tdir.name)
        card = QFrame()
        card.setObjectName("spaceCard")
        card.setFixedWidth(286)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 14, 14, 12)
        cv.setSpacing(10)
        btn = QPushButton()
        btn.setObjectName("spaceThumb")
        btn.setFixedSize(250, 222)
        btn.setEnabled(False)          # 回收箱里的图只看不召唤
        if png is not None:
            pm = QPixmap(str(png))
            if not pm.isNull():
                pm = pm.scaled(240, 212, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                btn.setIcon(QIcon(pm))
                btn.setIconSize(pm.size())
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(name if len(name) <= 14 else name[:13] + "…")
        lbl.setToolTip(f"{name}\n删除于 {meta.get('deleted_at', '')}")
        lbl.setStyleSheet("color:#3e4a5c;font-weight:600;")
        btn_back = QPushButton("恢复")
        btn_back.setObjectName("trashRestore")
        btn_back.setFixedHeight(28)
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_back.clicked.connect(lambda _=False, d=tdir: self._restore(d))
        row.addWidget(lbl, 1)
        row.addWidget(btn_back)
        cv.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        cv.addLayout(row)
        return card

    def _restore(self, tdir: Path):
        try:
            name = self.owner._restore_trash(tdir)
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", f"{exc}")
            return
        self.owner._log(f"[回收箱] 已恢复 {name} 到桌宠空间")
        self.owner._refresh_space()
        self.space.reload()
        self.reload()

    def _clear(self):
        n = len(self.owner._scan_trash())
        if n == 0:
            return
        ok = QMessageBox.question(
            self, "清空回收箱",
            f"确定永久删除回收箱里的 {n} 只桌宠吗？\n所有数据都会被删掉，无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.owner._empty_trash()
        self.owner._log(f"[回收箱] 已永久删除 {n} 只桌宠")
        self.reload()


class LaunchUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("")
        scr = QApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.resize(min(1280, g.width() - 40),
                        min(880, g.height() - 60))
        else:
            self.resize(1280, 860)
        self._running = False
        self._image_path: str = ""
        self._out_dir: Path | None = None
        self._pets: list[PetWindow] = []
        # 多只桌宠共用一个 pack：一个定时器驱动全部，并两两做碰撞分离。
        # 之前这里是各开各的窗口，两只桌宠会直接互相穿透。
        self._pack: PetPack | None = None
        # 抠图结果的状态：rgb/框/掩膜 + 输出路径；右侧选做步骤都基于它
        self._cut: dict | None = None
        self._hl_mask = None   # 当前高亮选区（裁切图坐标），切姿势/重绘时重新盖上
        self._sharpen = 0   # 边界锐化档位 0~3，剪切预览上方「边缘 −/+」调

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        right_inner = QWidget()
        right = QVBoxLayout(right_inner)
        right.setContentsMargins(0, 0, 0, 0)
        root.addLayout(left, 1)

        # ---- 左：标题 / 提示 / 画布（抠图结果直接画在这里） ----
        self.step_label = QLabel("准备就绪")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.step_label.setFont(f)
        self.step_label.setStyleSheet("color:#3e4a5c;")
        self.hint_label = QLabel(
            "选择图片后点「开始处理」：框取手办并抠图。左边是框选出来的内容、"
            "右边是白底预览；每一步上方都有按钮（确认 / 撤销 / 重来 / 跳过 / 取消）。"
            "抠完手办会按本来大小停在画布中间：眼睛跟着鼠标看、按时间轴眨眼、发缕和衣摆随风飘，"
            "并左右轻摇；点左侧竖条「眼睛」「头发」「衣服」只暂停摇摆，方便标注。")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color:#8d99a8;")
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.step_label, 1)
        # 输出参数收进齿轮：主界面只留步骤和预览
        self.btn_settings = GearButton()
        self.btn_settings.clicked.connect(lambda: self._open_settings())
        head.addWidget(self.btn_settings, 0, Qt.AlignmentFlag.AlignTop)
        left.addLayout(head)
        left.addWidget(self.hint_label)

        # 任务进度条：跑任务时来回走，结束时填满再收起
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(10)
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { background:#eaf3fb; border:none; border-radius:5px; }"
            "QProgressBar::chunk { background:#62cdb8; border-radius:5px; }")
        self._progress_running = False
        left.addWidget(self.progress)

        # 效果预览工具条：每步做完先在画布上播一遍效果，u 回退、s 完成
        self.pv_bar = QWidget()
        pb = QHBoxLayout(self.pv_bar)
        pb.setContentsMargins(0, 4, 0, 4)
        self.pv_label = QLabel("效果预览")
        self.btn_pv_back = QPushButton("回退 (u)")
        self.btn_pv_ok = QPushButton("保存 (s)")
        self.btn_pv_ok.setIcon(_save_icon())
        self.btn_pv_ok.setIconSize(QSize(16, 16))
        self.btn_pv_cancel = QPushButton("取消")
        # 边界锐化档位：0=不动，1~3 逐档加强，只作用于切割轮廓不去锐化整张图
        self.btn_sharpen_less = QPushButton("边缘 −")
        self.btn_sharpen_more = QPushButton("边缘 +")
        self.btn_sharpen_less.setToolTip("减弱轮廓锐化（0 = 不处理）")
        self.btn_sharpen_more.setToolTip(
            "加强切割边界：轮廓更实、2 档起把抠图吃掉的发丝补回来")
        self.lbl_sharpen = QLabel("0")
        self.lbl_sharpen.setToolTip("边界锐化档位 0~3（只动轮廓，不动画面内部）")
        self.lbl_sharpen.setMinimumWidth(24)
        self.lbl_sharpen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sharpen.setStyleSheet(
            "background:#fff6e8;border:2px solid #ffe2bd;border-radius:12px;"
            "color:#a06a1f;font-weight:700;")
        for b in (self.btn_pv_back, self.btn_pv_ok, self.btn_pv_cancel,
                  self.btn_sharpen_less, self.btn_sharpen_more):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_sharpen_less.clicked.connect(lambda: self._change_sharpen(-1))
        self.btn_sharpen_more.clicked.connect(lambda: self._change_sharpen(1))
        pb.addWidget(self.pv_label)
        pb.addWidget(self.btn_pv_back)
        pb.addWidget(self.btn_pv_ok)
        pb.addWidget(self.btn_pv_cancel)
        pb.addSpacing(10)
        pb.addWidget(self.btn_sharpen_less)
        pb.addWidget(self.lbl_sharpen)
        pb.addWidget(self.btn_sharpen_more)
        pb.addStretch(1)
        self.btn_pv_back.clicked.connect(lambda: self.canvas._finish("back"))
        self.btn_pv_ok.clicked.connect(lambda: self.canvas._finish("ok"))
        self.btn_pv_cancel.clicked.connect(lambda: self.canvas._finish(None))
        self.pv_bar.setVisible(False)
        left.addWidget(self.pv_bar)

        # 每步操作条：框选 / 补点 / 眼眶 / 发缕 都能用鼠标点，不必记快捷键
        self.act_bar = QWidget()
        self.act_lay = QHBoxLayout(self.act_bar)
        self.act_lay.setContentsMargins(0, 0, 0, 6)
        self.act_lay.setSpacing(6)
        self.act_bar.setVisible(False)
        left.addWidget(self.act_bar)

        self.canvas = ImageCanvas()
        self.canvas.setMinimumWidth(320)
        # 眼眶标注时闭合/撤销一只眼，时间轴跟着实时长/缩一行
        self.canvas.eye_rows_hook = self._on_eye_rows_changed

        # 画布缩放条（滚轮/触控板之外再给一组按钮，眼睛细节好放大看）
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        zoom_row.addStretch(1)
        self.btn_zoom_out = QPushButton("－")
        self.btn_zoom_in = QPushButton("＋")
        self.lbl_zoom = QLabel("100%")
        self.btn_zoom_fit = QPushButton("适应窗口")
        for b in (self.btn_zoom_out, self.btn_zoom_in, self.btn_zoom_fit):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        for b in (self.btn_zoom_out, self.btn_zoom_in):
            b.setFixedSize(30, 24)
        self.btn_zoom_fit.setFixedHeight(24)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_zoom.setFixedWidth(64)
        self.lbl_zoom.setStyleSheet("color:#58708a;font-weight:600;")
        self.btn_zoom_out.setToolTip("缩小")
        self.btn_zoom_in.setToolTip("放大")
        self.btn_zoom_fit.setToolTip("缩放到刚好铺满窗口")
        self.btn_zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.btn_zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        self.btn_zoom_fit.clicked.connect(self.canvas.fit)
        zoom_row.addWidget(self.btn_zoom_out)
        zoom_row.addWidget(self.lbl_zoom)
        zoom_row.addWidget(self.btn_zoom_in)
        zoom_row.addWidget(self.btn_zoom_fit)
        self.canvas.zoom_hook = lambda pct: self.lbl_zoom.setText(f"{pct}%")
        left.addLayout(zoom_row)

        # 画布右侧：白底预览（成品放在白底上的效果，方便看边缘干不干净）
        self.pv_wrap = QWidget()
        self.pv_wrap.setFixedWidth(320)
        pv_lay = QVBoxLayout(self.pv_wrap)
        pv_lay.setContentsMargins(0, 0, 0, 0)
        pv_lay.setSpacing(4)
        pv_title = QLabel("白底预览")
        pv_title.setStyleSheet("color:#58708a;font-weight:700;")
        pv_lay.addWidget(pv_title)
        self.board = PreviewBoard()
        pv_lay.addWidget(self.board, 1)
        self.pv_wrap.setVisible(False)

        view_row = QHBoxLayout()
        view_row.setSpacing(10)
        view_row.addWidget(self.canvas, 1)
        view_row.addWidget(self.pv_wrap)
        left.addLayout(view_row, 1)

        # ---- 右：参数面板 ----
        right.setContentsMargins(8, 0, 0, 0)

        g_img = QGroupBox("图片")
        v = QVBoxLayout(g_img)
        v.setSpacing(8)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.btn_pick = QPushButton("选择图片…")
        self.btn_pick.setMinimumHeight(34)
        self.btn_pick.clicked.connect(self._pick_image)
        self.lbl_image = QLabel("尚未选择")
        self.lbl_image.setWordWrap(True)
        self.lbl_image.setStyleSheet("color:#8d99a8;")
        pick_row.addWidget(self.btn_pick)
        pick_row.addWidget(self.lbl_image, 1)
        v.addLayout(pick_row)
        # 缩略图：卡片原来只有一行太窄，加一张预览图撑起来
        self.lbl_thumb = QLabel("选一张手办照片\n这里显示缩略图")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setMinimumHeight(132)
        self.lbl_thumb.setStyleSheet(
            "background:#fbfeff;border:2px dashed #dbe9f7;border-radius:16px;"
            "color:#aab6c4;")
        v.addWidget(self.lbl_thumb)
        right.addWidget(g_img)

        # 输出参数搬进齿轮「设置」，主界面右侧只留步骤与选项
        self.settings_dlg = self._make_settings_dialog()

        # ---- 选做：眼睛 / 头发 / 衣服 的切换按钮搬到右侧竖条，这里只放面板 ----
        self.btn_tab_eyes = QPushButton("眼睛")
        self.btn_tab_hair = QPushButton("头发")
        self.btn_tab_cloth = QPushButton("衣服")
        for b in (self.btn_tab_eyes, self.btn_tab_hair, self.btn_tab_cloth):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_eyes.setChecked(True)

        self.opt_stack = QStackedWidget()
        right.addWidget(self.opt_stack)

        # ---- 眼睛面板：档位 / 标注 / 眨眼时间轴 合并成一张卡 ----
        page_eyes = QWidget()
        ev = QVBoxLayout(page_eyes)
        ev.setContentsMargins(0, 4, 0, 0)
        ev.setSpacing(8)

        g_eye = QGroupBox("眼睛处理")
        gv = QVBoxLayout(g_eye)
        gv.setSpacing(6)

        erow = QHBoxLayout()
        erow.addWidget(QLabel("当前档位"))
        self.cb_eye_level = ClickComboBox()
        for text, lv in EYE_LEVELS:
            self.cb_eye_level.addItem(text, lv)
        self.cb_eye_level.setToolTip(
            "画下一只眼前先选档位；同一张图里可以逐只混用\n"
            "L2 移虹膜、L1/L0 整块微移")
        erow.addWidget(self.cb_eye_level, 1)
        gv.addLayout(erow)
        self.cb_eye_level.currentIndexChanged.connect(self._on_eye_level_changed)

        # 两个效果总开关：预览与生成的素材都按这里走
        srow = QHBoxLayout()
        srow.setSpacing(12)
        self.ck_gaze = CuteCheckBox("鼠标跟踪")
        self.ck_gaze.setChecked(True)
        self.ck_gaze.setToolTip("眼睛跟着鼠标动；关掉后眼睛定住不动（预览与桌宠都生效）")
        self.ck_gaze.stateChanged.connect(self._on_eye_switch)
        self.ck_blink = CuteCheckBox("眨眼")
        self.ck_blink.setChecked(True)
        self.ck_blink.setToolTip("关掉后完全不眨眼（预览与桌宠都生效）")
        self.ck_blink.stateChanged.connect(self._on_eye_switch)
        srow.addWidget(self.ck_gaze)
        srow.addWidget(self.ck_blink)
        srow.addStretch(1)
        gv.addLayout(srow)

        self.btn_eyes = QPushButton("眼睛处理…（眼眶标注）")
        self.btn_eyes.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_eyes.setEnabled(False)
        self.btn_eyes.clicked.connect(self._annotate_eyes)
        gv.addWidget(self.btn_eyes)

        lbl_eye = QLabel("每只眼按当前档位打标，时间轴里可按眼逐只改（L1-1、L1-2）")
        lbl_eye.setWordWrap(True)
        lbl_eye.setStyleSheet("color:#8d99a8;")
        gv.addWidget(lbl_eye)

        # 分节线 + 小标题：同一张卡里接着配眨眼时间轴（虚线留出呼吸感）
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.NoFrame)
        sep.setFixedHeight(9)
        sep.setStyleSheet(
            "border:none;border-top:2px dashed #e6eef9;background:transparent;")
        gv.addWidget(sep)
        lbl_blink = QLabel("眨眼时间轴")
        lbl_blink.setStyleSheet("color:#3e4a5c;font-weight:700;font-size:14px;")
        gv.addWidget(lbl_blink)

        bv = gv
        self.ck_blink_plan = CuteCheckBox("按时间轴眨眼（不勾选则随机眨眼）")
        self.ck_blink_plan.setChecked(True)
        self.ck_blink_plan.stateChanged.connect(self._refresh_pet)
        bv.addWidget(self.ck_blink_plan)

        self.tb_blink = QTableWidget(0, 4)
        self.tb_blink.setHorizontalHeaderLabels(
            ["眼睛", "当前档位", "时刻 (s)", "闭眼时长 (s)"])
        _hdr_tips = (
            "眼睛编号：一行一只眼，1 = 左眼、2 = 右眼、3 = 第三只眼…",
            "这一只眼的档位，如 L1-1（自动 / L2 / L1 / L0）；还没标眼眶时是 -",
            "在循环周期内的触发时刻（秒）",
            "闭眼保持时长（秒）",
        )
        for c, tip_text in enumerate(_hdr_tips):
            self.tb_blink.horizontalHeaderItem(c).setToolTip(tip_text)
        self.tb_blink.verticalHeader().setVisible(False)
        self.tb_blink.setAlternatingRowColors(True)
        self.tb_blink.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb_blink.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed)
        hh = self.tb_blink.horizontalHeader()
        hh.setMinimumSectionSize(44)
        # 一行一只眼：档位一列按内容给宽，时刻占用剩余空间
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tb_blink.setFixedHeight(100)
        self.tb_blink.itemChanged.connect(self._on_blink_cell_changed)
        self.tb_blink.itemSelectionChanged.connect(self._highlight_selection)
        bv.addWidget(self.tb_blink)

        brow = QHBoxLayout()
        self.btn_blink_del = QPushButton("删除选中")
        self.btn_blink_clear = QPushButton("清空")
        for b in (self.btn_blink_del, self.btn_blink_clear):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            brow.addWidget(b)
        self.btn_blink_del.clicked.connect(self._remove_blink_objects)
        self.btn_blink_clear.clicked.connect(self._clear_blink_objects)
        bv.addLayout(brow)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("循环周期"))
        self.sp_blink_cycle = ClickDoubleSpinBox()
        self.sp_blink_cycle.setRange(1.0, 120.0)
        self.sp_blink_cycle.setDecimals(1)
        self.sp_blink_cycle.setSingleStep(0.5)
        self.sp_blink_cycle.setValue(8.0)
        self.sp_blink_cycle.setSuffix(" s")
        self.sp_blink_cycle.valueChanged.connect(self._refresh_pet)
        crow.addWidget(self.sp_blink_cycle)
        bv.addLayout(crow)

        ev.addWidget(g_eye)
        self.opt_stack.addWidget(page_eyes)

        # ---- 头发面板 ----
        page_hair = QWidget()
        hv = QVBoxLayout(page_hair)
        hv.setContentsMargins(0, 4, 0, 0)
        hv.setSpacing(8)
        g_hair = QGroupBox("头发飘动")
        gh = QVBoxLayout(g_hair)
        gh.setSpacing(8)
        self.btn_hair = QPushButton("标注发缕…（多边形）")
        self.btn_hair.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_hair.setEnabled(False)
        self.btn_hair.clicked.connect(self._annotate_hair)
        gh.addWidget(self.btn_hair)
        lbl_hair = QLabel("沿一缕头发的轮廓点一圈（第一个顶点 = 发根），回车/「闭合发缕」闭合；"
                          "右键拖动可平移图片；"
                          "可继续画下一缕。标完会先播「头发飘动预览」："
                          "u 回退重画、s 保存。不标注就不做飘动，不影响出素材。")
        lbl_hair.setWordWrap(True)
        lbl_hair.setStyleSheet("color:#8d99a8;")
        gh.addWidget(lbl_hair)

        lbl_hair_list = QLabel("发缕列表（点一行会在上面的桌宠上亮起绿色流光）")
        lbl_hair_list.setStyleSheet("color:#3e4a5c;font-weight:700;font-size:14px;")
        gh.addWidget(lbl_hair_list)

        self.tb_hair = QTableWidget(0, 2)
        self.tb_hair.setHorizontalHeaderLabels(["发缕", "发梢 (x, y)"])
        self.tb_hair.horizontalHeaderItem(0).setToolTip("发缕编号：1 = 第一条画的多边形")
        self.tb_hair.horizontalHeaderItem(1).setToolTip(
            "静止时的发梢位置（裁切图坐标），用于算摆动力学方向")
        self.tb_hair.verticalHeader().setVisible(False)
        self.tb_hair.setAlternatingRowColors(True)
        self.tb_hair.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb_hair.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hv_hdr = self.tb_hair.horizontalHeader()
        hv_hdr.setMinimumSectionSize(44)
        hv_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hv_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tb_hair.setFixedHeight(100)
        self.tb_hair.itemSelectionChanged.connect(self._highlight_selection)
        gh.addWidget(self.tb_hair)

        hrow = QHBoxLayout()
        self.btn_hair_del = QPushButton("删除选中")
        self.btn_hair_clear = QPushButton("清空")
        for b in (self.btn_hair_del, self.btn_hair_clear):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            hrow.addWidget(b)
        self.btn_hair_del.clicked.connect(self._remove_hair_rows)
        self.btn_hair_clear.clicked.connect(self._clear_hair_rows)
        gh.addLayout(hrow)
        hv.addWidget(g_hair)
        hv.addStretch(1)
        self.opt_stack.addWidget(page_hair)

        # ---- 衣服 / 小物件面板：与头发同一套飘动逻辑 ----
        page_cloth = QWidget()
        cv = QVBoxLayout(page_cloth)
        cv.setContentsMargins(0, 4, 0, 0)
        cv.setSpacing(8)
        g_cloth = QGroupBox("衣服 / 小物件飘动")
        gc = QVBoxLayout(g_cloth)
        gc.setSpacing(8)
        self.btn_cloth = QPushButton("标注衣摆 / 小物件…（多边形）")
        self.btn_cloth.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_cloth.setEnabled(False)
        self.btn_cloth.clicked.connect(self._annotate_cloth)
        gc.addWidget(self.btn_cloth)
        lbl_cloth = QLabel("沿衣摆、袖子、挂饰等会飘的轮廓点一圈（第一个顶点 = 固定端），"
                           "回车/「闭合」闭合；右键拖动可平移图片；可继续画下一块。"
                           "标完会先播「衣服飘动预览」：u 回退重画、s 保存。"
                           "不标注就不做飘动，不影响出素材。")
        lbl_cloth.setWordWrap(True)
        lbl_cloth.setStyleSheet("color:#8d99a8;")
        gc.addWidget(lbl_cloth)

        lbl_cloth_list = QLabel("衣物列表（点一行会在上面的桌宠上亮起蓝色流光）")
        lbl_cloth_list.setStyleSheet("color:#3e4a5c;font-weight:700;font-size:14px;")
        gc.addWidget(lbl_cloth_list)

        self.tb_cloth = QTableWidget(0, 2)
        self.tb_cloth.setHorizontalHeaderLabels(["衣物", "末端 (x, y)"])
        self.tb_cloth.horizontalHeaderItem(0).setToolTip("衣物编号：1 = 第一块画的区域")
        self.tb_cloth.horizontalHeaderItem(1).setToolTip(
            "静止时的末端位置（裁切图坐标），用于算摆动力学方向")
        self.tb_cloth.verticalHeader().setVisible(False)
        self.tb_cloth.setAlternatingRowColors(True)
        self.tb_cloth.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb_cloth.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cv_hdr = self.tb_cloth.horizontalHeader()
        cv_hdr.setMinimumSectionSize(44)
        cv_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cv_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tb_cloth.setFixedHeight(100)
        self.tb_cloth.itemSelectionChanged.connect(self._highlight_selection)
        gc.addWidget(self.tb_cloth)

        crow = QHBoxLayout()
        self.btn_cloth_del = QPushButton("删除选中")
        self.btn_cloth_clear = QPushButton("清空")
        for b in (self.btn_cloth_del, self.btn_cloth_clear):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            crow.addWidget(b)
        self.btn_cloth_del.clicked.connect(self._remove_cloth_rows)
        self.btn_cloth_clear.clicked.connect(self._clear_cloth_rows)
        gc.addLayout(crow)
        cv.addWidget(g_cloth)
        cv.addStretch(1)
        self.opt_stack.addWidget(page_cloth)

        self.btn_tab_eyes.clicked.connect(lambda: self._switch_opt_page(0))
        self.btn_tab_hair.clicked.connect(lambda: self._switch_opt_page(1))
        self.btn_tab_cloth.clicked.connect(lambda: self._switch_opt_page(2))
        self.opt_stack.setCurrentIndex(0)
        right.addStretch(1)

        # 参数区放进滚动区：窗口变矮时也能滚到“眨眼时间轴”，按钮和日志常驻底部
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(right_inner)
        self._right_scroll = scroll

        self.btn_start = QPushButton("开始处理（框取手办并抠图）")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self._start)

        self.ck_launch = CuteCheckBox("生成后启动桌宠")
        self.ck_launch.setChecked(True)

        self.btn_build = QPushButton("生成素材并启动桌宠")
        self.btn_build.setObjectName("primary")
        self.btn_build.setMinimumHeight(36)
        self.btn_build.setEnabled(False)
        self.btn_build.clicked.connect(self._build)

        self.btn_space = QPushButton("宠物空间")
        self.btn_space.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_space.clicked.connect(self.open_pet_space)

        g_log = QGroupBox("日志")
        g_log.setMinimumHeight(70)
        g_log.setMaximumHeight(130)
        lv = QVBoxLayout(g_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet("font-family: monospace;")
        lv.addWidget(self.log)

        right_wrap = QWidget()
        right_wrap.setFixedWidth(424)
        right_wrap.setStyleSheet(PANEL_QSS)
        rw = QVBoxLayout(right_wrap)
        rw.setContentsMargins(2, 2, 2, 2)
        rw.setSpacing(10)
        rw.addWidget(scroll, 1)
        rw.addWidget(self.btn_start)
        rw.addWidget(self.ck_launch)
        rw.addWidget(g_log)

        # ---- 画布右侧竖条：每个功能一个小图标 ----
        self.btn_motion = QPushButton("动作参数")
        self.btn_motion.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_motion.clicked.connect(lambda: self._open_settings(anim=True))

        self.btn_table = QPushButton("小桌子")
        self.btn_table.setCheckable(True)
        self.btn_table.setEnabled(False)
        self.btn_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_table.clicked.connect(self._toggle_table)

        self.rail = QFrame()
        self.rail.setObjectName("sideRail")
        self.rail.setFixedWidth(68)
        self.rail.setFixedHeight(7 * 48 + 6 * 12 + 2 * 14 + 4)
        self.rail.setStyleSheet(PANEL_QSS)
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(9, 14, 9, 14)
        rl.setSpacing(12)
        for btn, kind, color, tip in (
                (self.btn_motion, "tune", "#e08a4f",
                 "动作参数：头发飘动 / 回弹 / 摇摆"),
                (self.btn_tab_cloth, "cloth", "#4fa8d8",
                 "衣服 / 小物件：标注飘动区域，和头发同一套物理"),
                (self.btn_tab_eyes, "eye", "#3b8fd4",
                 "眼睛：标注眼眶 / 调眨眼时间轴"),
                (self.btn_tab_hair, "hair", "#8a6fd4",
                 "头发：标注发缕 / 看飘动"),
                (self.btn_table, "table", "#b07a4c",
                 "小桌子：开启重力，桌宠从上方落到桌面上；下落系数在动作参数里"),
                (self.btn_build, "save", "#2f9c88",
                 "保存素材并启动桌宠（PNG / WebP / manifest）"),
                (self.btn_space, "house", "#c9854f",
                 "桌宠空间：查看已生成的桌宠，召唤 / 改名 / 删除 / 回收箱")):
            btn.setText("")
            btn.setObjectName("railBtn")
            btn.setIcon(_rail_icon(kind, color))
            btn.setIconSize(QSize(28, 28))
            btn.setFixedSize(48, 48)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rl.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch(1)
        rail_col = QVBoxLayout()
        rail_col.setContentsMargins(0, 0, 0, 0)
        rail_col.addStretch(1)
        rail_col.addWidget(self.rail, 0, Qt.AlignmentFlag.AlignVCenter)
        rail_col.addStretch(1)
        root.addLayout(rail_col, 0)
        root.addWidget(right_wrap, 0)

    def paintEvent(self, _event):
        p = QPainter(self)
        paint_cute_backdrop(p, self.rect())

    # ---------- 设置 ----------
    def _make_settings_dialog(self) -> QDialog:
        """齿轮「设置」：SKU / 名称 / 素材边长 / 显示高度 / 引擎 / 设备。

        控件还是原来的那批（self.ed_sku 等），生成流程读到的就是弹窗里的值，
        所以改完直接关掉即可，不需要「确定/取消」再回写一份。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("设置")
        dlg.setObjectName("settingsWin")
        dlg.setModal(False)
        dlg.setStyleSheet(PANEL_QSS + "\nQDialog#settingsWin{background:#fbfeff;}")
        dlg.setFixedWidth(420)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        g_out = QGroupBox("输出")
        grid = QVBoxLayout(g_out)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("SKU"))
        self.ed_sku = QLineEdit("custom")
        self.ed_sku.setToolTip("素材输出到 desktop_pet/assets/<SKU>/")
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
        self.sp_size = ClickSpinBox()
        self.sp_size.setRange(64, 1024)
        self.sp_size.setValue(512)
        row3.addWidget(self.sp_size)
        row3.addWidget(QLabel("显示高度"))
        self.sp_height = ClickSpinBox()
        self.sp_height.setRange(0, 2000)
        self.sp_height.setSpecialValueText("自动")
        row3.addWidget(self.sp_height)
        grid.addLayout(row3)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("引擎"))
        self.cb_engine = ClickComboBox()
        self.cb_engine.addItems(["auto", "sam", "grabcut"])
        row4.addWidget(self.cb_engine)
        row4.addWidget(QLabel("设备"))
        self.cb_device = ClickComboBox()
        self.cb_device.addItems(["cuda", "cpu"])
        row4.addWidget(self.cb_device)
        grid.addLayout(row4)
        v.addWidget(g_out)

        # 动作参数：头发飘动 / 身体摇摆 / 回弹弹簧，改完画布上的桌宠立刻按新参数动
        g_anim = QGroupBox("动作参数")
        ag = QGridLayout(g_anim)
        ag.setHorizontalSpacing(10)
        ag.setVerticalSpacing(8)

        def _anim_row(r, text, w, tip):
            ag.addWidget(QLabel(text), r, 0)
            w.setToolTip(tip)
            ag.addWidget(w, r, 1)

        # 拖动条 0~3.0×（0 = 停止），数字框可手输更高，如 5.0×
        self.sp_hair_speed = AnimSlider(value=1.0)
        _anim_row(0, "头发飘动速度", self.sp_hair_speed,
                  "拖动条 0~3.0×（0 = 停止）；也可以直接在右侧输入更大，如 5.0×")

        self.sp_hair_amp = AnimSlider(value=1.0)
        _anim_row(1, "头发摆幅", self.sp_hair_amp,
                  "发梢摆动的幅度，越大越飘；0 = 不摆")

        self.sp_hair_stiff = AnimSlider(value=1.0)
        _anim_row(2, "回弹刚度", self.sp_hair_stiff,
                  "弹簧回弹的力度：越大回得越急、越硬")

        self.sp_hair_damp = AnimSlider(value=1.0)
        _anim_row(3, "回弹阻尼", self.sp_hair_damp,
                  "停下来的快慢：越大越快稳定，越小晃得越久")

        self.sp_sway_deg = AnimSlider(lo=0.0, hi=8.0, value=2.4, step=0.2,
                                      suffix=" °")
        _anim_row(4, "摇摆角度", self.sp_sway_deg,
                  "身体左右摇摆的幅度，0 = 不摇（拖动条 0~8°，也可以手输更大）")

        self.sp_sway_speed = AnimSlider(value=1.0)
        _anim_row(5, "摇摆速度", self.sp_sway_speed,
                  "身体一左一右的快慢；0 = 停住不摇")

        self.sp_bounce = AnimSlider(lo=0.0, hi=1.0, value=0.45, step=0.05)
        _anim_row(6, "回弹系数", self.sp_bounce,
                  "小桌子模式下落到桌面的反弹力度：0 = 不弹直接停住，"
                  "越大弹得越高（0~1.0，也可以手输更大，1 = 原速弹回）")

        self.sp_fall = AnimSlider(lo=0.0, hi=3.0, value=1.0, step=0.1)
        _anim_row(7, "下落系数", self.sp_fall,
                  "小桌子模式下的下落加速度：越大掉得越快，0 = 悬在桌面上方不落")

        self.sp_plat = AnimSlider(lo=10.0, hi=2000.0, value=60.0, step=5.0,
                                  suffix=" %", decimals=0, log=True)
        _anim_row(8, "平台宽度", self.sp_plat,
                  "小桌子平台占整个画布的百分比（世界坐标里定死，滚轮缩放只动镜头、"
                  "桌板和桌宠一起等比缩放）：60% = 半个多画布（默认），100% = 铺满整个画布，"
                  "2000% = 画布 20 倍宽（视角缩到 5% 时也铺满）；开着桌子时拖动立刻生效")
        ag.setColumnStretch(1, 1)
        for w in (self.sp_hair_speed, self.sp_hair_amp, self.sp_hair_stiff,
                  self.sp_hair_damp, self.sp_sway_deg, self.sp_sway_speed,
                  self.sp_bounce, self.sp_fall, self.sp_plat):
            w.valueChanged.connect(self._apply_anim_params)
            # 手输数字时先不重建（重建会抢帧、把输入打断），输完再应用
            w.spin.editingFinished.connect(self._on_anim_edit_done)
        v.addWidget(g_anim)

        tip = QLabel("改完直接关掉即可：动作参数立刻作用在画布上的桌宠，"
                     "其余下次「生成素材」时生效。")
        tip.setStyleSheet("color:#8d99a8;")
        tip.setWordWrap(True)
        v.addWidget(tip)

        btn_done = QPushButton("完成")
        btn_done.setObjectName("primary")
        btn_done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_done.clicked.connect(dlg.accept)
        v.addWidget(btn_done, 0, Qt.AlignmentFlag.AlignRight)
        return dlg

    def _open_settings(self, anim: bool = False):
        """打开设置；anim=True 时（竖条「动作参数」）直接把光标落到动作常数第一项。"""
        dlg = self.settings_dlg
        dlg.adjustSize()
        if not dlg.isVisible():
            c = self.geometry().center()
            dlg.move(c.x() - dlg.width() // 2, c.y() - dlg.height() // 2)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        if anim:
            self.sp_hair_speed.spin.setFocus(Qt.FocusReason.OtherFocusReason)
            self.sp_hair_speed.spin.selectAll()

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
        pix = QPixmap(path)
        if not pix.isNull():
            self.lbl_thumb.setText("")
            self.lbl_thumb.setPixmap(pix.scaled(
                max(120, self.lbl_thumb.width() or 240), 132,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        if not self.ed_name.text():
            self.ed_name.setPlaceholderText(Path(path).stem + "_pet")
        # 换图后旧的剪切结果作废，否则选做/生成还会作用在上一张图上
        self._cut = None
        self.board.clear()
        self.pv_wrap.setVisible(False)
        self.step_label.setText("准备就绪")
        self._set_busy(False)
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

    # ---------- 桌宠空间 ----------
    def _scan_saved_pets(self) -> list[tuple[Path, dict, Path]]:
        """扫 assets/<sku>/manifest.json，列出所有已生成、图片还在的桌宠。"""
        out: list[tuple[Path, dict, Path]] = []
        if not DEFAULT_OUT.exists():
            return out
        for d in DEFAULT_OUT.iterdir():
            mp = d / "manifest.json"
            if not d.is_dir() or not mp.exists():
                continue
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for it in data.get("items") or []:
                stem = str(it.get("stem") or "")
                png = d / f"{stem}.png"
                if stem and png.exists():
                    out.append((d, it, png))
        out.sort(key=lambda t: t[2].stat().st_mtime, reverse=True)  # 新生成的排前面
        return out

    def open_pet_space(self):
        """打开独立的「桌宠空间」窗口（缩略图更大，点图直接召唤）。"""
        win = getattr(self, "_space_win", None)
        try:
            if win is not None:
                win.isVisible()
        except RuntimeError:      # 窗口已销毁
            win = None
        if win is None:
            win = PetSpaceWindow(self)
            self._space_win = win
        win.reload()
        win.show()
        win.raise_()
        win.activateWindow()

    def _refresh_space(self, *_):
        """生成 / 删除后刷新已打开的桌宠空间窗口。"""
        win = getattr(self, "_space_win", None)
        try:
            if win is not None and win.isVisible():
                win.reload()
        except RuntimeError:
            self._space_win = None

    def _summon_pet(self, out_dir: Path, item: dict):
        """点缩略图：把已经生成好的桌宠直接放回桌面。"""
        try:
            self._launch_pet(out_dir, item)
        except Exception as exc:
            self._log(f"[错误] 召唤桌宠失败：{exc}")
            return
        name = item.get("label") or item.get("stem")
        self._log(f"[桌宠空间] 已召唤 {name} 到桌面")

    def _rename_pet(self, out_dir: Path, item: dict, label: str) -> bool:
        """改显示名：只往 manifest 的 label 字段写，不动文件名。

        素材名（stem）还被 eyes_mask / hair mask 等引用着，改文件名会牵一
        发而动全身；所以这里只做一层「显示名」，没改过时照旧显示 stem。
        """
        stem = str(item.get("stem") or "")
        mp = out_dir / "manifest.json"
        if not stem or not mp.exists():
            return False
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._log(f"[错误] 读取 manifest 失败：{exc}")
            return False
        hit = None
        for it in data.get("items") or []:
            if it.get("stem") == stem:
                hit = it
                break
        if hit is None:
            self._log(f"[错误] manifest 里找不到 {stem}")
            return False
        if label and label != stem:
            hit["label"] = label
        else:
            hit.pop("label", None)      # 改回原名就删掉字段，保持 manifest 干净
        try:
            mp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        except OSError as exc:
            self._log(f"[错误] 写入 manifest 失败：{exc}")
            return False
        item["label"] = hit.get("label")     # 同步内存里的那一份，卡片立即用新名
        self._log(f"[桌宠空间] {stem} 改名为 {label}")
        return True

    # ---------- 修改已有桌宠 ----------
    def _edit_pet(self, out_dir: Path, item: dict) -> bool:
        """桌宠空间「修改」：把已生成桌宠的源图 + 全部标注还原到中间画布。

        之后点竖条「保存素材」走的就是同一条生成流程，sku / stem 都不变，
        所以 PNG / WebP / manifest / 眨眼时间轴会被原地覆盖。
        """
        stem = str(item.get("stem") or "")
        sku = out_dir.name
        source_dir = DEFAULT_CANON / sku
        src = source_dir / f"{stem}.png"
        msk = source_dir / f"{stem}_mask.png"
        if not stem or not src.exists() or not msk.exists():
            self._log(f"[错误] 找不到 {sku}/{stem} 的源图或掩膜，无法修改")
            self._log("        旧版本生成、源图已被清掉的桌宠只能重新抠图")
            return False
        img = np.array(Image.open(src).convert("RGB"))
        m = cv2.imread(str(msk), cv2.IMREAD_GRAYSCALE)
        if m is None or m.shape[:2] != img.shape[:2]:
            self._log(f"[错误] 掩膜 {msk.name} 尺寸与源图不一致，无法修改")
            return False

        mask = m > 127
        h, w = mask.shape
        rgb = np.ascontiguousarray(img)
        sockets, _hl = load_eye_masks(
            sorted(source_dir.glob(f"{stem}_eyes*.png")), mask.shape)
        levels = self._edit_levels(source_dir, stem, item, len(sockets))
        self._cut = {
            "path": str(src), "sku": sku, "stem": stem,
            "source_dir": source_dir, "out_dir": out_dir,
            "rgb": rgb,
            "rgba": np.dstack([rgb, np.where(mask, 255, 0).astype(np.uint8)]),
            "has_alpha": False,
            "box": (0, 0, w, h), "mask": mask, "pts": [], "cache": {},
            "sockets": sockets, "levels": levels,
            "hair": self._edit_wisps(source_dir, stem, "hair", mask.shape),
            "cloth": self._edit_wisps(source_dir, stem, "cloth", mask.shape),
            "src_png": src, "src_mask": msk,
        }
        self._out_dir = out_dir
        self._image_path = str(src)
        self.ed_sku.setText(sku)
        self.ed_name.setText(stem)
        self._hl_mask = None
        self._pet_key = None        # 换个来源，别拿上一只的渲染缓存
        self._apply_edit_anim(item.get("anim") or {})
        self._restore_edit_blink(source_dir, stem, item)
        self._sync_hair_table()
        self._sync_cloth_table()
        self._show_cutout()
        self._show_pet(animate=True)
        self._set_busy(False)
        self.btn_build.setToolTip("保存修改：覆盖这只桌宠原来的 PNG / WebP / manifest")
        self._step("修改中 · 改完点保存",
                   f"已载入「{item.get('label') or stem}」：眼睛 / 头发 / 衣服 / 动作参数 / "
                   "眨眼时间轴都能接着改；改完点竖条「保存素材」，覆盖同一份素材")
        self._log(f"[修改] 载入 {sku}/{stem}：保存会覆盖原素材")
        self.raise_()
        self.activateWindow()
        return True

    @staticmethod
    def _edit_wisps(source_dir: Path, stem: str, kind: str, shape) -> list[dict]:
        """读 <stem>_hair/cloth.json + 对应 mask，还原成画布用的整图坐标。"""
        p = source_dir / f"{stem}_{kind}.json"
        out: list[dict] = []
        if not p.exists():
            return out
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return out
        for it in data.get("wisps") or []:
            try:
                mp = source_dir / str(it.get("mask") or "")
                m = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE)
                if m is None or m.shape[:2] != tuple(shape[:2]):
                    continue
                mm = m > 127
                if not mm.any():
                    continue
                pivot = (float(it["pivot"][0]), float(it["pivot"][1]))
                tip = (float(it["tip"][0]), float(it["tip"][1]))
            except (KeyError, TypeError, ValueError, IndexError, OSError):
                continue
            out.append({"mask": mm, "pivot": pivot, "tip": tip})
        return out

    @staticmethod
    def _edit_levels(source_dir: Path, stem: str, item: dict, n: int) -> list[str]:
        """逐只眼的处理档位：优先用 manifest，其次 <stem>_eyes.json，缺的补 auto。"""
        levels = [str(v) for v in (item.get("eye_level") or [])]
        if not levels:
            p = source_dir / f"{stem}_eyes.json"
            try:
                levels = [str(v) for v in
                          (json.loads(p.read_text(encoding="utf-8")).get("eye_level") or [])]
            except (OSError, json.JSONDecodeError):
                levels = []
        return (levels + ["auto"] * n)[:n]

    def _apply_edit_anim(self, anim: dict):
        """还原动作参数的拖动条（setValue 不发信号，画布不会抖一下）。"""
        for name, key, default in (
                ("sp_hair_speed", "hair_speed", 1.0),
                ("sp_hair_amp", "hair_amp", 1.0),
                ("sp_hair_stiff", "hair_stiff", 1.0),
                ("sp_hair_damp", "hair_damp", 1.0),
                ("sp_sway_deg", "sway_deg", 2.4),
                ("sp_sway_speed", "sway_speed", 1.0),
                ("sp_bounce", "bounce", 0.45),
                ("sp_fall", "fall", 1.0)):
            w = getattr(self, name, None)
            if w is None:
                continue
            try:
                w.setValue(float(anim.get(key, default)))
            except (TypeError, ValueError):
                pass
        # 平台宽度在界面上是 10~100%，manifest 里存 0.1~1.0
        sp = getattr(self, "sp_plat", None)
        if sp is not None:
            try:
                sp.setValue(float(anim.get("plat", 0.6)) * 100.0)
            except (TypeError, ValueError):
                pass

    def _restore_edit_blink(self, source_dir: Path, stem: str, item: dict):
        """还原眨眼时间轴：勾选状态、循环周期，以及逐只眼的时刻 / 时长。"""
        plan = item.get("blink")
        if not isinstance(plan, dict):
            p = source_dir / f"{stem}_blink.json"
            try:
                plan = (json.loads(p.read_text(encoding="utf-8"))
                        .get("blink") or {})
            except (OSError, json.JSONDecodeError):
                plan = {}
        cycle = 8.0
        try:
            cycle = max(1.0, float(plan.get("cycle", 8.0)))
        except (TypeError, ValueError):
            pass
        for w, key, default in ((self.ck_blink, "blink", True),
                                (self.ck_gaze, "gaze", True),
                                (self.ck_blink_plan, "enabled", True)):
            w.blockSignals(True)
            w.setChecked(bool(plan.get(key, default)))
            w.blockSignals(False)
        self.sp_blink_cycle.blockSignals(True)
        self.sp_blink_cycle.setValue(cycle)
        self.sp_blink_cycle.blockSignals(False)

        self._sync_blink_level_texts()
        # 一只眼一行：按「档位-眼号」把存下来的时刻 / 时长写回对应行
        times: dict[int, tuple[float, float]] = {}
        for it in plan.get("items") or []:
            try:
                t = max(0.0, min(cycle - 0.01, float(it.get("t", 2.0))))
                hold = min(3.0, max(0.03, float(it.get("hold", 0.2))))
            except (TypeError, ValueError):
                continue
            eyes = []
            for tok in it.get("levels") or []:
                mt = re.search(r"(\d+)\s*$", str(tok))
                if mt:
                    eyes.append(int(mt.group(1)))
            if not eyes:
                try:
                    eyes = [int(it.get("group", 1))]
                except (TypeError, ValueError):
                    eyes = []
            for eye in eyes:
                times[eye] = (t, hold)
        if not times:
            return
        self.tb_blink.blockSignals(True)
        for r in range(self.tb_blink.rowCount()):
            it0 = self.tb_blink.item(r, 0)
            if it0 is None:
                continue
            hit = times.get(int(it0.data(Qt.ItemDataRole.UserRole)))
            if hit is None:
                continue
            for col, val in ((2, hit[0]), (3, hit[1])):
                cell = self.tb_blink.item(r, col)
                if cell is None:
                    continue
                cell.setText(f"{val:.2f}")
                cell.setData(Qt.ItemDataRole.UserRole, float(val))
        self.tb_blink.blockSignals(False)

    def _delete_pet(self, out_dir: Path, item: dict, parent=None):
        name = str(item.get("label") or item.get("stem") or "")
        ok = QMessageBox.question(
            parent or self, "删除桌宠",
            f"确定删除「{name}」吗？\n会先移入回收箱，之后可以恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        if self._trash_pets([(out_dir, item)]):
            self._log(f"[桌宠空间] {name} 已移入回收箱")

    # ---------- 回收箱（软删除） ----------
    def _trash_root(self) -> Path:
        return DEFAULT_OUT / ".trash"

    def _trash_pets(self, pairs) -> int:
        """把若干只桌宠移入回收箱（单删 / 批量删除共用）。"""
        moved = 0
        for out_dir, item in pairs:
            name = str(item.get("stem") or "")
            try:
                self._close_pet(out_dir / f"{name}.png")
                self._move_to_trash(out_dir, item)
                moved += 1
            except Exception as exc:
                self._log(f"[错误] {name} 移入回收箱失败：{exc}")
        self._refresh_space()
        return moved

    def _move_to_trash(self, out_dir: Path, item: dict):
        """把素材文件挪进 assets/.trash/，并写一份能还原的 meta.json。"""
        name = str(item.get("stem") or "")
        stamp = (time.strftime("%Y%m%d-%H%M%S")
                 + f"-{time.time_ns() % 1000000:06d}")
        tdir = self._trash_root() / f"{out_dir.name}__{name}__{stamp}"
        tdir.mkdir(parents=True, exist_ok=True)
        files = []
        srcs = [out_dir / f"{name}.png", out_dir / f"{name}.webp",
                out_dir / f"{name}_preview.png"]
        srcs += list(out_dir.glob(f"{name}_eyes*.png"))
        for p in srcs:
            if p.exists():
                shutil.move(str(p), str(tdir / p.name))
                files.append(p.name)
        if not files:
            shutil.rmtree(tdir, ignore_errors=True)
            raise RuntimeError("找不到这只桌宠的素材文件")
        meta = {"sku": out_dir.name, "stem": name, "item": item,
                "files": files, "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        (tdir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._remove_manifest_item(out_dir, name)

    def _remove_manifest_item(self, out_dir: Path, name: str):
        mp = out_dir / "manifest.json"
        if not mp.exists():
            return
        data = json.loads(mp.read_text(encoding="utf-8"))
        data["items"] = [it for it in data.get("items") or []
                         if it.get("stem") != name]
        mp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    def _scan_trash(self) -> list[tuple[Path, dict, Path | None]]:
        root = self._trash_root()
        out: list[tuple[Path, dict, Path | None]] = []
        if not root.exists():
            return out
        for d in root.iterdir():
            mp = d / "meta.json"
            if not d.is_dir() or not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            png = d / f"{meta.get('stem')}.png"
            out.append((d, meta, png if png.exists() else None))
        out.sort(key=lambda t: t[1].get("deleted_at", ""), reverse=True)
        return out

    def _restore_trash(self, tdir: Path) -> str:
        """恢复一只：文件搬回原 sku 目录，并把 manifest 条目写回去。"""
        meta = json.loads((tdir / "meta.json").read_text(encoding="utf-8"))
        sku = str(meta.get("sku") or "")
        stem = str(meta.get("stem") or "")
        if not sku or not stem or not meta.get("item"):
            raise RuntimeError("回收箱记录不完整")
        out_dir = DEFAULT_OUT / sku
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in meta.get("files") or []:
            src = tdir / name
            if src.exists():
                shutil.move(str(src), str(out_dir / name))
        mp = out_dir / "manifest.json"
        data = (json.loads(mp.read_text(encoding="utf-8")) if mp.exists()
                else {"schema": "pet-asset/1", "sku": sku, "items": []})
        data["items"] = [it for it in data.get("items") or []
                         if it.get("stem") != stem] + [meta["item"]]
        mp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        shutil.rmtree(tdir, ignore_errors=True)
        return stem

    def _empty_trash(self):
        """清空回收箱：永久删除，无法恢复。"""
        shutil.rmtree(self._trash_root(), ignore_errors=True)

    def _close_pet(self, src: Path):
        """关掉桌面上正在显示、且素材是 src 的桌宠（替换/删除时用）。"""
        for old in list(self._pets):
            try:
                same = getattr(old.asset, "source", None) == src
            except RuntimeError:      # 底层窗口已销毁
                same = True
            if same:
                self._pets.remove(old)
                old.close()

    def _launch_pet(self, out_dir: Path, item: dict, height: int = 0) -> PetWindow:
        """从已生成的素材加载并显示一只桌宠（生成流程与桌宠空间共用）。"""
        src = out_dir / f"{item['stem']}.png"
        self._close_pet(src)
        asset = PetAsset.load(src,
                              eyes=item.get("eyes"),
                              hair=item.get("hair"),
                              cloth=item.get("cloth"),
                              blink=item.get("blink"),
                              eye_masks=[out_dir / n for n in
                                         (item.get("eyes_mask") or [])],
                              eye_level=item.get("eye_level"),
                              anim=item.get("anim"))
        a = item.get("anchor") or {}
        asset.anchor = (float(a.get("x", 0.5)), float(a.get("y", 0.99)))
        if self._pack is None:
            self._pack = PetPack()
        win = PetWindow(asset, height or item.get("display_height_px") or 0,
                        pack=self._pack)
        win.show()
        if self._pack.started:
            self._pack.place_new(win)     # 已有桌宠时贴空位落地，不挤动旧的
        self._pack.start()                # 第一只：铺开；其余只确保定时器在跑
        win.raise_()                      # 置顶，保证立刻看得到
        self._pets.append(win)
        return win

    # ---------- 眼睛处理档位 ----------
    def _on_eye_level_changed(self, _index: int = 0):
        """把「当前档位」同步给画布：下一只闭合的眼眶按它打标。

        可以先画一只 L2、切到 L0 再画另一只，同一张图里混用两种处理方法。
        """
        lv = self.cb_eye_level.currentData() or "auto"
        self.canvas.eye_level = lv

    def _on_eye_switch(self, _state: int = 0):
        """「鼠标跟踪 / 眨眼」总开关：眨眼关掉时时间轴整块置灰。"""
        on = getattr(self, "ck_blink", None)
        blink_on = True if on is None else on.isChecked()
        for w in (getattr(self, "ck_blink_plan", None),
                  getattr(self, "tb_blink", None),
                  getattr(self, "btn_blink_del", None),
                  getattr(self, "btn_blink_clear", None)):
            if w is not None:
                w.setEnabled(blink_on)
        self._refresh_pet()   # 开关一变，中间的桌宠立刻跟着眨 / 不眨

    def _switch_opt_page(self, index: int):
        """切换「眼睛 / 头发 / 衣服」面板，并把页签滚到顶部，方便直接看面板。

        点这三个页签时，中间桌宠暂停左右摇摆，眨眼 / 目光 / 飘发 / 衣摆照常，方便看和标注。
        """
        self.opt_stack.setCurrentIndex(index)
        # 程序化切换时同步竖条按钮的选中态（autoExclusive 只在鼠标点击时自动处理）
        if hasattr(self, "btn_tab_eyes"):
            (self.btn_tab_eyes, self.btn_tab_hair,
             self.btn_tab_cloth)[index].setChecked(True)
        scroll = getattr(self, "_right_scroll", None)
        if scroll is not None:
            bar = scroll.verticalScrollBar()
            bar.setValue(max(0, self.opt_stack.y() - 6))
        if not self._running and self._cut:
            self._show_pet(animate=True)
        self._highlight_selection()

    # ---------- 选区高亮 ----------
    def _picked_masks(self) -> list:
        """当前面板里选中的眼睛 / 发缕 / 衣物 mask（没选返回空列表）。"""
        st = self._cut or {}
        idx = self.opt_stack.currentIndex()
        if idx == 0:
            sockets = st.get("sockets") or []
            out = []
            for r in {i.row() for i in self.tb_blink.selectedIndexes()}:
                it = self.tb_blink.item(r, 0)
                if it is None:
                    continue
                eye = int(it.data(Qt.ItemDataRole.UserRole) or 0)
                if 1 <= eye <= len(sockets):
                    out.append(sockets[eye - 1])
            return out
        items = st.get("cloth" if idx == 2 else "hair") or []
        out = []
        table = self.tb_cloth if idx == 2 else self.tb_hair
        for r in {i.row() for i in table.selectedIndexes()}:
            if 0 <= r < len(items):
                m = items[r].get("mask")
                if m is not None:
                    out.append(m)
        return out

    def _highlight_selection(self):
        """把表格里选中的眼睛 / 发缕在画布桌宠上高亮成流动绿光。

        表格里存的是整图坐标的 mask，画布上的桌宠是裁切后的图，
        这里要先按框裁掉再用，否则绿光会整体偏移、缩放对不上。
        """
        masks = self._picked_masks()
        box = (self._cut or {}).get("box")
        if masks and box:
            x0, y0, x1, y1 = box
            masks = [np.asarray(m, bool)[y0:y1, x0:x1] for m in masks]
            masks = [m for m in masks if m.any()]
        mask = None
        if len(masks) == 1:
            mask = np.asarray(masks[0], bool)
        elif masks:
            mask = np.zeros_like(masks[0], bool)
            for m in masks:
                mask |= np.asarray(m, bool)
        self._hl_mask = mask
        self.canvas.show_highlight(mask)

    # ---------- 发缕列表 ----------
    def _sync_hair_table(self):
        hair = (self._cut or {}).get("hair") or []
        self.tb_hair.blockSignals(True)
        self.tb_hair.setRowCount(0)
        for i, w in enumerate(hair):
            self.tb_hair.insertRow(i)
            it = QTableWidgetItem(f"第 {i + 1} 缕")
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb_hair.setItem(i, 0, it)
            try:
                tip = w.get("tip") or (0, 0)
                txt = f"{float(tip[0]):.0f}, {float(tip[1]):.0f}"
            except (TypeError, ValueError, IndexError):
                txt = "-"
            it2 = QTableWidgetItem(txt)
            it2.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb_hair.setItem(i, 1, it2)
        self.tb_hair.blockSignals(False)
        self._highlight_selection()

    def _remove_hair_rows(self):
        if not self._cut:
            return
        rows = sorted({i.row() for i in self.tb_hair.selectedIndexes()},
                      reverse=True)
        if not rows:
            self._log("[提示] 先选中要删除的发缕")
            return
        hair = list(self._cut.get("hair") or [])
        for r in rows:
            if 0 <= r < len(hair):
                hair.pop(r)
        self._apply_hair(hair)

    def _clear_hair_rows(self):
        if not self._cut:
            return
        self._apply_hair([])

    def _apply_hair(self, hair: list):
        """删掉/清空发缕后重新落盘，并刷新桌宠预览（已生成素材不受影响）。"""
        st = self._cut
        st["hair"] = hair
        x0, y0, x1, y1 = st["box"]
        try:
            launch.save_hair_polys(st["source_dir"], st["stem"],
                                   (x1 - x0, y1 - y0), hair, (x0, y0),
                                   force=True)
        except Exception as exc:      # noqa: BLE001  （落盘失败不该打断界面）
            self._log(f"[错误] 发缕标注保存失败：{exc}")
        self._log(f"    ✓ 发缕已更新（{len(hair)} 缕）" if hair
                  else "    ✓ 发缕已清空，重新生成的桌宠不再做头发飘动")
        self._sync_hair_table()
        if not self._running:
            self._show_pet(animate=True)

    # ---------- 衣物列表 ----------
    def _sync_cloth_table(self):
        cloth = (self._cut or {}).get("cloth") or []
        self.tb_cloth.blockSignals(True)
        self.tb_cloth.setRowCount(0)
        for i, w in enumerate(cloth):
            self.tb_cloth.insertRow(i)
            it = QTableWidgetItem(f"第 {i + 1} 块")
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb_cloth.setItem(i, 0, it)
            try:
                tip = w.get("tip") or (0, 0)
                txt = f"{float(tip[0]):.0f}, {float(tip[1]):.0f}"
            except (TypeError, ValueError, IndexError):
                txt = "-"
            it2 = QTableWidgetItem(txt)
            it2.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb_cloth.setItem(i, 1, it2)
        self.tb_cloth.blockSignals(False)
        self._highlight_selection()

    def _remove_cloth_rows(self):
        if not self._cut:
            return
        rows = sorted({i.row() for i in self.tb_cloth.selectedIndexes()},
                      reverse=True)
        if not rows:
            self._log("[提示] 先选中要删除的衣物")
            return
        cloth = list(self._cut.get("cloth") or [])
        for r in rows:
            if 0 <= r < len(cloth):
                cloth.pop(r)
        self._apply_cloth(cloth)

    def _clear_cloth_rows(self):
        if not self._cut:
            return
        self._apply_cloth([])

    def _apply_cloth(self, cloth: list):
        """删掉/清空衣物后重新落盘，并刷新桌宠预览（已生成素材不受影响）。"""
        st = self._cut
        st["cloth"] = cloth
        x0, y0, x1, y1 = st["box"]
        try:
            launch.save_hair_polys(st["source_dir"], st["stem"],
                                   (x1 - x0, y1 - y0), cloth, (x0, y0),
                                   force=True, kind="cloth")
        except Exception as exc:      # noqa: BLE001  （落盘失败不该打断界面）
            self._log(f"[错误] 衣物标注保存失败：{exc}")
        self._log(f"    ✓ 衣物已更新（{len(cloth)} 块）" if cloth
                  else "    ✓ 衣物已清空，重新生成的桌宠不再做衣摆飘动")
        self._sync_cloth_table()
        if not self._running:
            self._show_pet(animate=True)

    # ---------- 眨眼时间轴 ----------
    def _add_blink_row(self, t: float, hold: float, eye: int | None = None,
                       level: str | None = None, text: str = ""):
        """新增一行「一只眼」的时间轴；还没标眼眶时档位列显示「-」。"""
        self.tb_blink.blockSignals(True)
        r = self.tb_blink.rowCount()
        self.tb_blink.insertRow(r)

        if eye is None:
            used = [int(self.tb_blink.item(rr, 0).data(Qt.ItemDataRole.UserRole))
                    for rr in range(r)
                    if self.tb_blink.item(rr, 0) is not None]
            eye = (max(used) + 1) if used else 1
        it = QTableWidgetItem(str(int(eye)))
        it.setData(Qt.ItemDataRole.UserRole, int(eye))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tb_blink.setItem(r, 0, it)

        if not text:
            if level is None:
                text = "-"      # 还没标注眼眶：留空占位
            else:
                base = EYE_LEVEL_SHORT.get(level, "自动")
                text = f"{base}-{int(eye)}"
        it = QTableWidgetItem(text)
        it.setData(Qt.ItemDataRole.UserRole, text)
        it.setToolTip("这一只眼的档位，写法「档位-眼号」，如 L1-1；"
                      "还没标眼眶时是 -")
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tb_blink.setItem(r, 1, it)

        for col, val in ((2, float(t)), (3, float(hold))):
            it = QTableWidgetItem(f"{val:.2f}")
            it.setData(Qt.ItemDataRole.UserRole, float(val))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb_blink.setItem(r, col, it)
        self.tb_blink.blockSignals(False)

    def _remove_blink_objects(self):
        rows = sorted({i.row() for i in self.tb_blink.selectedIndexes()},
                      reverse=True)
        if not rows:
            self._log("[提示] 先选中要删除的眨眼对象")
            return
        self.tb_blink.blockSignals(True)
        for r in rows:
            self.tb_blink.removeRow(r)
        self.tb_blink.blockSignals(False)
        self._highlight_selection()

    def _clear_blink_objects(self):
        self.tb_blink.blockSignals(True)
        self.tb_blink.setRowCount(0)
        self.tb_blink.blockSignals(False)
        self._highlight_selection()

    @staticmethod
    def _parse_eye_levels(text: str):
        """解析「L1-1,L0-2」→ (规范化文本, [(眼号, 档位), …])；格式错误返回 None。

        还没标眼眶时单元格里是「-」，按空配置处理（生成时退回自动判定）。
        """
        raw_text = (text or "").strip()
        if raw_text in ("", "-", "—", "无"):
            return "-", []
        toks = []
        for raw in re.split(r"[，,、;；\s]+", raw_text):
            if not raw:
                continue
            m = re.fullmatch(r"(自动|auto|AUTO|l0|L0|l1|L1|l2|L2)-(\d{1,2})", raw)
            if not m:
                return None
            lv = m.group(1)
            lv = "auto" if lv.lower() == "auto" else lv.upper()
            eye = int(m.group(2))
            if eye < 1:
                return None
            toks.append((eye, lv))
        if not toks:
            return None
        toks.sort(key=lambda t: t[0])
        norm = ",".join(f"{EYE_LEVEL_SHORT.get(lv, lv)}-{eye}" for eye, lv in toks)
        return norm, toks

    def _on_blink_cell_changed(self, item):
        """单元格改完立刻校验：眼号 ≥ 1、档位格式、时刻在周期内、时长合理。"""
        col = item.column()
        prev = item.data(Qt.ItemDataRole.UserRole)
        if col == 1:
            parsed = self._parse_eye_levels(item.text())
            if parsed is None:
                self._log("[提示] 档位写法应为「档位-眼号」，如 L1-1，已还原")
                text = str(prev) if prev else "-"
            else:
                text = parsed[0]
                item.setData(Qt.ItemDataRole.UserRole, text)
            if item.text() != text:
                self.tb_blink.blockSignals(True)
                item.setText(text)
                self.tb_blink.blockSignals(False)
            return
        try:
            val = float(item.text())
        except ValueError:
            val = prev
            self._log("[提示] 眨眼时间要填数字，已还原")
        if col == 0:
            val = max(1, int(round(float(val))))
            text = str(val)
        elif col == 2:
            val = max(0.0, min(float(self.sp_blink_cycle.value()) - 0.01, val))
            text = f"{val:.2f}"
        elif col == 3:
            val = min(3.0, max(0.03, val))
            text = f"{val:.2f}"
        else:
            return
        item.setData(Qt.ItemDataRole.UserRole,
                     val if col == 0 else float(val))
        if item.text() != text:
            self.tb_blink.blockSignals(True)
            item.setText(text)
            self.tb_blink.blockSignals(False)
        self._refresh_pet()   # 时刻 / 时长改完立刻在桌宠上重播

    def _blink_rows(self) -> list[dict]:
        """一行 = 一只眼：{eye, level, t, hold}（count 固定 1）。"""
        rows = []
        for r in range(self.tb_blink.rowCount()):
            items = [self.tb_blink.item(r, c) for c in (0, 1, 2, 3)]
            if any(it is None for it in items):
                continue
            eye = int(items[0].data(Qt.ItemDataRole.UserRole))
            parsed = self._parse_eye_levels(items[1].text())
            if parsed is None:
                parsed = ("-", [])
            toks = [{"eye": e, "level": lv} for e, lv in parsed[1]]
            lv = next((t["level"] for t in toks if t["eye"] == eye),
                       toks[0]["level"] if toks else "auto")
            rows.append({
                "eye": eye,
                "group": eye,
                "levels": [{"eye": eye, "level": lv}],
                "level": lv,
                "t": float(items[2].data(Qt.ItemDataRole.UserRole)),
                "hold": float(items[3].data(Qt.ItemDataRole.UserRole)),
                "count": 1,
            })
        rows.sort(key=lambda it: (it["t"], it["eye"]))
        return rows

    def _blink_plan(self) -> dict:
        blink_on = self.ck_blink.isChecked()
        return {"enabled": blink_on and self.ck_blink_plan.isChecked(),
                "blink": blink_on,
                "gaze": self.ck_gaze.isChecked(),
                "cycle": float(self.sp_blink_cycle.value()),
                "items": self._blink_rows()}

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
                   allow_skip=False, full_key=True, color="lime", band=24,
                   boxes=None, masks=None, levels=None, hair=None):
        loop = QEventLoop()
        result = {"v": None}

        def on_done(value):
            result["v"] = value
            if loop.isRunning():
                loop.quit()

        self.canvas.enter_mode(mode, box=box, points=points,
                               allow_skip=allow_skip, full_key=full_key,
                               color=color, on_done=on_done, band=band,
                               boxes=boxes, masks=masks, levels=levels,
                               hair=hair)
        self.hint_label.setText(hint)
        self._set_actions(mode)
        self.canvas.setFocus()
        loop.exec()
        self.act_bar.setVisible(False)
        self.canvas.set_idle()
        return result["v"]

    def _set_actions(self, mode: str):
        """把当前这一步能做的操作排成按钮（和键盘 u/r/s/回车 等价）。"""
        while self.act_lay.count():
            it = self.act_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        specs = {
            "box": [("确认框选", "confirm"), ("整图", "full"),
                    ("重新框选", "reset"), ("取消", "escape")],
            "points": [("重新分割", "confirm"), ("撤销点", "undo"),
                       ("清空点", "reset"), ("跳过", "skip"),
                       ("取消", "escape")],
            "eyes": [("闭合眼眶", "confirm"), ("完成", "done"),
                     ("撤销", "undo"), ("重来", "reset"),
                     ("跳过", "skip"), ("取消", "escape")],
            "hair": [("闭合发缕", "confirm"), ("完成", "done"),
                     ("撤销", "undo"), ("重来", "reset"),
                     ("跳过", "skip"), ("取消", "escape")],
            "cloth": [("闭合衣物", "confirm"), ("完成", "done"),
                      ("撤销", "undo"), ("重来", "reset"),
                      ("跳过", "skip"), ("取消", "escape")],
        }.get(mode, [])
        for label, act in specs:
            b = QPushButton(label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(
                lambda _checked=False, a=act: self.canvas.run_action(a))
            self.act_lay.addWidget(b)
        self.act_lay.addStretch(1)
        self.act_bar.setVisible(bool(specs))

    # ---------- 画布显示剪切结果 ----------
    def _show_cutout(self, show_mask: bool = False, show_board: bool = False):
        """左边显示框选出来的原图，右边白底预览抠图效果；已有标注直接描边。

        show_mask = 绿色半透明铺出「会被抠掉保留的范围」（剪切/补点阶段用）；
        show_board = 右侧白底预览；进入下一步（眼睛/头发）后收起，只留主窗口。
        """
        st = self._cut
        if not st:
            return
        # 标注 / 换图后画布回到静态裁切图：先把小桌子关掉，避免按钮与画布不一致
        self.btn_table.setChecked(False)
        self.canvas.set_table(False)
        x0, y0, x1, y1 = st["box"]
        crop = np.ascontiguousarray(st["rgb"][y0:y1, x0:x1])
        self.canvas.set_image(crop)
        # 绿色虚线 = 剪切范围，框选完之后一直保留，方便对照抠图效果
        self.canvas._draw_cut_frame()
        if show_mask:
            self.canvas._draw_mask_fill(st["mask"][y0:y1, x0:x1])
        for m in st.get("sockets") or []:
            self.canvas._draw_mask_contour(m[y0:y1, x0:x1], "#00e5ff",
                                           dashed=True)
        for w in st.get("hair") or []:
            self.canvas._draw_mask_contour(w["mask"][y0:y1, x0:x1], "#ff9800",
                                           dashed=True)
        for w in st.get("cloth") or []:
            self.canvas._draw_mask_contour(w["mask"][y0:y1, x0:x1], "#4fc3f7",
                                           dashed=True)
        if show_board:
            self.board.set_rgba(self._cut_rgba())
        self.pv_wrap.setVisible(show_board)

    def _show_pet(self, animate: bool = True):
        """抠完之后：画布按桌宠本来大小居中显示，摇摆 + 目光 + 眨眼 + 飘发/衣摆都直接生效。"""
        if not self._cut:
            return
        h = int(self.sp_height.value()) or 380
        rgba = self._cut_rgba()
        live = self._pet_engine(rgba)
        anim = self._anim_params()
        # 下落 / 回弹 / 平台宽度只影响画布重力，重建桌宠时先同步过去（缓存 key 不含它们）
        self.canvas.set_fall(anim["fall"])
        self.canvas.set_bounce(anim["bounce"])
        self.canvas.set_plat(anim["plat"])
        self.canvas.show_pet(rgba, h, animate=animate, live=live, frame_ms=50,
                             sway_deg=anim["sway_deg"],
                             sway_speed=anim["sway_speed"])
        # 画布被重置过（换图 / 重新标注）时，按竖条的勾选状态把小桌子找回来
        if self.btn_table.isChecked() and not self.canvas.table_on:
            self.canvas.set_table(True, anim["fall"])
        # 重绘会丢掉旧覆盖层：按「当前表格里真正选中的行」补高亮，
        # 没选中就什么都不画（别把上次的选区残留在桌宠脸上）
        self._highlight_selection()
        self.board.set_rgba(rgba)
        self.pv_wrap.setVisible(False)

    def _refresh_pet(self):
        """时间轴 / 眨眼 / 目光 / 头发改动后立刻刷新中间的桌宠，让改动当场生效。"""
        if not getattr(self, "_running", False) and getattr(self, "_cut", None):
            self._show_pet(animate=True)

    def _toggle_table(self):
        """竖条「小桌子」：开启重力，桌宠从桌面上方落下并稳稳站住。"""
        on = self.btn_table.isChecked()
        anim = self._anim_params()
        self.canvas.set_fall(anim["fall"])
        self.canvas.set_bounce(anim["bounce"])
        self.canvas.set_plat(anim["plat"])
        if not self.canvas.set_table(on, anim["fall"]):
            self.btn_table.setChecked(False)
            self._log("[小桌子] 先在画布上显示一只桌宠，才能开启重力")
            return
        self._log("[小桌子] 重力开启：桌宠从桌面上方落下，下落 / 回弹系数在动作参数里" if on
                  else "[小桌子] 重力关闭：桌宠回到原位")

    def _apply_anim_params(self, *_):
        """动作参数改了：合并连续拖动，停手后 80ms 再重建一次桌宠。

        拖动条每挪一个像素都会发 valueChanged，逐次重建会把 UI 拖到卡顿、
        画布动画看着像冻住；所以只记一个延时任务，停下来才真正应用。
        """
        if getattr(self, "_running", False) or not getattr(self, "_cut", None):
            return
        t = getattr(self, "_anim_timer", None)
        if t is None:
            t = self._anim_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._rebuild_anim)
        t.start(80)

    def _rebuild_anim(self, force: bool = False):
        """按当前动作参数重建画布上的桌宠，保留缩放与视角。"""
        if getattr(self, "_running", False) or not getattr(self, "_cut", None):
            return
        fw = QApplication.focusWidget()
        dlg = getattr(self, "settings_dlg", None)
        # 编辑数字时焦点落在 spin 内部的 QLineEdit 上，两种都算「正在手输」
        typing = (fw is not None and (isinstance(fw, QAbstractSpinBox)
                                      or isinstance(fw.parentWidget(),
                                                    QAbstractSpinBox)))
        if (not force and typing and dlg is not None and dlg.isVisible()):
            # 正在往数字框里手输：先不重建，等编辑结束（回车 / 失焦）再应用，
            # 否则几十上百毫秒的重建会把输入卡住，数字输不完
            self._anim_pending = True
            return
        self._anim_pending = False
        c = self.canvas
        zoom = c.transform()
        mid = c.mapToScene(c.viewport().rect().center())
        self._show_pet(animate=True)    # 摇摆与否由角度 / 速度自己决定，别沿用上一状态
        c.setTransform(zoom)
        c.centerOn(mid)
        c._zoom_notify()                # 桌子在世界里，_show_pet 已按同一尺寸重画

    def _on_anim_edit_done(self):
        """数字框编辑结束（回车或点到别处）：把刚才压下的重建补上。"""
        if getattr(self, "_anim_pending", False):
            self._anim_pending = False
            self._rebuild_anim(force=True)

    def _anim_params(self) -> dict:
        """设置里的「动作参数」：头发速度/摆幅/回弹、身体摇摆。"""
        def val(name, default):
            w = getattr(self, name, None)
            try:
                return float(w.value())
            except (AttributeError, TypeError, ValueError):
                return default
        return {
            "hair_speed": val("sp_hair_speed", 1.0),
            "hair_amp": val("sp_hair_amp", 1.0),
            "hair_stiff": val("sp_hair_stiff", 1.0),
            "hair_damp": val("sp_hair_damp", 1.0),
            "sway_deg": val("sp_sway_deg", 2.4),
            "sway_speed": val("sp_sway_speed", 1.0),
            "bounce": val("sp_bounce", 0.45),
            "fall": val("sp_fall", 1.0),
            "plat": val("sp_plat", 60.0) / 100.0,   # 界面 10~2000%，存成 0.1~20.0
        }

    def _pet_engine(self, rgba):
        """按当前眼眶、发缕与时间轴建一个可逐帧渲染的桌宠（结果缓存）。"""
        st = self._cut
        sockets = st.get("sockets") or []
        hair = st.get("hair") or []
        cloth = st.get("cloth") or []
        anim = self._anim_params()
        levels = list(st.get("levels") or [])
        if len(levels) < len(sockets):
            levels += ["auto"] * (len(sockets) - len(levels))
        if self.ck_blink.isChecked():
            blinks, cycle = blinks_from_plan(self._blink_plan())
        else:
            blinks, cycle = [], 8.0
        key = (st.get("stem"), st.get("path"), rgba.shape,
               tuple(int(np.count_nonzero(m)) for m in sockets), tuple(levels),
               tuple((it["mask"].shape, int(np.count_nonzero(it["mask"])))
                     for it in hair),
               tuple((it["mask"].shape, int(np.count_nonzero(it["mask"])))
                     for it in cloth),
               tuple((round(b.t, 2), round(b.hold, 2), b.group_no,
                      bool(b.half)) for b in blinks),
               round(float(cycle), 2), self.ck_gaze.isChecked(),
               # 下落 / 回弹 / 平台宽度只作用于画布重力，不影响逐帧渲染，别因为它重建引擎
               tuple(round(v, 2) for k, v in anim.items()
                     if k not in ("fall", "bounce", "plat")))
        if getattr(self, "_pet_key", None) == key:
            return self._pet_engine_cache
        x0, y0, x1, y1 = st["box"]
        su = [m[y0:y1, x0:x1] for m in sockets] or None
        wisps = []
        for it in hair:
            m = it["mask"][y0:y1, x0:x1]
            if not m.any():
                continue
            wisps.append(HairWisp(m.astype(bool),
                                  (it["pivot"][0] - x0, it["pivot"][1] - y0),
                                  (it["tip"][0] - x0, it["tip"][1] - y0)))
        cloth_wisps = []
        for it in cloth:
            m = it["mask"][y0:y1, x0:x1]
            if not m.any():
                continue
            cloth_wisps.append(HairWisp(
                m.astype(bool),
                (it["pivot"][0] - x0, it["pivot"][1] - y0),
                (it["tip"][0] - x0, it["tip"][1] - y0)))
        eng = PetPreview(rgba, self._eyes_crop(sockets), su, levels, wisps,
                         blinks, cycle, gaze_on=self.ck_gaze.isChecked(),
                         hair_speed=anim["hair_speed"], hair_amp=anim["hair_amp"],
                         hair_stiff=anim["hair_stiff"],
                         hair_damp=anim["hair_damp"],
                         cloth_wisps=cloth_wisps)
        self._pet_key = key
        self._pet_engine_cache = eng
        return eng

    def _save_source(self):
        """剪切结果落盘：白底裁切图 + 掩膜，素材生成和选做步骤都读这两个文件。"""
        st = self._cut
        x0, y0, x1, y1 = st["box"]
        crop = st["rgb"][y0:y1, x0:x1]
        m = st["mask"][y0:y1, x0:x1]
        white = np.full_like(crop, 255)
        white[m > 0] = crop[m > 0]
        st["src_png"] = st["source_dir"] / f"{st['stem']}.png"
        st["src_mask"] = st["source_dir"] / f"{st['stem']}_mask.png"
        Image.fromarray(white).save(st["src_png"])
        Image.fromarray((m.astype(np.uint8) * 255)).save(st["src_mask"])
        print(f"    ✓ {st['src_png'].name}  {st['src_mask'].name}")

    # ---------- 任务调度 ----------
    def _run_task(self, fn):
        if self._running:
            return
        self._running = True
        self._set_busy(True)
        # 任务在 UI 线程里同步跑，先让长条画出来，不然忙完才看得到
        self.progress.repaint()
        QApplication.processEvents()
        try:
            with contextlib.redirect_stdout(LogWriter(self.log)):
                fn()
        except Cancelled:
            self._log("[取消] 已停止")
            self.step_label.setText("已取消")
        except Exception as exc:  # 界面里出错不要闪退
            self._log(f"[错误] {exc}")
            self._log(traceback.format_exc())
        finally:
            self._running = False
            self._set_busy(False)
            self.canvas.set_idle()
            self.act_bar.setVisible(False)
            self.pv_bar.setVisible(False)
            # 任务结束回到「桌宠在中间摇摆」的状态（没抠完就什么都不做）
            self._show_pet(animate=True)

    def _set_busy(self, busy: bool):
        """跑任务时禁用按钮；抠图完成后才放开选做步骤与生成。"""
        cut = self._cut is not None
        self.btn_start.setEnabled(not busy)
        for b in (self.btn_eyes, self.btn_hair, self.btn_cloth):
            b.setEnabled(not busy and cut)
        self.btn_table.setEnabled(not busy and cut)
        self.btn_build.setEnabled(not busy and cut)
        self._set_progress(busy)

    def _set_progress(self, busy: bool):
        """任务长条：干活时来回跑（不确定进度），结束时填满再收起。"""
        p = getattr(self, "progress", None)
        if p is None:
            return
        if busy:
            self._progress_running = True
            p.setRange(0, 0)          # 0/0 = 不确定进度，Qt 自动来回走
            p.setVisible(True)
        elif getattr(self, "_progress_running", False):
            self._progress_running = False
            p.setRange(0, 100)
            p.setValue(100)
            QTimer.singleShot(
                800, lambda: None if self._progress_running else p.setVisible(False))

    def _start(self):
        """第一步（必做）：框取手办并抠图，结果直接显示在画布上。"""
        if not self._image_path:
            self._log("[提示] 请先选择图片")
            return
        self.log.clear()
        self._run_task(self._cut_flow)

    def _require(self, value):
        if value is None:
            raise Cancelled()
        return value

    def _cut_flow(self):
        """必做步骤：读图 → 框取 →（可跳过补点）→ 抠图，完成后立刻显示剪切结果。"""
        path = self._image_path
        sku = launch.safe_stem(self.ed_sku.text().strip() or "custom")
        stem = launch.safe_stem(self.ed_name.text().strip()
                                or (Path(path).stem + "_pet"))
        source_dir = DEFAULT_CANON / sku
        out_dir = DEFAULT_OUT / sku
        source_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir = out_dir

        self.btn_build.setToolTip("保存素材并启动桌宠（PNG / WebP / manifest）")
        self._step("第 1 步 · 读图")
        print(f"[1/3] 读图 {Path(path).name}")
        im = Image.open(path)
        rgba = np.array(self._rotated_rgba(im))
        rgb = rgba[..., :3].copy()
        has_alpha = int(rgba[..., 3].min()) < 250
        self.canvas.set_image(rgb)
        print(f"    尺寸 {rgb.shape[1]}×{rgb.shape[0]}"
              + ("  含 alpha，直接用透明通道" if has_alpha else ""))

        # ---- 框取 → 确认框大小 → 只保留框内画面 ----
        while True:
            # 回退重框时也要回到整图，不能在裁切图上再画框
            self.canvas.set_image(rgb)
            self._step("第 1 步 · 框取手办",
                       "拖拽或左键点两个对角圈住手办；回车确认"
                       "（r 重框 / u 撤销 / f 整图 / ESC 取消）")
            print("[2/3] 框取手办")
            box = self._require(self._run_phase(
                "box", "拖拽或点两个对角圈住手办，回车确认"))
            h, w = rgb.shape[:2]
            x0, y0, x1, y1 = [int(v) for v in box]
            x0, y0 = max(0, min(x0, w - 2)), max(0, min(y0, h - 2))
            x1, y1 = min(w, max(x1, x0 + 2)), min(h, max(y1, y0 + 2))
            if x1 - x0 < 8 or y1 - y0 < 8:
                self._log("[提示] 框太小了，请重新框选")
                continue
            box = (x0, y0, x1, y1)
            print(f"    框 [{x0},{y0},{x1},{y1}]  {x1 - x0}×{y1 - y0}")

            crop_rgb = np.ascontiguousarray(rgb[y0:y1, x0:x1])
            crop_rgba = np.ascontiguousarray(rgba[y0:y1, x0:x1])
            self.canvas.set_image(crop_rgb)
            act = self._run_preview(
                [QPixmap.fromImage(qimage(crop_rgb))], "确认标框大小",
                back="回退：重新框选 (u)", ok="保存 (s)")
            if act == "ok":
                break
            if act is None:
                raise Cancelled()
            # back → 重新框取

        # 之后所有步骤都只看框内画面（补点/眼眶/发缕坐标也都在这个裁切里）
        rgb, rgba = crop_rgb, crop_rgba
        box = (0, 0, crop_rgb.shape[1], crop_rgb.shape[0])
        self.canvas.set_image(rgb)

        # ---- 选点 → 抠图 → 剪切效果预览；预览里 u = 补点精修、s = 保存 ----
        while True:
            pts_list: list[list[int]] = []
            cache: dict = {}
            first = True
            restart = False
            while True:
                # 回到裁切后的原图，避免把上一轮的剪切/棋盘格当成底图
                self.canvas.set_image(rgb)
                # 回退到补点时收起白底预览，剪切预览阶段再显示
                self.pv_wrap.setVisible(False)
                reuse = False
                if has_alpha:
                    print("    已有 alpha，跳过选点")
                else:
                    self._step("第 2 步 · 补点（可直接回车跳过）" if first
                               else "补点精修 · 补完回车重新分割",
                               "只算绿框内的点：左键 = 前景(红)   右键单击 = 背景(蓝)   "
                               "回车 = 重新分割（r / u / s / ESC）")
                    res = self._run_phase(
                        "points",
                        "绿框内：左键=前景(红)，右键单击=背景(蓝)（右键拖动=平移），回车=重新分割；"
                        "直接回车 = 不补点（s = 保留当前掩膜）",
                        box=box, points=pts_list, allow_skip=True)
                    if res is None:
                        raise Cancelled()
                    if res is SKIP:
                        reuse = not first
                        if not reuse:
                            pts_list = []
                            print("    未补点，直接按框分割")
                        else:
                            print("    未补点，保留当前掩膜")
                    else:
                        pts_list = [list(p) for p in res]
                        print(f"    前景 {sum(p[2] == 1 for p in pts_list)} 个，"
                              f"背景 {sum(p[2] == 0 for p in pts_list)} 个")

                pts, labels = (point_arrays(pts_list) if pts_list else (None, None))
                if reuse:
                    mask = self._cut["mask"]
                else:
                    self._step("计算掩膜…", "正在估计前景掩膜，请稍候")
                    mask = launch.estimate_mask(
                        rgb, rgba, has_alpha, box, pts, labels,
                        self.cb_engine.currentText(), self.cb_device.currentText(),
                        cache)
                    print(f"    掩膜覆盖率 {mask.mean() * 100:.1f}%")

                self._cut = {"path": path, "sku": sku, "stem": stem,
                             "source_dir": source_dir, "out_dir": out_dir,
                             "rgb": rgb, "rgba": rgba, "has_alpha": has_alpha,
                             "box": box, "mask": mask, "pts": pts_list,
                             "cache": cache, "sockets": [], "levels": [],
                             "hair": [], "cloth": []}
                self._hl_mask = None   # 新图没有选区，别把上一张的绿光带过来
                self._sync_hair_table()
                self._sync_cloth_table()
                self._save_source()
                self._show_cutout(show_mask=True, show_board=True)
                act = self._run_preview(
                    self._frames_cut(), "剪切结果预览",
                    back=("回退：重新框选 (u)" if has_alpha
                          else "回退：补点精修 (u)"),
                    ok="保存 (s)", sharpen=True)
                if act == "ok":
                    # 按当前锐化档位重写源图，后续生成素材沿用它
                    self._save_source()
                    break
                if act is None:
                    raise Cancelled()
                if has_alpha:
                    restart = True
                    break
                first = False
            if not restart:
                break

        self._step("已抠图 · 右侧可选做",
                   "眼睛处理 / 标注发缕 / 调眨眼时间轴，每步做完都会先播效果预览；"
                   "最后点「生成素材并启动桌宠」")
        print("[完成] 抠图完成，右侧选做步骤均为可选；"
              "不标注则用自动检测或已有标注")

    # ---------- 效果预览 ----------
    def _run_preview(self, frames, hint, back="回退 (u)", ok="保存 (s)",
                     sharpen: bool = False):
        """在画布上循环播一组帧：u = 回退、s = 完成、ESC = 取消。

        frames 可以是帧列表，也可以是 (帧列表, 每帧毫秒) 二元组。
        """
        interval = 80
        live = None
        if isinstance(frames, tuple):
            if len(frames) >= 3:
                frames, interval, live = frames[0], frames[1], frames[2]
            else:
                frames, interval = frames
        loop = QEventLoop()
        res = {"v": None}

        def on_done(value):
            res["v"] = value
            if loop.isRunning():
                loop.quit()

        self.pv_label.setText(hint)
        self.btn_pv_back.setText(back or "回退 (u)")
        self.btn_pv_back.setVisible(back is not None)
        self.btn_pv_ok.setText(ok)
        # 边界锐化档位只在「剪切结果预览」里出现，其他步骤不干扰
        for wdt in (self.btn_sharpen_less, self.lbl_sharpen, self.btn_sharpen_more):
            wdt.setVisible(sharpen)
        self.lbl_sharpen.setText(str(self._sharpen))
        self.pv_bar.setVisible(True)
        self.act_bar.setVisible(False)
        self.hint_label.setText(
            f"{hint}：s 保存" + ("，u 回退" if back else "") + "，ESC 取消")
        self.canvas.start_preview(frames, on_done, interval, live=live)
        loop.exec()
        self.pv_bar.setVisible(False)
        self.canvas.stop_preview()
        self.canvas.set_idle()
        return res["v"]

    def _cut_crop(self):
        x0, y0, x1, y1 = self._cut["box"]
        return (self._cut["rgb"][y0:y1, x0:x1].copy(),
                self._cut["mask"][y0:y1, x0:x1].copy())

    def _cut_rgba(self):
        """预览用回退路径抠图：pymatting 一次要跑一两秒，播放前等不起。

        结果缓存在 _cut 里（按边界锐化档位缓存），桌宠预览与白底预览共用同一份。
        """
        st = self._cut
        lv = int(getattr(self, "_sharpen", 0))
        cached = st.get("_rgba")
        if cached and cached[0] == lv:
            return cached[1].copy()
        rgb, m = self._cut_crop()
        a, fg = fallback_alpha(rgb, m, 4)
        if lv > 0:
            fg, a = sharpen_cut_edges(fg, a, lv)
        out = np.dstack([np.clip(fg, 0, 255).astype(np.uint8),
                         (np.clip(a, 0, 1) * 255).astype(np.uint8)])
        st["_rgba"] = (lv, out)
        return out.copy()

    def _change_sharpen(self, delta: int):
        """边界锐化 −/+：0~3 档，只作用于切割轮廓周围，右边白底预览实时跟着变。"""
        self._sharpen = max(0, min(3, int(self._sharpen) + delta))
        self.lbl_sharpen.setText(str(self._sharpen))
        if not self._cut:
            return
        self._cut.pop("_rgba", None)
        if self.pv_wrap.isVisible():
            self.board.set_rgba(self._cut_rgba())

    def _frames_cut(self):
        """剪切结果预览的「左边」：框选出来的原图（右边白底由 PreviewBoard 显示）。"""
        rgb, _m = self._cut_crop()
        return [QPixmap.fromImage(qimage(rgb))]

    def _eyes_crop(self, sockets):
        """眼眶 mask（全图坐标）-> 裁切坐标下的 Eye 列表。"""
        x0, y0, x1, y1 = self._cut["box"]
        eyes = []
        for m in sockets:
            cm = m[y0:y1, x0:x1]
            ys, xs = np.nonzero(cm)
            if not len(xs):
                continue
            eyes.append(Eye(cx=float(xs.mean()), cy=float(ys.mean()),
                            rx=max(3.0, float((xs.max() - xs.min()) / 2.0)),
                            ry=max(3.0, float((ys.max() - ys.min()) / 2.0))))
        return eyes

    def _frames_wisps(self, items=None):
        """发缕 / 衣物的飘动预览：按被动跟随的微风相位铺一整个摆动周期。"""
        items = self._cut["hair"] if items is None else items
        rgba = self._cut_rgba()
        x0, y0, x1, y1 = self._cut["box"]
        wisps = []
        for it in items:
            m = it["mask"][y0:y1, x0:x1]
            if not m.any():
                continue
            wisps.append(HairWisp(
                m.astype(bool),
                (it["pivot"][0] - x0, it["pivot"][1] - y0),
                (it["tip"][0] - x0, it["tip"][1] - y0)))
        if not wisps:
            return self._frames_cut()
        h, w = rgba.shape[:2]
        field = HairField(wisps, rgba, w, h)
        frames = []
        for _ in range(30):
            field.step(0.09, 0.0, 0.0)
            frames.append(QPixmap.fromImage(
                qimage(checkerboard(field.swing(rgba)))))
        return frames

    # ---------- 选做步骤 ----------
    def _annotate_eyes(self):
        if self._cut:
            self._run_task(self._eyes_flow)

    def _annotate_hair(self):
        if self._cut:
            self._run_task(self._hair_flow)

    def _annotate_cloth(self):
        if self._cut:
            self._run_task(self._cloth_flow)

    def _build(self):
        if self._cut:
            self._run_task(self._build_flow)

    def _eyes_flow(self):
        st = self._cut
        self.canvas.set_image(st["rgb"])
        self._on_eye_level_changed()
        self._step("眼睛处理 · 眼眶标注",
                   "左键沿眼眶边界逐个点顶点，回车或点「闭合眼眶」闭合当前眼眶；"
                   "右键/中键按住可拖动图片（放大后用来平移）；"
                   "闭合后可继续点下一只，没有未闭合多边形时回车完成；"
                   "u = 撤销顶点，r = 重来，s = 保持已有标注，ESC = 取消")
        res = self._run_phase(
            "eyes",
            "左键沿眼眶边界点一圈（至少 3 个顶点），回车或「闭合眼眶」闭合；"
            "右键拖动图片，放大后可以平移；"
            "闭合后继续点下一只，再按回车完成（u 撤销 / r 重来 / s 保持已有）。"
            "档位在下方「眨眼时间轴」逐只调（L1-1、L1-2 …）",
            allow_skip=True, full_key=False, color="cyan",
            masks=st["sockets"], levels=st["levels"])
        if res is None:
            self._sync_blink_level_texts()   # 标框中途取消，时间轴回到已保存的行
            raise Cancelled()
        if res is SKIP:
            print("    未画眼眶，保留已有标注（自动检测仍生效）")
            self._show_cutout()
            self._sync_blink_level_texts()
            return
        sockets = [it["mask"].astype(bool) for it in res]
        levels = [str(it.get("level") or "auto") for it in res]
        # 标完直接落盘：返回后桌宠立刻用上新眼眶（眨眼在桌宠身上直接播）
        self._save_eyes(sockets, levels)
        self._show_cutout()

    def _save_eyes(self, sockets, levels):
        st = self._cut
        x0, y0, x1, y1 = st["box"]
        # 空多边形不落盘；档位与 mask 必须一起过滤，保持逐只对齐
        keep = [i for i, m in enumerate(sockets) if m.any()]
        sockets = [sockets[i] for i in keep]
        levels = [levels[i] if i < len(levels) else "auto" for i in keep]
        st["sockets"], st["levels"] = sockets, levels
        crop_sockets = [m[y0:y1, x0:x1] for m in sockets]
        hl = (eye_highlight_mask(st["rgb"][y0:y1, x0:x1], crop_sockets)
              if crop_sockets else None)
        mask_files = (save_eye_masks(st["source_dir"], st["stem"],
                                     crop_sockets, hl) if crop_sockets else [])
        mask_boxes = boxes_from_eye_masks(sockets)
        eyes_path = launch.save_eyes_json(
            st["source_dir"], st["stem"], (x1 - x0, y1 - y0),
            [(b["x1"], b["y1"], b["x2"], b["y2"]) for b in mask_boxes],
            (x0, y0), force=True, levels=levels)
        for old in st["source_dir"].glob(f"{st['stem']}_eyes*.png"):
            if old.name not in mask_files:
                old.unlink()
        print(f"    ✓ 眼眶 mask {', '.join(mask_files) or '（无）'}；"
              f"{eyes_path.name}（{len(mask_boxes)} 只眼，未标注的不眨眼）")
        self._sync_blink_level_texts()

    def _on_eye_rows_changed(self, levels):
        """画布里边框完一只眼就实时同步时间轴（保存前也能看到逐眼行）。"""
        if getattr(self, "tb_blink", None) is None:
            return
        self._sync_blink_level_texts(levels)

    def _sync_blink_level_texts(self, levels=None):
        """把时间轴重建成「一只眼一行」，并填上真实档位。

        levels 为 None 时用已保存的眼眶档位；标框过程中传当前画布里的档位，
        这样闭合一只眼下面就立刻多一行（一开始没有标注时一行都没有）。
        """
        if levels is None:
            if not self._cut:
                return
            levels = list(self._cut.get("levels") or [])
        levels = [str(v) for v in levels]

        if not levels:
            self.tb_blink.blockSignals(True)
            self.tb_blink.setRowCount(0)
            self.tb_blink.blockSignals(False)
            return

        old: dict[int, tuple[float, float]] = {}
        for r in range(self.tb_blink.rowCount()):
            it0 = self.tb_blink.item(r, 0)
            it2 = self.tb_blink.item(r, 2)
            it3 = self.tb_blink.item(r, 3)
            if it0 is None or it2 is None or it3 is None:
                continue
            old[int(it0.data(Qt.ItemDataRole.UserRole))] = (
                float(it2.data(Qt.ItemDataRole.UserRole)),
                float(it3.data(Qt.ItemDataRole.UserRole)))

        self.tb_blink.blockSignals(True)
        self.tb_blink.setRowCount(0)
        self.tb_blink.blockSignals(False)
        cycle = max(1.0, float(self.sp_blink_cycle.value()))
        for eye, lv in enumerate(levels, 1):
            if eye in old:
                t, hold = old[eye]
            else:
                # 新眼睛：默认 2.0s 起步，多只依次错开一点，避免所有眼同时眨
                t = min(max(0.2, cycle - 0.5), 2.0 + 0.8 * (eye - 1))
                hold = 0.2
            self._add_blink_row(round(t, 2), hold, eye=eye, level=lv)

    def _hair_flow(self):
        st = self._cut
        box = st["box"]
        while True:
            self.canvas.set_image(st["rgb"])
            self._step("标注发缕",
                       "左键沿一缕头发的轮廓点一圈（第一个顶点=发根），回车或点「闭合发缕」闭合；"
                       "右键/中键按住可拖动图片（放大后用来平移）；"
                       "可继续画多缕，没有未闭合多边形时回车完成；"
                       "u = 撤销顶点，r = 重来，s = 保持已有标注，ESC = 取消")
            res = self._run_phase(
                "hair",
                "左键沿发缕轮廓点一圈（第一个顶点=发根），回车或「闭合发缕」闭合；"
                "右键拖动图片，放大后可以平移；"
                "可继续画下一缕，再按回车完成（u 撤销 / r 重来 / s 保持已有）。"
                "只选外轮廓上的发丝（未压住脸/身体），摆动才不留空洞",
                allow_skip=True, full_key=False, color="orange",
                hair=st["hair"])
            if res is None:
                raise Cancelled()
            if res is SKIP:
                print("    未画发缕，保留已有标注")
                self._sync_hair_table()
                self._show_cutout()
                return
            wisps = list(res)
            st["hair"] = wisps

            self._show_cutout()
            act = self._run_preview(
                self._frames_wisps(wisps),
                f"头发飘动效果预览（{len(wisps)} 缕）",
                back="回退：重画发缕 (u)", ok="保存 (s)")
            if act == "ok":
                x0, y0, x1, y1 = box
                hair_path = launch.save_hair_polys(
                    st["source_dir"], st["stem"], (x1 - x0, y1 - y0),
                    wisps, (x0, y0), force=True)
                print(f"    ✓ 发缕标注 {hair_path.name}（{len(wisps)} 缕，"
                      f"首点=发根，外轮廓发丝按 ±9° 摆）")
                self._sync_hair_table()
                self._show_cutout()
                return
            if act is None:
                raise Cancelled()

    def _cloth_flow(self):
        """衣服 / 小物件的飘动标注：流程、交互与头发完全一致，只是换一套落盘文件。"""
        st = self._cut
        box = st["box"]
        while True:
            self.canvas.set_image(st["rgb"])
            self._step("标注衣服 / 小物件",
                       "左键沿衣摆 / 袖子 / 挂饰等会飘的轮廓点一圈（第一个顶点=固定端），"
                       "回车或点「闭合衣物」闭合；右键/中键按住可拖动图片；"
                       "可继续画多块，没有未闭合多边形时回车完成；"
                       "u = 撤销顶点，r = 重来，s = 保持已有标注，ESC = 取消")
            res = self._run_phase(
                "cloth",
                "左键沿衣摆 / 小物件轮廓点一圈（第一个顶点=固定端），回车或「闭合衣物」闭合；"
                "右键拖动图片，放大后可以平移；"
                "可继续画下一块，再按回车完成（u 撤销 / r 重来 / s 保持已有）。"
                "只选会飘的衣物边缘，别把整件衣服圈进去",
                allow_skip=True, full_key=False, color="blue",
                hair=st["cloth"])
            if res is None:
                raise Cancelled()
            if res is SKIP:
                print("    未画衣物，保留已有标注")
                self._sync_cloth_table()
                self._show_cutout()
                return
            wisps = list(res)
            st["cloth"] = wisps

            self._show_cutout()
            act = self._run_preview(
                self._frames_wisps(wisps),
                f"衣服飘动效果预览（{len(wisps)} 块）",
                back="回退：重画衣物 (u)", ok="保存 (s)")
            if act == "ok":
                x0, y0, x1, y1 = box
                cloth_path = launch.save_hair_polys(
                    st["source_dir"], st["stem"], (x1 - x0, y1 - y0),
                    wisps, (x0, y0), force=True, kind="cloth")
                print(f"    ✓ 衣物标注 {cloth_path.name}（{len(wisps)} 块，"
                      "与头发共用飘动物理）")
                self._sync_cloth_table()
                self._show_cutout()
                return
            if act is None:
                raise Cancelled()

    def _apply_plan_levels(self, plan: dict):
        """把时间轴每行的「当前档位 / 眼睛数量」映射回逐只眼。

        auto 不覆盖已有标注（运行时按眼睛层级自动判）；数量决定这一组从第几只
        眼开始、占几只，组号按顺序连续分配。
        """
        st = self._cut
        n = len(st.get("sockets") or [])
        if n == 0:
            return
        groups: dict[int, dict] = {}
        for it in plan.get("items") or []:
            groups.setdefault(int(it.get("group", 1)), it)
        levels = (list(st.get("levels") or []) + ["auto"] * n)[:n]
        start = 0
        for g in sorted(groups):
            it = groups[g]
            cnt = max(1, int(it.get("count", 2)))
            first, last = start + 1, start + cnt
            toks = it.get("levels") or []
            if not toks:
                # 兼容只有单一 level 的旧格式：整组套用
                lv = str(it.get("level") or "auto")
                if lv != "auto":
                    for k in range(first - 1, min(n, last)):
                        levels[k] = lv
            for tok in toks:
                try:
                    eye = int(tok.get("eye", 0))
                    lv = str(tok.get("level") or "auto")
                except (TypeError, ValueError):
                    continue
                # 眼号落在这一组的范围内才生效（如 L1-1、L1-2、L1-3）
                if lv == "auto" or eye < first or eye > last or eye > n:
                    continue
                levels[eye - 1] = lv
            start += cnt
        if levels == list(st.get("levels") or []):
            return
        self._save_eyes(st["sockets"], levels)

    def _blink_num_groups(self, n_eyes: int) -> int:
        """按时间轴的眼睛数量算实际分几组（生成时校验眼组号用）。"""
        if n_eyes <= 0:
            return 0
        blinks, _ = blinks_from_plan(self._blink_plan())
        counts = blink_group_counts(blinks)
        if not counts:
            return (n_eyes + 1) // 2
        total, g = 0, 0
        for c in counts:
            if total >= n_eyes:
                break
            total += max(1, int(c))
            g += 1
        return max(1, g)

    def _build_flow(self):
        """最后一步：把当前剪切结果 + 已有标注 + 眨眼时间轴打包成桌宠素材。"""
        st = self._cut
        plan = self._blink_plan()
        self._apply_plan_levels(plan)
        blink_path = launch.save_blink_json(st["source_dir"], st["stem"], plan)
        print(f"[生成] {st['source_dir'].name}/{st['stem']} → {st['out_dir'].name}")
        print(f"    ✓ 眨眼时间轴 {blink_path.name}（{len(plan['items'])} 个对象，"
              f"周期 {plan['cycle']:.1f}s，"
              f"{'启用' if plan['enabled'] else '未启用·随机眨眼'}）")
        over = [it for it in plan["items"] if it["t"] >= plan["cycle"]]
        if over:
            print(f"    [warn] {len(over)} 个眨眼对象时刻超出循环周期，桌宠会忽略")
        if not (st["source_dir"] / f"{st['stem']}_eyes.json").exists():
            print("    · 未标注眼眶，眼睛位置用自动检测")
        if not (st["source_dir"] / f"{st['stem']}_hair.json").exists():
            print("    · 未标注发缕，不做头发飘动")
        if not (st["source_dir"] / f"{st['stem']}_cloth.json").exists():
            print("    · 未标注衣物，不做衣摆 / 小物件飘动")

        self._step("生成素材…", "裁切、去白边、缩放并写 manifest")
        item = build_one(st["src_png"], st["src_mask"],
                         st["source_dir"] / f"{st['stem']}.json",
                         st["out_dir"], self.sp_size.value(), edge_px=4,
                         margin=8, use_matting=True, preview=True,
                         edge=int(getattr(self, "_sharpen", 0)))
        item["blink"] = plan
        item["anim"] = self._anim_params()   # 动作参数跟着素材走，桌宠本体一致
        # 覆盖保存时保住用户改过的显示名：manifest 是按 stem 整条替换的
        try:
            old_items = json.loads(
                (st["out_dir"] / "manifest.json").read_text(encoding="utf-8")
            ).get("items") or []
        except (OSError, json.JSONDecodeError):
            old_items = []
        old = next((it for it in old_items if it.get("stem") == st["stem"]), None)
        if old and old.get("label"):
            item["label"] = old["label"]
        n_groups = self._blink_num_groups(len(item.get("eyes") or []))
        bad_groups = [it for it in plan["items"] if it["group"] > n_groups]
        if plan["enabled"] and bad_groups:
            print(f"    [warn] {len(bad_groups)} 个眨眼对象的眼组超出范围"
                  f"（当前只有 {n_groups} 组眼，桌宠会忽略）")
        manifest = launch.update_manifest(st["out_dir"], st["sku"], item)
        total = len(json.loads(manifest.read_text(encoding="utf-8"))["items"])
        print(f"    ✓ {st['out_dir'] / (st['stem'] + '.png')}")
        print(f"    ✓ {manifest}（共 {total} 个素材）")
        print(f"    掩膜覆盖率 {item['coverage'] * 100:.1f}%  "
              f"手工眼睛 {len(item.get('eyes') or [])} 个（{n_groups} 组）  "
              f"发缕 {len(item.get('hair') or [])} 缕  "
              f"衣物 {len(item.get('cloth') or [])} 块  "
              f"眨眼对象 {len(plan['items']) if plan['enabled'] else 0} 个")
        self._refresh_space()                 # 新生成的桌宠立刻出现在桌宠空间
        self._step("完成", f"素材已生成：{st['out_dir']}")
        self._log(f"[完成] 素材目录：{st['out_dir']}")

        if self.ck_launch.isChecked():
            print("[启动] 桌宠窗口（立刻在桌面显示最新效果）")
            # 同一只宠物重新生成会自动替换旧桌宠，不叠窗口
            win = self._launch_pet(st["out_dir"], item, self.sp_height.value() or 0)
            print(f"    眼睛 {len(win.asset.eyes)} 个，眨眼帧 {len(win.blink_pixmaps)} 张，"
                  f"眨眼时间轴 {'开' if win.asset.blinks else '关（随机）'}")
            if len(self._pets) > 1:
                print(f"    多只桌宠：{len(self._pets)} 只同屏，已启用相互碰撞")


def run_ui():
    prefer_xcb()   # 必须在 QApplication 之前决定平台插件
    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = LaunchUI()
    win.show()
    app.exec()


def main():
    run_ui()


if __name__ == "__main__":
    main()
