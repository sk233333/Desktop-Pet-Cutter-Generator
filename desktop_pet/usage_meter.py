#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token / 费用估算：只按类别累计字符量，不保存正文。

估算按「非空白字符数 / 2 向上取整」，只用于显示量级，不是计费真值。
输出行只有类别与 token 估算，不含 URL、key、原图名或消息正文。

两个入口：
- ``meter_from_config``：单次内存统计（v0.3.1，保持兼容）；
- ``record_usage``：``chat.usage_log=true`` 时把类别累计原子写入
  ``~/.config/desktop_pet/usage.json``（0600），供「用量统计」面板读取。
默认关闭：开关不是布尔 ``True`` 时不创建 meter、不落盘、不打印。
"""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()


def estimate_tokens(text) -> int:
    """粗估 token 数；空值与非字符串返回 0。"""
    if not isinstance(text, str) or not text:
        return 0
    chars = sum(1 for ch in text if not ch.isspace())
    return int(math.ceil(chars / 2.0)) if chars else 0


class UsageMeter:
    """按类别累计 input / output 估算值。"""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, int]] = {}

    def record(self, category: str, input_text="", output_text="") -> dict:
        cat = category if isinstance(category, str) and category else "unknown"
        row = self._rows.setdefault(cat, {"input": 0, "output": 0})
        tin = estimate_tokens(input_text)
        tout = estimate_tokens(output_text)
        row["input"] += tin
        row["output"] += tout
        return {"category": cat, "input": tin, "output": tout}

    def lines(self) -> list[str]:
        """只返回类别与估算值，调用侧可直接写 stderr。"""
        return [
            f"{cat} in≈{row['input']} tok out≈{row['output']} tok"
            for cat, row in sorted(self._rows.items())
        ]

    def to_rows(self) -> dict:
        return {cat: dict(row) for cat, row in self._rows.items()}

    def load_rows(self, rows) -> None:
        if not isinstance(rows, dict):
            return
        for cat, row in rows.items():
            if not isinstance(cat, str) or not isinstance(row, dict):
                continue
            self._rows[cat] = {
                "input": _nonneg(row.get("input")),
                "output": _nonneg(row.get("output")),
            }


def _nonneg(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def ledger_path() -> Path:
    env = os.environ.get("DESKTOP_PET_USAGE_PATH", "")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".config" / "desktop_pet" / "usage.json"


def load_ledger(path=None) -> UsageMeter:
    target = Path(path) if path else ledger_path()
    meter = UsageMeter()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return meter
    if isinstance(data, dict):
        meter.load_rows(data.get("rows"))
    return meter


def save_ledger(meter: UsageMeter, path=None) -> Path:
    """原子写 0600；只写类别累计，不写正文。"""
    target = Path(path) if path else ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 1, "rows": meter.to_rows()},
                         ensure_ascii=False, sort_keys=True)
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def ledger_enabled(cfg) -> bool:
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    chat = chat if isinstance(chat, dict) else {}
    return chat.get("usage_log") is True


def record_usage(cfg, category: str, input_text="",
                 output_text="") -> list[str]:
    """开关打开时累计到磁盘并返回全部类别行；关闭时零副作用。"""
    if not ledger_enabled(cfg):
        return []
    with _LOCK:
        meter = load_ledger()
        meter.record(category, input_text, output_text)
        try:
            save_ledger(meter)
        except OSError:
            pass
        return meter.lines()


def ledger_lines(path=None) -> list[str]:
    """给 UI 读当前累计；文件缺失时返回空列表。"""
    return load_ledger(path).lines()


def meter_from_config(cfg) -> UsageMeter | None:
    """``chat.usage_log`` 默认关；关闭时不创建 meter，也不触发任何输出。"""
    if not ledger_enabled(cfg):
        return None
    return UsageMeter()
