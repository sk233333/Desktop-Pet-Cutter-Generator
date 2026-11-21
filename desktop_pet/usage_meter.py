#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token / 费用估算：只按类别累计字符量，不保存正文。

估算按「非空白字符数 / 2 向上取整」，只用于显示量级，不是计费真值。
输出行只有类别与 token 估算，不含 URL、key、原图名或消息正文。
默认关闭：``chat.usage_log`` 不是布尔 ``True`` 时 ``meter_from_config()``
返回 ``None``，调用侧不打印任何东西。
"""

from __future__ import annotations

import math


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


def meter_from_config(cfg) -> UsageMeter | None:
    """``chat.usage_log`` 默认关；关闭时不创建 meter，也不触发任何输出。"""
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    chat = chat if isinstance(chat, dict) else {}
    if chat.get("usage_log") is not True:
        return None
    return UsageMeter()
