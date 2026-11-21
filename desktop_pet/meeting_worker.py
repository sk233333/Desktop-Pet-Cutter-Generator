#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.3.2 开会 QThread 壳：只把配置期评审结果转成信号。

顶层 import PyQt6，禁止被 tests 收集。纯逻辑全在 ``meeting``；
本文件不写盘、不碰 arbiter、不改 manifest。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from .meeting import discuss
except ImportError:  # 平铺 sys.path
    from meeting import discuss


class MeetingWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, chat_cfg: dict, plan: str, meeting_cfg=None, parent=None):
        super().__init__(parent)
        self.chat_cfg = chat_cfg
        self.plan = plan
        self.meeting_cfg = meeting_cfg

    def run(self):
        try:
            self.done.emit(discuss(self.chat_cfg, self.plan,
                                   cfg=self.meeting_cfg))
        except Exception as exc:
            # 只回通用原因；不把 key / URL / 路径带到界面
            self.failed.emit(str(exc) or "meeting_failed")
