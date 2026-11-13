"""动作设置的图形界面。

为什么要有它：`.json` 在不少机器上默认被浏览器接管（打开只显示
"Access to the file was denied"），让用户手改 JSON 不现实。
这里直接列动作、选程序、勾双击，保存到用户配置并即时生效。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from action_bridge import DEFAULT_USER_CFG, user_config_path


class ActionDialog(QDialog):
    """一张表：名称 / 程序 / 双击触发 / 启动前确认 / 删除。"""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("桌宠动作设置")
        self.resize(720, 440)
        cfg = dict(cfg or {})
        reg = dict(cfg.get("registry") or {})
        if not reg:                      # 第一次用：先给两个可用的例子
            reg = dict(DEFAULT_USER_CFG["registry"])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        tip = QLabel("勾「启用」后：双击桌宠（或右键菜单）就会执行对应动作。"
                     "程序用「选择程序…」指到 exe/可执行文件即可，路径只存在你自己的配置里。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6b7785;")
        lay.addWidget(tip)

        self.cb_enabled = QCheckBox("启用动作")
        self.cb_enabled.setChecked(bool(cfg.get("enabled", False)))
        lay.addWidget(self.cb_enabled)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["名称", "程序", "双击触发", "启动前确认", "删除"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        lay.addWidget(self.table, 1)

        bound = (cfg.get("bindings") or {}).get("double_tap") or ""
        for aid, spec in reg.items():
            if isinstance(spec, dict) and spec.get("kind") == "app":
                self._add_row(aid, spec, aid == bound)

        row = QHBoxLayout()
        btn_add = QPushButton("添加应用…")
        btn_add.clicked.connect(lambda: self._add_row("", {}, False, ask=True))
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("primary")
        btn_ok.clicked.connect(self._save)
        row.addWidget(btn_add)
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        lay.addLayout(row)

    # ---------------- 行 ----------------
    def _add_row(self, aid: str, spec: dict, bound: bool, ask: bool = False):
        path = str(spec.get("path") or "")
        label = str(spec.get("label") or "")
        if not path:
            # 例子里只写了候选命令名，本机能找到就先填上，省得用户再选一次
            key = {"win32": "win", "darwin": "mac"}.get(sys.platform, "linux")
            for cand in spec.get(key, []) or []:
                found = shutil.which(str(cand))
                if found:
                    path = found
                    break
        if ask:
            f, _ = QFileDialog.getOpenFileName(self, "选择要启动的程序",
                                               str(Path.home()))
            if not f:
                return
            path = f
            if not label:
                label = Path(f).stem
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setRowHeight(r, 38)

        it = QTableWidgetItem(label or aid)
        it.setData(Qt.ItemDataRole.UserRole, aid)
        self.table.setItem(r, 0, it)

        btn = QPushButton(Path(path).name if path else "选择程序…")
        btn.setToolTip(path or "点这里选一个可执行文件")
        btn.setProperty("path", path)
        btn.setFlat(True)
        btn.clicked.connect(lambda _, b=btn: self._pick(b))
        self.table.setCellWidget(r, 1, btn)

        cb_dbl = QCheckBox()
        cb_dbl.setChecked(bound)
        cb_dbl.toggled.connect(lambda on, w=cb_dbl: self._exclusive(w, on))
        self.table.setCellWidget(r, 2, cb_dbl)

        cb_ok = QCheckBox()
        cb_ok.setChecked(bool(spec.get("confirm", True)))
        self.table.setCellWidget(r, 3, cb_ok)

        btn_del = QPushButton("删除")
        btn_del.clicked.connect(lambda _, b=btn_del: self._del_row(b))
        self.table.setCellWidget(r, 4, btn_del)

    def _pick(self, btn: QPushButton):
        start = btn.property("path") or str(Path.home())
        f, _ = QFileDialog.getOpenFileName(self, "选择要启动的程序", start)
        if not f:
            return
        btn.setProperty("path", f)
        btn.setText(Path(f).name)
        btn.setToolTip(f)
        r = self._row_of(btn)
        if r >= 0:
            it = self.table.item(r, 0)
            if not (it and it.text().strip()):
                self.table.setItem(r, 0, QTableWidgetItem(Path(f).stem))

    def _row_of(self, widget) -> int:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 1) is widget:
                return r
            for c in (2, 3, 4):
                if self.table.cellWidget(r, c) is widget:
                    return r
        return -1

    def _del_row(self, btn: QPushButton):
        r = self._row_of(btn)
        if r >= 0:
            self.table.removeRow(r)

    def _exclusive(self, which: QCheckBox, on: bool):
        if not on:
            return
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            if cb is not which and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)

    # ---------------- 保存 ----------------
    def _save(self):
        registry, binding = {}, ""
        used = set()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            name = (it.text().strip() if it else "") or "未命名动作"
            btn = self.table.cellWidget(r, 1)
            path = str(btn.property("path") or "") if btn else ""
            if not path:
                found = shutil.which(name)
                path = found or ""
            if not path or not Path(path).is_file():
                QMessageBox.warning(self, "还差一步",
                                    f"「{name}」还没选到有效的程序文件。")
                return
            old = (it.data(Qt.ItemDataRole.UserRole) if it else "") or ""
            aid = old if old.startswith("launch:") else f"launch:{Path(path).stem}"
            base, n = aid, 2
            while aid in used:
                aid = f"{base}-{n}"
                n += 1
            used.add(aid)
            registry[aid] = {
                "kind": "app", "label": name, "path": path,
                "confirm": bool(self.table.cellWidget(r, 3).isChecked()),
                "single_instance": True, "hidden": False,
            }
            if self.table.cellWidget(r, 2).isChecked():
                binding = aid

        cfg = {
            "enabled": self.cb_enabled.isChecked(),
            "require_confirm": True,
            "bindings": {"double_tap": binding},
            "registry": registry,
        }
        p = user_config_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.accept()
