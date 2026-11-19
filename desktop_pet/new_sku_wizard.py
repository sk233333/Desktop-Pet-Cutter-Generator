#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新建 SKU 向导：手动切割 / AI 生成（默认手动）。

「AI 一键」= 一次请求出全部候选，不等于一次点击自动写盘：

选图 → 显式勾选上传授权 → 生成候选 → 并排预览叠真实图 → 逐项勾选 /
整体否决 → ChangeSet → ``upgrade_core.commit_changes()`` 同一闸门落盘。

失败（未启用 / 缺 key / 断网 / 超时 / 429 / 5xx / 乱码 / 候选无效 /
写盘失败）一律整体回手动，不留半份 SKU。

单独运行（离屏冒烟）：
    QT_QPA_PLATFORM=offscreen python3 desktop_pet/new_sku_wizard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QRadioButton, QVBoxLayout)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_generate_adapter import (build_changeset, commit_selected,  # noqa: E402
                                 load_source_rgb, new_sku_info,
                                 reject_existing_base)
from ai_generate_contract import (AIGenError, AIGenRequest,  # noqa: E402
                                  load_config, propose_candidates)
from ai_generate_provider import HttpAIProvider                     # noqa: E402
from completers.base import composite                               # noqa: E402


def _pixmap(rgb: np.ndarray) -> QPixmap:
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class NewSkuWizard(QDialog):
    """模式选择 + AI 候选预览；手动切割沿用主窗口现有流程。"""

    def __init__(self, parent=None, assets_root=None, provider=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("新建 SKU")
        self.resize(900, 680)
        self._assets_root = assets_root
        self._provider = provider or HttpAIProvider()
        self._config = config or load_config()
        self._image_path: Path | None = None
        self._rgb: np.ndarray | None = None
        self._candidates: list = []
        self._build_ui()
        self._sync_mode()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("新建 SKU · 第一步：选择生成方式")
        title.setStyleSheet("font-size:18px;font-weight:800;color:#3e4a5c;")
        root.addWidget(title)

        box_mode = QGroupBox("生成方式")
        mv = QHBoxLayout(box_mode)
        self.rb_manual = QRadioButton("手动切割（默认）")
        self.rb_ai = QRadioButton("AI 生成候选")
        self.rb_manual.setChecked(True)
        self.rb_manual.toggled.connect(self._sync_mode)
        mv.addWidget(self.rb_manual)
        mv.addWidget(self.rb_ai)
        mv.addStretch(1)
        root.addWidget(box_mode)

        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("SKU 名"))
        self.ed_sku = QLineEdit()
        self.ed_sku.setPlaceholderText("assets/ 下的目录名，例如 pet")
        row_name.addWidget(self.ed_sku, 1)
        root.addLayout(row_name)

        self.lb_mode = QLabel()
        self.lb_mode.setWordWrap(True)
        root.addWidget(self.lb_mode)

        box_src = QGroupBox("原图与授权")
        sv = QVBoxLayout(box_src)
        row_src = QHBoxLayout()
        self.btn_pick = QPushButton("选择原图…")
        self.btn_pick.clicked.connect(self._pick_image)
        self.lb_src = QLabel("未选择")
        row_src.addWidget(self.btn_pick)
        row_src.addWidget(self.lb_src, 1)
        sv.addLayout(row_src)
        self.ck_consent = QCheckBox("允许上传原图（仅在本次请求中发送图片字节，"
                                    "不发路径 / key / 日志）")
        sv.addWidget(self.ck_consent)
        row_gen = QHBoxLayout()
        self.btn_generate = QPushButton("生成候选")
        self.btn_generate.clicked.connect(self._generate)
        self.btn_reject = QPushButton("整体否决")
        self.btn_reject.clicked.connect(self._reject_all)
        row_gen.addWidget(self.btn_generate)
        row_gen.addWidget(self.btn_reject)
        row_gen.addStretch(1)
        sv.addLayout(row_gen)
        root.addWidget(box_src)

        body = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.itemChanged.connect(self._refresh_preview)
        body.addWidget(self.lst, 1)
        self.lb_preview = QLabel("候选预览（叠在真实图上）")
        self.lb_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_preview.setMinimumSize(360, 360)
        self.lb_preview.setStyleSheet("background:#eef1f5;border:1px solid #d5dbe3;")
        body.addWidget(self.lb_preview, 1)
        root.addLayout(body, 1)

        nav = QHBoxLayout()
        self.btn_commit = QPushButton("写入 SKU（候选默认不选）")
        self.btn_commit.clicked.connect(self._commit)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.reject)
        nav.addStretch(1)
        nav.addWidget(self.btn_commit)
        nav.addWidget(self.btn_close)
        root.addLayout(nav)

    def _sync_mode(self):
        ai = self.rb_ai.isChecked()
        for w in (self.btn_pick, self.btn_generate, self.btn_reject, self.ck_consent):
            w.setEnabled(ai)
        self.btn_commit.setEnabled(ai)
        self.lb_mode.setText(
            "AI 生成：一次请求出全部候选，逐项人工确认后才落盘；失败整体回手动。"
            if ai else
            "手动切割：沿用主窗口现有切割流程；本向导不复制切割逻辑。")

    # ---------------- 选择原图 ----------------
    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择原图", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        self._image_path = Path(path)
        self._rgb = load_source_rgb(self._image_path)
        # 只显示文件名，不显示完整路径
        self.lb_src.setText(self._image_path.name)
        self._candidates = []
        self.lst.clear()
        self.lb_preview.setText("候选预览（叠在真实图上）")

    # ---------------- 生成候选 ----------------
    def _generate(self):
        if self._image_path is None or self._rgb is None:
            QMessageBox.information(self, "AI 生成", "请先选择原图")
            return
        if not self.ck_consent.isChecked():
            QMessageBox.warning(self, "AI 生成",
                                "上传默认关闭：请显式勾选「允许上传原图」后再生成候选")
            return
        h, w = self._rgb.shape[:2]
        request = AIGenRequest(
            sku=(self.ed_sku.text().strip() or self._image_path.stem),
            stem=self._image_path.stem,
            image_bytes=self._image_path.read_bytes(), width=w, height=h)
        try:
            self._candidates = propose_candidates(self._provider, request, self._config)
        except AIGenError as exc:
            self._candidates = []
            self.lst.clear()
            self.lb_preview.setText("候选预览（叠在真实图上）")
            QMessageBox.warning(self, "AI 生成失败 · 已回手动",
                                f"{exc}\n\n未写入任何文件；请改用手动切割。")
            return
        self.lst.blockSignals(True)
        self.lst.clear()
        for cand in self._candidates:
            item = QListWidgetItem(
                f"{cand.id} · 置信度 {cand.confidence:.2f}"
                + (f" · {'；'.join(cand.warnings)}" if cand.warnings else ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)   # 候选默认一只都不选
            item.setData(Qt.ItemDataRole.UserRole, cand.id)
            self.lst.addItem(item)
        self.lst.blockSignals(False)
        self._refresh_preview()

    def _selected(self) -> list:
        picked = []
        for i in range(self.lst.count()):
            row = self.lst.item(i)
            if row.checkState() == Qt.CheckState.Checked:
                picked.append(row.data(Qt.ItemDataRole.UserRole))
        return [c for c in self._candidates if c.id in set(picked)]

    def _refresh_preview(self, *_):
        if self._rgb is None or not self._candidates:
            return
        selected = self._selected() or self._candidates[:1]
        alpha = np.maximum.reduce([c.alpha for c in selected])
        rgba = np.dstack([self._rgb[..., :3].astype(np.uint8),
                          np.clip(alpha * 255.0, 0, 255).astype(np.uint8)])
        self.lb_preview.setPixmap(_pixmap(composite(self._rgb, rgba)).scaled(
            340, 340, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _reject_all(self):
        self._candidates = []
        self.lst.clear()
        self.lb_preview.setText("候选预览（已整体否决，不写盘）")

    # ---------------- 落盘 ----------------
    def _commit(self):
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, "写入 SKU", "候选默认不勾选：请先逐项选择")
            return
        sku = self.ed_sku.text().strip() or (self._image_path.stem
                                             if self._image_path else "")
        if not sku:
            QMessageBox.information(self, "写入 SKU", "请填写 SKU 名")
            return
        stem = self._image_path.stem if self._image_path else sku
        try:
            info = new_sku_info(sku, self._assets_root)
            reject_existing_base(info, stem)
            changeset = build_changeset(
                sku=sku, stem=stem, rgb=self._rgb, selected=selected,
                provider=str(getattr(self._provider, "name", "")),
                uploaded=self.ck_consent.isChecked())
            result = commit_selected(info, changeset)
        except AIGenError as exc:
            QMessageBox.warning(self, "写入失败 · 已回手动", f"{exc}\n\n未留下半份 SKU。")
            return
        if not result.report.ok:
            QMessageBox.warning(self, "写入失败 · 已回手动", result.report.text)
            return
        QMessageBox.information(self, "写入完成", result.report.text)
        self.accept()


def main() -> int:
    app = QApplication(sys.argv)
    w = NewSkuWizard()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
