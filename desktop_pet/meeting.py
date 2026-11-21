#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.3.2 配置期多角色评审（开会）：顺序讨论、同意计数、硬停。

边界：
- 默认关；只有用户层 ``~/.config/desktop_pet/meeting.json`` 的
  ``enabled`` 为布尔 ``True`` 且至少两个角色时才运行；
- 只在配置期跑，依次问角色，中间意见只在本进程内存里，不写盘、
  不进运行时、不碰 ``arbiter`` / manifest；
- 全部 ``agree`` 只返回 ``ready_for_human_review``，是否保存仍由人点确认；
  任一 ``reject`` / 超时 / 轮次到顶都返回 ``conflict``；
- 每角色可绑自己的 profile / chat 覆盖，实现「一桌宠一个 provider」；
  示例配置不写具体模型 ID / host，key 只给 ``key_env`` 占位。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from .model_router import TASK_KINDS
    from .secrets_store import resolve_key
    from .usage_meter import estimate_tokens
except ImportError:  # 平铺 sys.path
    from model_router import TASK_KINDS
    from secrets_store import resolve_key
    from usage_meter import estimate_tokens

MAX_ROLES = 5
MAX_ROUNDS = 3
DEFAULT_TIMEOUT_MS = 60000
PLAN_LIMIT = 1600
REASON_LIMIT = 200

STATUS_READY = "ready_for_human_review"
STATUS_CONFLICT = "conflict"
STATUS_DISABLED = "disabled"


class MeetingError(RuntimeError):
    """配置或请求前的硬失败；消息不含 key / URL / 路径。"""


@dataclass
class TokenTrace:
    """一次评审的估算用量；只用于显示，不是计费真值。"""

    ttft_ms: int = 0
    first5s: int = 0
    total: int = 0

    def merge(self, other) -> None:
        if not isinstance(other, dict):
            return
        ttft = _as_int(other.get("ttft_ms"))
        if ttft > 0 and (self.ttft_ms == 0 or ttft < self.ttft_ms):
            self.ttft_ms = ttft
        self.first5s += _as_int(other.get("first5s"))
        self.total += _as_int(other.get("total"))

    def as_dict(self) -> dict:
        return {"ttft_ms": self.ttft_ms, "first5s": self.first5s,
                "total": self.total}


def _as_int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def config_path() -> Path:
    env = os.environ.get("DESKTOP_PET_MEETING_CONFIG", "")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".config" / "desktop_pet" / "meeting.json"


def _normalize_role(raw, index: int, *, devil=False) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = _as_text(raw.get("name")) or f"角色{index + 1}"
    chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
    task = _as_text(raw.get("task")) or "chat"
    if task not in TASK_KINDS:
        task = "chat"
    return {
        "name": name[:40],
        "sku": _as_text(raw.get("sku"))[:80],
        "profile": _as_text(raw.get("profile"))[:80],
        "task": task,
        "chat": dict(chat),
        "devil": bool(raw.get("devil") or devil),
    }


def normalize_config(raw) -> dict:
    """把任意输入收成稳定结构；默认关、轮次与超时都有上下界。"""
    data = raw if isinstance(raw, dict) else {}
    roles = []
    for i, item in enumerate(data.get("roles") or []):
        role = _normalize_role(item, i)
        if role is not None and not role["devil"]:
            roles.append(role)
    roles = roles[:MAX_ROLES]

    devil = _normalize_role(data.get("devil"), len(roles), devil=True)
    if devil is None:
        devil = _normalize_role({"name": "反方", "devil": True},
                            len(roles), devil=True)

    rounds = _as_int(data.get("max_rounds"), 2)
    rounds = max(1, min(rounds, MAX_ROUNDS))
    timeout_ms = _as_int(data.get("timeout_ms"), DEFAULT_TIMEOUT_MS)
    timeout_ms = max(1000, min(timeout_ms, 3600000))
    return {
        "enabled": data.get("enabled") is True,
        "max_rounds": rounds,
        "timeout_ms": timeout_ms,
        "roles": roles,
        "devil": devil,
    }


def load_meeting_config(path=None) -> dict:
    """读用户层配置；文件缺失 / 坏 JSON / 非对象一律回退为默认关。"""
    target = Path(path) if path else config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return normalize_config(raw)


def meeting_ready(cfg) -> bool:
    conf = normalize_config(cfg)
    return conf["enabled"] is True and len(conf["roles"]) >= 2


def role_config(base_cfg, role) -> dict:
    """生成该角色的一次性 cfg；只改内存副本，不翻转全局 chat.enabled。"""
    base = base_cfg if isinstance(base_cfg, dict) else {}
    chat = dict(base.get("chat")) if isinstance(base.get("chat"), dict) else {}
    override = role.get("chat") if isinstance(role.get("chat"), dict) else {}
    for key in ("base_url", "model", "key_env"):
        val = _as_text(override.get(key))
        if val:
            chat[key] = val
    for key in ("timeout_ms", "retry"):
        if key in override:
            val = _as_int(override.get(key), -1)
            if val >= 0:
                chat[key] = val
    json_mode = _as_text(override.get("json_mode"))
    if json_mode:
        chat["json_mode"] = json_mode
    chat["enabled"] = True           # 仅本次请求副本；落到硬盘的值不变
    cfg = dict(base)
    cfg["chat"] = chat
    if role.get("profile"):
        cfg["provider"] = role["profile"]
    return cfg


