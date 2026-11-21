#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A1 QThread 壳：只负责把 provider 的增量转成 Qt 信号。

顶层 import PyQt6，因此 **禁止** 被 tests 收集（C-A13）。
纯逻辑全部在 chat_contract / chat_provider，本文件不写状态、不碰 arbiter。
"""

from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from .agent_core import loop_config, parse_step_reply, run_loop
    from .chat_contract import (build_system_prompt, clamp, retry_attempts,
                                safe_parse)
    from .chat_provider import stream_chat
    from .usage_meter import record_usage
except ImportError:  # 直接以脚本 / 平铺 sys.path 运行时
    from agent_core import loop_config, parse_step_reply, run_loop
    from chat_contract import (build_system_prompt, clamp, retry_attempts,
                               safe_parse)
    from chat_provider import stream_chat
    from usage_meter import record_usage


class _Interrupted(Exception):
    """用户中断流式输出；只用于跳出循环，不上报 failed。"""


class SpeakWorker(QThread):
    """TTS 外部命令放后台跑；只回报成功与否，不碰状态与气泡。"""

    done = pyqtSignal(bool)

    def __init__(self, cfg: dict, text: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.text = text

    def run(self):
        try:
            try:
                from .voice_io import speak
            except ImportError:
                from voice_io import speak
            self.done.emit(bool(speak(self.cfg, self.text)))
        except Exception:
            self.done.emit(False)


class AsrWorker(QThread):
    """ASR 外部命令后台线程；空结果用空串回报，由调用方回「没听清」。"""

    done = pyqtSignal(str)

    def __init__(self, cfg: dict, path: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.path = path

    def run(self):
        try:
            try:
                from .voice_io import transcribe
            except ImportError:
                from voice_io import transcribe
            self.done.emit(transcribe(self.cfg, self.path) or "")
        except Exception:
            self.done.emit("")


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

    def _collect(self, sys_msg: str, messages: list[dict], *, emit: bool,
                 emitted: list):
        """跑一次 provider；``emitted[0]`` 记录是否已吐字。"""
        buf: list[str] = []
        for token in stream_chat(self.cfg, sys_msg, messages, sku=self.sku):
            if self.isInterruptionRequested():
                raise _Interrupted
            if not token:
                continue
            emitted[0] = True
            buf.append(token)
            if emit:
                self.chunk.emit(token)
        return "".join(buf)

    def _deliver(self, sys_msg: str, messages: list[dict], reply: dict):
        """唯一落点：先记类别用量（默认关），再 clamp 后发 done。"""
        text_in = sys_msg + "".join(
            str(m.get("content") or "") for m in messages
            if isinstance(m, dict))
        for line in record_usage(self.cfg, "chat", text_in,
                                 str(reply.get("text") or "")):
            print(f"[usage] {line}", file=sys.stderr)
        self.done.emit(clamp(reply, self.allowed))

    def run(self):
        sys_msg = build_system_prompt(self.persona, self.allowed,
                                      self.memories)
        loop = loop_config(self.cfg)
        try:
            if loop["enabled"]:
                self._run_loop(sys_msg, loop)
            else:
                self._run_once(sys_msg)
        except _Interrupted:
            return

    def _run_once(self, sys_msg: str):
        attempts = retry_attempts(self.cfg)
        emitted = [False]
        # 默认 retry=0 只跑一次；已吐出片段后不再重试，避免气泡重复。
        for attempt in range(attempts):
            try:
                raw = self._collect(sys_msg, self.messages, emit=True,
                                    emitted=emitted)
                reply = safe_parse(raw, None)
                if reply is None:
                    raise ValueError("回复不是合法 JSON")
                self._deliver(sys_msg, self.messages, reply)
                return
            except _Interrupted:
                raise
            except Exception:
                if emitted[0] or attempt == attempts - 1:
                    self.failed.emit()
                    return
                time.sleep(0.4)

    def _run_loop(self, sys_msg: str, loop: dict):
        """agent.enabled 且 max_steps>1 时走 L2 循环；默认关。"""
        prev = ""
        continued = True

        def step(index: int):
            nonlocal prev, continued
            if index > 0 and not continued:
                return None
            extra = list(self.messages)
            if index > 0:
                extra.extend([
                    {"role": "assistant", "content": prev},
                    {"role": "user", "content": "继续下一步"},
                ])
            raw = self._collect(sys_msg, extra, emit=(index == 0),
                                emitted=[False])
            reply = parse_step_reply(raw)
            if not reply:
                raise ValueError("回复不是合法 JSON")
            prev = str(reply.get("text") or "") or prev
            continued = isinstance(reply.get("change_set"), dict) \
                or bool(reply.get("report"))
            fields = ("text", "emotion", "action", "mood_hint", "remember",
                      "change_set", "report")
            return {"tool": "notify_bubble",
                    **{k: v for k, v in reply.items() if k in fields}}

        try:
            result = run_loop(step, max_steps=loop["max_steps"],
                              timeout_ms=loop["timeout_ms"])
        except _Interrupted:
            raise
        except Exception:
            self.failed.emit()
            return
        if not result.text and not result.change_set and not result.report:
            self.failed.emit()
            return
        self._deliver(sys_msg, self.messages, {
            "text": result.text,
            "emotion": result.emotion,
            "action": result.action,
            "mood_hint": result.mood_hint,
            "remember": result.remember,
        })
