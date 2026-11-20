#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可选语音：TTS / ASR 都走外部命令，零第三方依赖、默认关闭。

配置在 ``agent.json`` 的 ``voice`` 段：
``{"enabled": false, "tts": "espeak-ng", "asr": ""}``。
关闭 / 命令为空时不启动任何子进程；失败静默返回，不影响气泡与表情。
本模块不 import Qt，也不进 CI 五包。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess

MAX_TEXT = 400


def voice_config(cfg) -> dict:
    raw = cfg.get("voice") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def _cmd(a: dict, key: str) -> list[str]:
    if a.get("enabled") is not True:
        return []
    raw = a.get(key)
    if isinstance(raw, str):
        return shlex.split(raw)
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    return []


def tts_ready(cfg) -> bool:
    cmd = _cmd(voice_config(cfg), "tts")
    return bool(cmd) and shutil.which(cmd[0]) is not None


def asr_ready(cfg) -> bool:
    cmd = _cmd(voice_config(cfg), "asr")
    return bool(cmd) and shutil.which(cmd[0]) is not None


def _default_runner(cmd, stdin: bytes, timeout: float) -> str:
    proc = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=timeout,
                          check=False)
    return proc.stdout.decode("utf-8", "replace")


def speak(cfg, text: str, *, runner=None) -> bool:
    """把一句回复交给 TTS 命令（文本走 stdin）；未启用返回 False。"""
    cmd = _cmd(voice_config(cfg), "tts")
    text = (text or "").strip()[:MAX_TEXT]
    if not cmd or not text:
        return False
    run = runner or _default_runner
    try:
        run(cmd, text.encode("utf-8"), 15.0)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return False
    return True


def transcribe(cfg, audio_path, *, runner=None) -> str | None:
    """把音频文件路径写到 ASR 命令的 stdin，读回一行文本。"""
    cmd = _cmd(voice_config(cfg), "asr")
    path = str(audio_path or "").strip()
    if not cmd or not path:
        return None
    run = runner or _default_runner
    try:
        text = run(cmd, path.encode("utf-8"), 120.0)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None
    text = (text or "").strip()
    return text[:MAX_TEXT] or None
