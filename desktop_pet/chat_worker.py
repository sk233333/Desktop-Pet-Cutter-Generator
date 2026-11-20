#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A1 QThread 壳：只负责把 provider 的增量转成 Qt 信号。

顶层 import PyQt6，因此 **禁止** 被 tests 收集（C-A13）。
纯逻辑全部在 chat_contract / chat_provider，本文件不写状态、不碰 arbiter。
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from .chat_contract import build_system_prompt, clamp, safe_parse
    from .chat_provider import stream_chat
except ImportError:  # 直接以脚本 / 平铺 sys.path 运行时
    from chat_contract import build_system_prompt, clamp, safe_parse
    from chat_provider import stream_chat


class ChatWorker(QThread):
    chunk = pyqtSignal(str)      # 逐字 → 气泡 UI
    done = pyqtSignal(dict)      # clamp 后的 reply
    failed = pyqtSignal()        # 静默信号，不带错误文本

    def __init__(self, cfg: dict, persona: dict, allowed: dict,
                 messages: list[dict], parent=None, *, sku: str = "",
                 memories=None):
        super().__init__(parent)
        self.cfg = cfg
        self.persona = persona or {}
        self.allowed = allowed or {}
        self.messages = list(messages or [])
        self.sku = sku or ""
        self.memories = list(memories or [])

    def run(self):
        sys_msg = build_system_prompt(self.persona, self.allowed,
                                      self.memories)
        emitted = False
        # 最多 1 次退避重试；已吐出片段后不再重试，避免气泡重复
        for attempt in range(2):
            buf: list[str] = []
            try:
                for token in stream_chat(self.cfg, sys_msg, self.messages,
                                         sku=self.sku):
                    if self.isInterruptionRequested():
                        return
                    if not token:
                        continue
                    emitted = True
                    buf.append(token)
                    self.chunk.emit(token)
                reply = safe_parse("".join(buf), None)
                if reply is None:
                    raise ValueError("回复不是合法 JSON")
                self.done.emit(clamp(reply, self.allowed))
                return
            except Exception:
                if emitted or attempt == 1:
                    self.failed.emit()
                    return
                time.sleep(0.4)
