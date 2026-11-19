#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地分割推理放到 QThread：UI 线程只等结果，不冻结。

只被 ui_app 的「本地 AI 抠图」分支使用；CI 五包没有 PyQt6，测试不导入本文件。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from ai_generate_contract import AIGenError, propose_candidates


class MattingWorker(QThread):
    """跑一次 ``propose_candidates``；成功发候选列表，失败发可读原因。"""

    prepared = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, provider, request, config, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._request = request
        self._config = config

    def run(self):
        try:
            cands = propose_candidates(self._provider, self._request, self._config)
        except AIGenError as exc:
            self.failed.emit(str(exc))
            return
        except Exception:                        # noqa: BLE001
            self.failed.emit("本地分割异常，已回手动")
            return
        self.prepared.emit(cands)
