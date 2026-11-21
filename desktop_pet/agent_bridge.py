#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立 Agent 的窄协议适配：一次请求 = 一行 JSON 进、一行 JSON 出。

Agent 是**触发源**，不是状态源：输出仍由 PetWindow 交给
``arbiter.request("interaction", ...)``；本模块不 import Qt、不碰 arbiter、
不写盘。协议字段：``text`` / ``emotion`` / ``action`` / ``mood_hint`` /
``remember``，全部可缺省。命令与超时来自 ``agent.json`` 的 ``agent`` 段，
默认关闭，关闭时零子进程。
"""

from __future__ import annotations

import json
import shlex
import subprocess

MOOD_KEYS = ("joy", "sleepy", "angry", "sad", "shy")
MAX_STDOUT = 64 * 1024


class AgentError(RuntimeError):
    """Agent 不可用 / 超时 / 非 JSON / 结构不对；调用侧静默回 idle。"""


def agent_config(cfg) -> dict:
    raw = cfg.get("agent") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def agent_enabled(cfg) -> bool:
    a = agent_config(cfg)
    return a.get("enabled") is True and bool(str(a.get("command") or "").strip())


def _command(a: dict) -> list[str]:
    cmd = a.get("command")
    if isinstance(cmd, str):
        parts = shlex.split(cmd)
    elif isinstance(cmd, (list, tuple)):
        parts = [str(x) for x in cmd]
    else:
        parts = []
    if not parts:
        raise AgentError("agent.command 为空")
    return parts


def _default_runner(cmd: list[str], stdin: str, timeout: float) -> str:
    proc = subprocess.run(cmd, input=stdin.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=timeout, check=False)
    return proc.stdout.decode("utf-8", "replace")[:MAX_STDOUT]


def _parse_reply(raw: str) -> dict:
    line = next((ln for ln in (raw or "").splitlines() if ln.strip()), "")
    try:
        obj = json.loads(line)
    except ValueError as exc:
        raise AgentError("Agent 输出不是 JSON") from exc
    if not isinstance(obj, dict):
        raise AgentError("Agent 输出顶层不是对象")
    out: dict = {}
    for key in ("text", "emotion", "action"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val[:200] if key != "text" else val[:4000]
    hint = obj.get("mood_hint")
    if hint in MOOD_KEYS:
        out["mood_hint"] = hint
    remember = obj.get("remember")
    if isinstance(remember, str) and remember.strip():
        out["remember"] = remember.strip()[:200]
    # L2 循环的中间步可能只回候选 / 报告；仍是候选源，不直接写盘。
    report = obj.get("report")
    if isinstance(report, (dict, list)):
        out["report"] = report
    change_set = obj.get("change_set")
    if isinstance(change_set, dict):
        out["change_set"] = change_set
    if not out.get("text") and not out.get("report") and not out.get("change_set"):
        raise AgentError("Agent 输出没有可用字段")
    return out


def respond(cfg, payload: dict, *, runner=None, timeout_ms=None) -> dict:
    """发一次 JSONL 请求并解析回复；失败一律抛 AgentError。"""
    a = agent_config(cfg)
    if not agent_enabled(cfg):
        raise AgentError("agent 未启用")
    raw_timeout = timeout_ms if timeout_ms is not None else a.get("timeout_ms")
    try:
        timeout = max(0.1, float(raw_timeout or 8000) / 1000.0)
    except (TypeError, ValueError):
        timeout = 8.0
    stdin = json.dumps(payload, ensure_ascii=False) + "\n"
    run = runner or _default_runner
    try:
        raw = run(_command(a), stdin, timeout)
    except AgentError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        raise AgentError("Agent 进程失败或超时") from exc
    return _parse_reply(raw)