def ensure_role_ready(cfg) -> None:
    """请求前自查；缺 base_url / model / key 时在联网前失败。"""
    chat = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    if not _as_text(chat.get("base_url")) or not _as_text(chat.get("model")):
        raise MeetingError("角色缺少 base_url 或 model")
    profiles = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    profile = profiles.get(cfg.get("provider") or "")
    profile = profile if isinstance(profile, dict) else {}
    auth = _as_text(profile.get("auth")) or "bearer"
    if auth == "bearer" and not resolve_key(cfg):
        raise MeetingError("未找到 API key")


def build_prompt(plan: str, opinions: list[dict], role: dict,
                 round_i: int, max_rounds: int) -> str:
    """把方案与前面意见收成一段；只含角色名 / 结论 / 理由。"""
    plan = plan if isinstance(plan, str) else ""
    lines = [
        f"你是评审角色「{role.get('name', '')}」，正在参加第 "
        f"{round_i}/{max_rounds} 轮配置期评审。",
        "请只返回一行 JSON：{\"verdict\":\"agree\" 或 \"reject\","
        " \"reason\":\"不超过 80 字\"}。",
        "必须检查：是否引入默认联网、是否新增状态源、是否绕过人工确认、"
        "是否写 manifest 或执行办公动作。",
        "",
        "方案：",
        plan[:PLAN_LIMIT],
    ]
    if opinions:
        lines.append("")
        lines.append("前面意见：")
        for item in opinions[-8:]:
            lines.append(
                f"- {item.get('role', '')}（第 {item.get('round', '')} 轮）"
                f"：{item.get('verdict', '')} · {item.get('reason', '')}")
    return "\n".join(lines)


def _verdict_of(reply) -> tuple[str, str]:
    if not isinstance(reply, dict):
        return "reject", "invalid_reply"
    verdict = reply.get("verdict")
    if verdict not in ("agree", "reject"):
        return "reject", "invalid_reply"
    reason = _as_text(reply.get("reason"))[:REASON_LIMIT]
    if verdict == "reject" and not reason:
        reason = "rejected"
    return verdict, reason


def make_ask(base_cfg, *, stream=None):
    """构造 ask(role, prompt)；真实网络只在被调用时发生。"""
    if stream is None:
        try:
            from .chat_provider import stream_chat
        except ImportError:
            from chat_provider import stream_chat
        stream = stream_chat
    sys_msg = ("你是配置期评审员。只输出一行 JSON，不要解释，不要调用工具。"
               "宁可 reject，也不要放过默认联网、状态源、绕过人工确认或写盘。")

    def ask(role: dict, prompt: str) -> dict:
        cfg = role_config(base_cfg, role)
        ensure_role_ready(cfg)
        started = time.monotonic()
        ttft_ms = 0
        first5s = 0
        total = 0
        chunks: list[str] = []
        for token in stream(cfg, sys_msg,
                            [{"role": "user", "content": prompt}],
                            sku=str(role.get("sku") or ""),
                            task=str(role.get("task") or "chat")):
            elapsed = time.monotonic() - started
            if not ttft_ms:
                ttft_ms = max(1, int(elapsed * 1000))
            size = estimate_tokens(token)
            total += size
            if elapsed <= 5.0:
                first5s += size
            chunks.append(token if isinstance(token, str) else "")
        verdict, reason = _verdict_of(_loads("".join(chunks)))
        return {"verdict": verdict, "reason": reason,
                "trace": {"ttft_ms": ttft_ms, "first5s": first5s,
                          "total": total}}

    return ask


def _loads(raw: str):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def discuss(base_cfg, plan: str, *, cfg=None, ask=None,
            clock=time.monotonic) -> dict:
    """顺序问角色；只返回状态与意见，不写盘、不产生 ChangeSet。"""
    conf = normalize_config(cfg if cfg is not None else load_meeting_config())
    trace = TokenTrace()
    if not meeting_ready(conf):
        return {"status": STATUS_DISABLED, "reason": "meeting_disabled",
                "opinions": [], "trace": trace.as_dict(), "roles": []}

    roles = list(conf["roles"]) + [conf["devil"]]
    ask = ask or make_ask(base_cfg)
    timeout = conf["timeout_ms"] / 1000.0
    started = clock()
    opinions: list[dict] = []

    for round_i in range(1, conf["max_rounds"] + 1):
        for role in roles:
            if clock() - started > timeout:
                return _result(STATUS_CONFLICT, "timeout", opinions, trace, roles)
            prompt = build_prompt(plan, opinions, role, round_i,
                                  conf["max_rounds"])
            try:
                reply = ask(role, prompt)
            except MeetingError as exc:
                return _result(STATUS_CONFLICT, str(exc), opinions, trace, roles)
            except Exception:
                return _result(STATUS_CONFLICT, "ask_failed", opinions, trace, roles)
            if isinstance(reply, dict):
                trace.merge(reply.get("trace"))
            verdict, reason = _verdict_of(reply)
            opinions.append({"role": role.get("name", ""),
                             "round": round_i, "verdict": verdict,
                             "reason": reason})
            if verdict != "agree":
                return _result(STATUS_CONFLICT, "rejected", opinions, trace, roles)
        if len(opinions) >= len(roles) * round_i:
            return _result(STATUS_READY, "all_agree", opinions, trace, roles)

    return _result(STATUS_CONFLICT, "max_rounds", opinions, trace, roles)


def _result(status: str, reason: str, opinions: list[dict],
            trace: TokenTrace, roles: list[dict]) -> dict:
    return {"status": status, "reason": reason,
            "opinions": list(opinions), "trace": trace.as_dict(),
            "roles": [r.get("name", "") for r in roles]}
