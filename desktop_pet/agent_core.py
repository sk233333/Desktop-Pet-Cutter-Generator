#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 自主循环的纯逻辑层：工具白名单、步数/超时闸门、字段裁剪。

零 Qt、零第三方依赖、不写盘；只提供纯函数与 dataclass。真正的工具执行、
状态提交与界面渲染都由调用侧负责，本模块只决定「何时停、停在哪」。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

TOOL_WHITELIST = ("get_health", "get_allowed", "get_manifest_item",
                  "propose_candidate", "notify_bubble")
RESULT_FIELDS = ("text", "emotion", "action", "mood_hint", "remember",
                 "change_set", "report")
TEXT_LIMIT = 400
MAX_STEPS = 8


@dataclass
class AgentStep:
    """一次 step_fn 调用留下的最小轨迹。"""

    tool: str = ""
    args: dict | None = None
    output: Any = None


@dataclass
class LoopResult:
    """循环结束后的合并结果；只有 RESULT_FIELDS 会从 step 里进入这里。"""

    text: str = ""
    emotion: str = ""
    action: str = ""
    mood_hint: str = ""
    remember: str = ""
    change_set: Any = None
    report: Any = None
    steps: list[AgentStep] = field(default_factory=list)
    stopped_reason: str = ""


def parse_step_reply(raw) -> dict:
    """解析 L2 单步回复：允许只有 ``report`` / ``change_set``。

    单次对话仍走 ``chat_contract.safe_parse`` 的严格三字段；循环步是候选源，
    中间步没有 text 时不应直接判失败。非法输入返回 ``{}``。
    """
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    for key in ("text", "emotion", "action", "mood_hint", "remember"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            out[key] = val
    for key in ("change_set", "report"):
        val = obj.get(key)
        if isinstance(val, (dict, list)):
            out[key] = val
    return out


def normalize_result(raw: dict) -> dict:
    """只保留允许字段；text / remember 为字符串并截到 400 字符。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in RESULT_FIELDS:
        if key not in raw:
            continue
        val = raw[key]
        if key in ("text", "emotion", "action", "mood_hint", "remember"):
            if not isinstance(val, str):
                continue
            if key in ("text", "remember"):
                val = val.strip()[:TEXT_LIMIT]
            if not val:
                continue
        elif val is None:
            continue
        out[key] = val
    return out


def _timeout_seconds(timeout_ms) -> float:
    try:
        ms = float(timeout_ms)
    except (TypeError, ValueError):
        ms = 8000.0
    return max(0.0, ms) / 1000.0


def loop_config(cfg) -> dict:
    """从 ``agent.json`` 的 ``agent`` 段解析循环开关。

    默认关：``agent.enabled`` 必须是布尔 ``True`` 且 ``max_steps > 1``；
    否则调用侧继续走单次对话，行为与旧版一致。
    """
    raw = cfg.get("agent") if isinstance(cfg, dict) else None
    agent = raw if isinstance(raw, dict) else {}
    enabled = agent.get("enabled") is True
    try:
        steps = int(agent.get("max_steps") or 4)
    except (TypeError, ValueError):
        steps = 4
    steps = max(1, min(steps, MAX_STEPS))
    try:
        timeout_ms = float(agent.get("timeout_ms") or 8000)
    except (TypeError, ValueError):
        timeout_ms = 8000.0
    return {
        "enabled": bool(enabled and steps > 1),
        "max_steps": steps,
        "timeout_ms": max(0.0, timeout_ms),
    }


def run_loop(step_fn, *, max_steps=4, timeout_ms=8000,
             clock=time.monotonic) -> LoopResult:
    """按步数 / 超时 / 白名单三个闸门跑一次纯逻辑循环。

    ``step_fn(index)`` 返回 dict 或 None；None 与 ``{}`` 都表示没有新信息。
    返回的 tool 不在 ``TOOL_WHITELIST`` 时立即停止，且该步字段不并入结果。
    """
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise ValueError("max_steps 必须是 1..8 的整数")
    if not 1 <= max_steps <= MAX_STEPS:
        raise ValueError("max_steps 必须在 1..8 之间")

    limit = _timeout_seconds(timeout_ms)
    started = clock()
    steps: list[AgentStep] = []
    merged: dict = {}
    stopped = "max_steps"

    for index in range(max_steps):
        if clock() - started > limit:
            stopped = "timeout"
            break

        raw = step_fn(index)

        if clock() - started > limit:
            stopped = "timeout"
            break
        if raw is None or raw == {}:
            stopped = "no_new_info"
            break
        if not isinstance(raw, dict):
            stopped = "no_new_info"
            break

        tool = raw.get("tool")
        steps.append(AgentStep(
            tool=tool if isinstance(tool, str) else "",
            args=raw.get("args"),
            output=raw.get("output"),
        ))
        if tool is not None and tool not in TOOL_WHITELIST:
            stopped = "tool_not_allowed"
            break

        merged.update(normalize_result(raw))

    return LoopResult(
        text=merged.get("text", ""),
        emotion=merged.get("emotion", ""),
        action=merged.get("action", ""),
        mood_hint=merged.get("mood_hint", ""),
        remember=merged.get("remember", ""),
        change_set=merged.get("change_set"),
        report=merged.get("report"),
        steps=steps,
        stopped_reason=stopped,
    )
