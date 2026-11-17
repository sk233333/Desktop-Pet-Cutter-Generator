#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力补全向导（六步 QDialog）。

不是软件版本升级：只扫描当前 SKU 缺什么 → 列可补项 → 出候选 → 人工点选
→ 统一落盘 → 回归。首期只注册三个纯本地补全器，不联网、不拉权重。

单独运行（离屏冒烟）：
    QT_QPA_PLATFORM=offscreen python3 desktop_pet/upgrade_wizard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QGroupBox, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QPushButton,
                             QStackedWidget, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from completers.base import composite                      # noqa: E402
from completers.registry import entries as completer_entries  # noqa: E402
from upgrade_core import (commit_changes, health_items, load_sku,  # noqa: E402
                          manifest_diff_summary, rollback, scan_skus)

STEPS = ("选 SKU", "体检报告", "挑补全器", "并排预览", "落盘 + 回归", "报告")


class UpgradeWizard(QDialog):
    """六步模态向导；引擎由外部注入，本类不持有所有权。"""

    def __init__(self, parent=None, engine=None, assets_root=None):
        super().__init__(parent)
        self.setWindowTitle("能力补全向导")
        self.resize(1000, 740)
        # 只保存引用：关闭时不得 dispose / 重建，否则主窗口预览会黑
        self._engine = engine
        self._assets_root = assets_root
        self._skus = scan_skus(assets_root)
        self._info = None
        self._cands: list = []
        self._vetoed: set[str] = set()
        self._committed = False
        self._log_lines: list[str] = []
        self._report_lines: list[str] = []
        self._build_ui()
        self._reload_skus()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.lb_title = QLabel("能力补全向导 · 首期")
        self.lb_title.setStyleSheet("font-size:18px;font-weight:800;color:#3e4a5c;")
        self.lb_step = QLabel()
        self.lb_step.setStyleSheet("color:#8d99a8;")
        root.addWidget(self.lb_title)
        root.addWidget(self.lb_step)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_sku())
        self.stack.addWidget(self._page_health())
        self.stack.addWidget(self._page_completers())
        self.stack.addWidget(self._page_preview())
        self.stack.addWidget(self._page_commit())
        self.stack.addWidget(self._page_report())
        root.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("上一步")
        self.btn_prev.clicked.connect(lambda: self._go(-1))
        self.btn_next = QPushButton("下一步")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(lambda: self._go(1))
        nav.addStretch(1)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        root.addLayout(nav)
        self.stack.currentChanged.connect(self._sync_nav)
        self._sync_nav()

    def _page_sku(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 0 · 选 SKU（扫 assets/*/manifest.json，"
                           "目录名为 SKU 主键）"))
        row = QHBoxLayout()
        self.cb_sku = QComboBox()
        self.cb_sku.currentIndexChanged.connect(self._on_sku_changed)
        btn = QPushButton("重新扫描")
        btn.clicked.connect(self._reload_skus)
        row.addWidget(self.cb_sku, 1)
        row.addWidget(btn)
        v.addLayout(row)
        self.lb_sku = QLabel()
        self.lb_sku.setWordWrap(True)
        v.addWidget(self.lb_sku)
        v.addStretch(1)
        return w

    def _page_health(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 1 · 体检报告：Issue / CHANGELOG / HANDOFF 里的 "
                           "pending 第一次变成可见清单"))
        self.tb_health = QTableWidget(0, 3)
        self.tb_health.setHorizontalHeaderLabels(["状态", "项", "说明"])
        self.tb_health.horizontalHeader().setStretchLastSection(True)
        self.tb_health.verticalHeader().setVisible(False)
        v.addWidget(self.tb_health, 1)
        row = QHBoxLayout()
        btn = QPushButton("重新体检")
        btn.clicked.connect(self._fill_health)
        batch = QPushButton("批量体检（只出报告，不写盘）")
        batch.clicked.connect(self._batch_report)
        row.addWidget(btn)
        row.addWidget(batch)
        row.addStretch(1)
        v.addLayout(row)
        self.lb_health = QLabel()
        self.lb_health.setWordWrap(True)
        v.addWidget(self.lb_health)
        return w

    def _page_completers(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 2 · 挑补全器：只做已有能力的补数据 / 补标注"))
        self.lw_comp = QListWidget()
        for e in completer_entries():
            item = QListWidgetItem(f"{e.id} · {e.domain} · 风险{e.risk}\n{e.summary}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, e.id)
            self.lw_comp.addItem(item)
        v.addWidget(self.lw_comp, 1)
        return w

    def _page_preview(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 3 · 并排预览：叠在真实图上；逐候选可整体否决"))
        self.cb_cand = QComboBox()
        self.cb_cand.currentIndexChanged.connect(self._on_cand_changed)
        v.addWidget(self.cb_cand)

        pair = QHBoxLayout()
        self.lb_before = QLabel("原图")
        self.lb_after = QLabel("叠加候选")
        for lb in (self.lb_before, self.lb_after):
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setMinimumSize(300, 300)
            lb.setStyleSheet("border:2px dashed #dfeaf8;border-radius:12px;"
                             "background:#fbfeff;")
            pair.addWidget(lb, 1)
        v.addLayout(pair, 1)

        self.gb_pick = QGroupBox("候选参数")
        self.pick_layout = QVBoxLayout(self.gb_pick)
        v.addWidget(self.gb_pick)

        row = QHBoxLayout()
        self.btn_veto = QPushButton("否决此候选")
        self.btn_veto.clicked.connect(self._veto_current)
        self.lb_veto = QLabel("")
        self.lb_veto.setStyleSheet("color:#e2645b;")
        row.addWidget(self.btn_veto)
        row.addWidget(self.lb_veto)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _page_commit(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 4 · 落盘 + 回归：统一过 validate_manifest，"
                           "失败整体回滚"))
        row = QHBoxLayout()
        self.btn_commit = QPushButton("执行落盘 + 回归")
        self.btn_commit.clicked.connect(self._run_commit)
        row.addWidget(self.btn_commit)
        row.addStretch(1)
        v.addLayout(row)
        self.tx_log = QPlainTextEdit()
        self.tx_log.setReadOnly(True)
        v.addWidget(self.tx_log, 1)
        return w

    def _page_report(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Step 5 · 报告：manifest 字节 diff / 改了哪些层 / "
                           "失败项是否回滚"))
        self.tx_report = QPlainTextEdit()
        self.tx_report.setReadOnly(True)
        v.addWidget(self.tx_report, 1)
        v.addWidget(QLabel("提示：克隆仓库后需自备素材，版权随原图（见 docs/ASSETS.md）。"))
        return w

    # ---------------- 数据 ----------------
    def _reload_skus(self):
        self._skus = scan_skus(self._assets_root)
        self.cb_sku.blockSignals(True)
        self.cb_sku.clear()
        for info in self._skus:
            self.cb_sku.addItem(info.name)
        self.cb_sku.blockSignals(False)
        self._on_sku_changed(0)

    def _current_info(self):
        if not self._skus:
            return None
        idx = max(0, self.cb_sku.currentIndex())
        return self._skus[idx]

    def _on_sku_changed(self, _idx):
        info = self._current_info()
        self._info = load_sku(info) if info else None
        if self._info is None:
            self.lb_sku.setText("没有找到 assets/*/manifest.json")
        else:
            self.lb_sku.setText(
                f"目录：{self._info.dir}\nschema_version：{self._info.schema_version}"
                f"　目录名 / sku：{'一致' if self._info.consistent else '不一致'}"
                f"　items：{len(self._info.manifest.get('items') or [])}")
            self._fill_health()

    def _fill_health(self):
        if self._info is None:
            return
        rows = health_items(self._info)
        self.tb_health.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.tb_health.setItem(r, 0, QTableWidgetItem(row["status"]))
            self.tb_health.setItem(r, 1, QTableWidgetItem(row["label"]))
            self.tb_health.setItem(r, 2, QTableWidgetItem(row["detail"]))
            self.tb_health.item(r, 1).setToolTip(row.get("detail", ""))
        self.tb_health.resizeColumnsToContents()
        self.tb_health.setColumnWidth(2, max(320, self.tb_health.columnWidth(2)))
        self.lb_health.setText(
            f"共 {len(rows)} 项；Issue #1/#2 只做入口展示，不自动关单。"
            "smile 标 v0.1.1，本轮不可勾选。")

    def _batch_report(self):
        if self._info is None:
            return
        lines = [f"[批量体检] {self._info.name}（只出报告，不写盘）"]
        lines += [f"- [{r['status']}] {r['label']}：{r['detail']}"
                  for r in health_items(self._info)]
        self._append_report(lines)
        self.lb_health.setText("批量体检报告已追加到 Step 5；没有产生任何文件改动。")

    # ---------------- 候选 ----------------
    def _collect_candidates(self):
        self._cands = []
        if self._info is None:
            return
        for i in range(self.lw_comp.count()):
            item = self.lw_comp.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            cid = item.data(Qt.ItemDataRole.UserRole)
            mod = next((e.module for e in completer_entries() if e.id == cid), None)
            if mod is None:
                continue
            try:
                self._cands.extend(mod.propose(self._info, self._info.manifest))
            except Exception as exc:         # noqa: BLE001
                self._append_report([f"[error] {cid}.propose：{exc}"])
        self._vetoed.clear()
        self._committed = False
        self.cb_cand.blockSignals(True)
        self.cb_cand.clear()
        for c in self._cands:
            self.cb_cand.addItem(f"{c.id}", c.id)
        self.cb_cand.blockSignals(False)
        if self._cands:
            self.cb_cand.setCurrentIndex(0)
            self._on_cand_changed(0)

    def _cand(self):
        idx = self.cb_cand.currentIndex()
        if 0 <= idx < len(self._cands):
            return self._cands[idx]
        return None

    def _on_cand_changed(self, _idx):
        while self.pick_layout.count():
            w = self.pick_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        cand = self._cand()
        if cand is None:
            self.lb_before.setPixmap(QPixmap())
            self.lb_after.setPixmap(QPixmap())
            return
        mod = next((e.module for e in completer_entries()
                    if e.id == self._completer_of(cand)), None)
        if mod is not None:
            try:
                prev = mod.preview(cand, self._engine)
                self._show_pair(prev.base, prev.overlay)
            except Exception as exc:         # noqa: BLE001
                self.lb_after.setText(f"预览失败：{exc}")

        if cand.kind == "mask" and cand.payload.get("eyes"):
            self.pick_layout.addWidget(QLabel("逐只点清真眼（不按「前 N 只」批量）："))
            selected = set(cand.payload.get("selected") or [])
            for eye in cand.payload["eyes"]:
                k = eye["index"]
                cb = QCheckBox(f"眼 {k}  (cx={eye['cx']:.0f}, cy={eye['cy']:.0f})")
                cb.setChecked(k in selected)
                cb.toggled.connect(
                    lambda on, idx=k, c=cand: self._toggle_eye(c, idx, on))
                self.pick_layout.addWidget(cb)
        elif cand.kind == "mouth":
            self.pick_layout.addWidget(QLabel("嘴型档位："))
            cb = QComboBox()
            for lv in cand.payload.get("levels") or []:
                cb.addItem(lv)
            cur = cand.payload.get("selected") or "smile"
            cb.setCurrentText(cur)
            cb.currentTextChanged.connect(
                lambda txt, c=cand: self._set_mouth(c, txt))
            self.pick_layout.addWidget(cb)
        else:
            note = cand.note or "只读提示：不写盘"
            lb = QLabel(note)
            lb.setWordWrap(True)
            self.pick_layout.addWidget(lb)

        self.lb_veto.setText(
            "已否决：不产生任何路径与 patch" if cand.id in self._vetoed else "")

    def _completer_of(self, cand) -> str:
        for e in completer_entries():
            if cand.id.startswith(e.id + ":"):
                return e.id
        return ""

    def _toggle_eye(self, cand, idx, on):
        sel = set(cand.payload.get("selected") or [])
        if on:
            sel.add(idx)
        else:
            sel.discard(idx)
        cand.payload["selected"] = sorted(sel)
        self._refresh_current_preview()

    def _set_mouth(self, cand, text):
        cand.payload["selected"] = text
        self._refresh_current_preview()

    def _refresh_current_preview(self):
        cand = self._cand()
        if cand is None:
            return
        mod = next((e.module for e in completer_entries()
                    if e.id == self._completer_of(cand)), None)
        if mod is None:
            return
        try:
            prev = mod.preview(cand, self._engine)
            self._show_pair(prev.base, prev.overlay)
        except Exception as exc:             # noqa: BLE001
            self.lb_after.setText(f"预览失败：{exc}")

    def _veto_current(self):
        cand = self._cand()
        if cand is None:
            return
        self._vetoed.add(cand.id)
        self.lb_veto.setText("已否决：不产生任何路径与 patch")

    def _show_pair(self, base_rgb, overlay_rgba):
        after = composite(base_rgb, overlay_rgba)
        self.lb_before.setPixmap(self._pixmap(base_rgb))
        self.lb_after.setPixmap(self._pixmap(after))

    @staticmethod
    def _pixmap(rgb: np.ndarray) -> QPixmap:
        arr = np.ascontiguousarray(rgb)
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, arr.strides[0],
                     QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(img).scaled(
            300, 300, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

    # ---------------- 落盘 ----------------
    def _run_commit(self):
        if self._info is None or self._committed:
            return
        self._committed = True
        lines = ["== 落盘 + 回归 =="]
        for cand in self._cands:
            if cand.id in self._vetoed:
                lines.append(f"[skip] {cand.id}：已否决，零路径")
                continue
            if not cand.commit_allowed:
                lines.append(f"[skip] {cand.id}：只读候选，不落盘")
                continue
            cid = self._completer_of(cand)
            mod = next((e.module for e in completer_entries() if e.id == cid), None)
            if mod is None:
                lines.append(f"[skip] {cand.id}：未注册")
                continue
            cs = mod.commit(cand)
            if not cs.files and not cs.manifest_patch:
                lines.append(f"[skip] {cand.id}：{cs.notes or '无变更'}")
                continue
            res = commit_changes(self._info, cs)
            lines += [f"[{cand.id}] " + ln for ln in res.report.lines]
            if not res.report.ok:
                lines.append(f"[abort] {cand.id} 落盘失败，后续候选不再执行")
                break
            rep = mod.verify(cs, self._info.dir, res.merged)
            lines += [f"[verify] " + ln for ln in rep.lines]
            if not rep.ok:
                rb = rollback(res)
                lines += [f"[rollback] " + ln for ln in rb.lines]
                break
            if res.migrated:
                lines.append("[manifest] v1→v2 迁移 + 补丁（派生字段随之生成）")
            else:
                lines += ["[manifest] " + ln for ln in
                          manifest_diff_summary(self._info.manifest, res.merged)]
            self._info = load_sku(self._info)
        self.tx_log.setPlainText("\n".join(lines))
        self._append_report(lines)
        self._fill_health()

    def _append_report(self, lines):
        self._report_lines.extend(lines)
        self.tx_report.setPlainText("\n".join(self._report_lines))

    # ---------------- 导航 ----------------
    def _go(self, delta):
        idx = self.stack.currentIndex()
        if delta > 0:
            if idx == 0 and self._info is None:
                return
            if idx == 1:
                self.stack.setCurrentIndex(2)
                return
            if idx == 2:
                self._collect_candidates()
                self.stack.setCurrentIndex(3)
                return
            if idx == 3:
                self.stack.setCurrentIndex(4)
                return
            if idx == 4:
                self._run_commit()
                self.stack.setCurrentIndex(5)
                return
            if idx == 5:
                self.accept()
                return
        idx = max(0, min(5, idx + delta))
        self.stack.setCurrentIndex(idx)

    def _sync_nav(self, _idx=None):
        idx = self.stack.currentIndex()
        self.lb_step.setText(f"Step {idx} · {STEPS[idx]}")
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setText("完成" if idx == 5 else "下一步")

    # ---------------- 生命周期 ----------------
    def closeEvent(self, event):
        # 注入的引擎属于主窗口：这里不 dispose、不替换、不置空
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    w = UpgradeWizard()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
