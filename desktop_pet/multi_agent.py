#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置期多 Agent 纯逻辑：串行扇出与冲突标记。

``fan_out`` 只按顺序调用注入的 runner，不启动线程/进程；``debate_gate``
只把报告按主题与归一化文本分组并标记冲突，不自动合并、不自动裁决、
不写盘。
"""

from __future__ import annotations

MAX_AGENTS = 3


def _as_text(value) -> str:
    return value if isinstance(value, str) else ""


def fan_out(prompts, runner, *, max_agents=3) -> dict:
    """依次向 runner 提问；prompts 超过 max_agents 的部分直接截断。"""
    try:
        limit = int(max_agents)
    except (TypeError, ValueError):
        limit = MAX_AGENTS
    limit = max(0, limit)

    items = list(prompts) if isinstance(prompts, (list, tuple)) else []
    reports: list[dict] = []
    for prompt in items[:limit]:
        raw = runner(prompt)
        data = raw if isinstance(raw, dict) else {}
        reports.append({
            "prompt": prompt,
            "text": _as_text(data.get("text")),
            "emotion": _as_text(data.get("emotion")),
        })
    return {"reports": reports, "conflicts": []}


def _topic(item: dict) -> str:
    for key in ("topic", "prompt"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def debate_gate(reports) -> dict:
    """按主题聚合 strip 后的文本；同一主题出现多种文本即标记冲突。"""
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for item in reports or []:
        if not isinstance(item, dict):
            continue
        topic = _topic(item)
        text = _as_text(item.get("text")).strip()
        if not text:
            continue
        if topic not in grouped:
            grouped[topic] = []
            order.append(topic)
        if text not in grouped[topic]:
            grouped[topic].append(text)

    conflicts = []
    for topic in order:
        variants = grouped[topic]
        if len(variants) > 1:
            conflicts.append({"topic": topic, "variants": variants})
    return {"conflicts": conflicts, "ok": not conflicts}
