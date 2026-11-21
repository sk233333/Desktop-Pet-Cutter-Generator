#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多模型路由：按任务类型选 profile / model，只读配置。

配置在 ``agent.json`` 的 ``routing`` 段，默认关闭：

    "routing": {
      "enabled": false,
      "tasks": {
        "chat":      {"provider": "", "model": ""},
        "vision":    {"provider": "", "model": ""},
        "reasoning": {"provider": "", "model": ""}
      }
    }

规则：``routing.enabled`` 不是布尔 ``True``、任务没填、或字段为空时，
一律回退到顶层 ``provider`` / ``chat.model``，与旧链路完全一致。
本模块不内置任何厂商常量、模型 ID、host，不联网、不 import Qt。
"""

from __future__ import annotations

TASK_KINDS = ("chat", "vision", "reasoning")
FALLBACK_TASK = "chat"


def routing_config(cfg) -> dict:
    raw = cfg.get("routing") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def routing_enabled(cfg) -> bool:
    return routing_config(cfg).get("enabled") is True


def _task_entry(cfg, task: str) -> dict:
    tasks = routing_config(cfg).get("tasks")
    if not isinstance(tasks, dict):
        return {}
    entry = tasks.get(task)
    return entry if isinstance(entry, dict) else {}


def resolve_route(cfg, task: str = "chat") -> dict:
    """返回 ``{"provider", "model", "routed"}``。

    只有 ``enabled=true`` 且该任务显式填了 provider/model 时才覆盖；
    任一字段为空就沿用旧值。默认关闭时 ``routed`` 恒为 False。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    chat = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    out = {
        "provider": str(cfg.get("provider") or ""),
        "model": str(chat.get("model") or ""),
        "routed": False,
    }
    if not routing_enabled(cfg):
        return out

    kind = task if task in TASK_KINDS else FALLBACK_TASK
    entry = _task_entry(cfg, kind)
    provider = str(entry.get("provider") or "").strip()
    model = str(entry.get("model") or "").strip()
    if not provider and not model:
        return out

    out["provider"] = provider or out["provider"]
    out["model"] = model or out["model"]
    out["routed"] = True
    return out


def task_model(cfg, task: str) -> str:
    """只返回该任务**显式**配置的 model；默认关 / 空值返回空串。

    视觉这类独立配置源（``ai_motion.json``）只需要「换模型」，
    不能因为只填了 provider 就把全局 ``chat.model`` 误当成视觉模型。
    """
    if not routing_enabled(cfg):
        return ""
    kind = task if task in TASK_KINDS else FALLBACK_TASK
    return str(_task_entry(cfg, kind).get("model") or "").strip()
