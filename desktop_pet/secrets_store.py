#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API key 解析：环境变量优先，钥匙串与 0600 文件均为可选。

默认路径与旧版一致：只读 ``chat.key_env`` 指向的环境变量，不 import
任何第三方库。只有 ``chat.keyring is True`` 时才尝试 ``keyring``（未安装
则静默跳过）；只有 ``chat.key_file`` 非空时才读文件，且 POSIX 下文件权限
必须不向 group / others 开放，否则拒绝读取。钥匙串 / 文件都只是环境变量
缺失时的后备，不会覆盖环境变量。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

SERVICE = "desktop_pet"


def _chat(cfg) -> dict:
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    return chat if isinstance(chat, dict) else {}


def key_env_name(cfg) -> str:
    name = str(_chat(cfg).get("key_env") or "DESKTOP_PET_API_KEY").strip()
    return name or "DESKTOP_PET_API_KEY"


def _keyring_key(chat: dict, name: str) -> str:
    if chat.get("keyring") is not True:
        return ""
    try:
        import keyring  # 可选依赖：未安装时静默回退
        return keyring.get_password(SERVICE, name) or ""
    except Exception:                     # noqa: BLE001 - 钥匙串不可用不算错误
        return ""


def _file_key(chat: dict) -> str:
    raw = str(chat.get("key_file") or "").strip()
    if not raw:
        return ""
    path = Path(os.path.expanduser(raw))
    try:
        if not path.is_file():
            return ""
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:              # 必须是 0600（或更严）
                return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_key(cfg) -> str:
    """环境变量 > 钥匙串 > 0600 文件；三者都没有时返回空串。"""
    chat = _chat(cfg)
    name = key_env_name(cfg)
    env = os.environ.get(name, "")
    if env:
        return env
    return _keyring_key(chat, name) or _file_key(chat)
