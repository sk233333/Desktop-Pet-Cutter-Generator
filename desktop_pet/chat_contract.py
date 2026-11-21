#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A1 纯逻辑层：人设 prompt、JSON 解析、词表裁剪、配置读取。

零 Qt、零第三方依赖，可被 CI 五包环境直接收集。
本模块**不** import upgrade_core / ui_app / pet_window / chat_worker。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_TEXT = 30          # 气泡文本上限
MAX_RAW_TEXT = 4000    # 解析前先挡住的异常长文本（正常回复远小于它）
DEFAULT_REPLY = {"text": "…", "emotion": "normal", "action": "none"}
# 语义提示只允许封闭覆盖层键；它只改变取图，不改变状态名 / 词表。
MOOD_KEYS = ("joy", "sleepy", "angry", "sad", "shy")

# effect_table.json 里「效果 id → 表情状态」的投影；derive_allowed 只看
# effects.enabled 的 id，不读表本身（避免多一份运行时依赖）。
_EFFECT_STATES = {
    "blink": "blink",
    "slow_blink": "blink",
    "half": "half",
    "wink_r": "wink_r",
    "perk": "surprise",
    "yawn": "surprise",
    "zzz": "half",
}

_DEFAULT_CHAT = {
    "enabled": False,
    "base_url": "",
    "model": "",
    "key_env": "DESKTOP_PET_API_KEY",
    "thinking": False,
    "timeout_ms": 8000,
    "max_tokens": 120,
    "history": 10,
    "json_mode": "object",
    "retry": 0,           # 失败重试次数（默认关）；已吐字不再重试
    "usage_log": False,   # token / 费用估算只打类别，默认关
    "keyring": False,     # 可选系统钥匙串，默认关
    "key_file": "",       # 可选 0600 key 文件，环境变量优先
}


def _default_copy(default) -> dict | None:
    return None if default is None else dict(default)


def build_system_prompt(persona: dict, allowed: dict, memories=None) -> str:
    """按运行时词表拼 system prompt；词表为空时只留 normal / none。"""
    p = persona if isinstance(persona, dict) else {}
    name = str(p.get("name") or "桌宠")
    persona_text = str(p.get("persona") or "")
    style = str(p.get("style") or "一句口语，不超过 30 字")
    emos = [str(x) for x in (allowed or {}).get("emotion") or []]
    acts = [str(x) for x in (allowed or {}).get("action") or []]
    if "normal" not in emos:
        emos.insert(0, "normal")
    emotion_line = "、".join(emos)
    action_line = "、".join(acts) if acts else "（无可用动作，只能填 none）"
    memory_block = ""
    lines = [str(m).strip() for m in (memories or []) if str(m).strip()]
    if lines:
        memory_block = ("\n已知的长期记忆（用户显式要求记住，只作参考，"
                        "不要复述原文）：\n" + "\n".join(f"- {m}" for m in lines)
                        + "\n")
    return (
        f"你是{name}。{persona_text}\n"
        f"说话风格：{style}\n"
        f"{memory_block}"
        "\n"
        "只输出严格 JSON（json），不要 markdown、不要解释、不要跳出角色：\n"
        '{"text":"...","emotion":"...","action":"...",'
        '"mood_hint":"...","remember":"..."}\n'
        "\n"
        f"emotion 只能从以下值里选：{emotion_line}\n"
        f"action 只能从以下值里选：{action_line}，没有动作就填 \"none\"\n"
        f"mood_hint 只能从以下值里选或省略：{'、'.join(MOOD_KEYS)}\n"
        "remember 只在用户明确要求记住时填写，否则省略；不要写入聊天全文。\n"
        "你不会眨眼，眨眼由宠物自己控制；不要试图指挥眨眼。\n"
        "text 超过 30 字自行截断。\n"
    )


def safe_parse(raw: str, default=DEFAULT_REPLY) -> dict | None:
    """严格 JSON 解析；任何不合格输入整体归一到 default。

    default=None 时返回 None（worker 用它区分「乱码」与「合法默认值」）。
    """
    if not isinstance(raw, str) or not raw.strip():
        return _default_copy(default)
    if len(raw) > MAX_RAW_TEXT:
        return _default_copy(default)
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return _default_copy(default)
    if not isinstance(obj, dict):
        return _default_copy(default)
    text = obj.get("text")
    emotion = obj.get("emotion")
    action = obj.get("action")
    if not all(isinstance(v, str) for v in (text, emotion, action)):
        return _default_copy(default)
    out = {"text": text, "emotion": emotion, "action": action}
    hint = obj.get("mood_hint")
    if hint in MOOD_KEYS:
        out["mood_hint"] = hint
    remember = obj.get("remember")
    if isinstance(remember, str) and remember.strip():
        out["remember"] = remember.strip()[:200]
    return out


_TEXT_KEY = re.compile(r'"text"\s*:\s*"')


def partial_text(raw: str) -> str:
    """从流式 JSON 里取出已到达的 `text` 值，供气泡逐字显示。

    模型被要求输出整个 JSON，chunk 里带的是 `{"text":"你` 这类片段；
    直接把原文塞进气泡会先闪一串括号和字段名。
    """
    if not isinstance(raw, str):
        return ""
    m = _TEXT_KEY.search(raw)
    if not m:
        s = raw.lstrip()
        # 还在 JSON 头部（还没到 text 字段）→ 返回空，气泡显示省略号；
        # json_mode=none / 模型不按 JSON 吐纯文本 → 回退为可见原文尾部。
        if not s or s.startswith("{"):
            return ""
        return s[-MAX_TEXT:]
    start = m.end()
    i, esc = start, False
    while i < len(raw):
        ch = raw[i]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            break
        i += 1
    frag = raw[start:i]
    try:
        return json.loads('"' + frag + '"')
    except ValueError:
        return (frag.replace("\\n", "\n").replace("\\t", "\t")
                .replace('\\"', '"').replace("\\\\", "\\"))


def clamp(reply: dict, allowed: dict) -> dict:
    """把已解析的回复裁到词表内：越界 emotion → normal，action → none。"""
    r = reply if isinstance(reply, dict) else {}
    emos = (allowed or {}).get("emotion") or []
    acts = (allowed or {}).get("action") or []
    text = r.get("text")
    text = text if isinstance(text, str) else ""
    emotion = r.get("emotion")
    action = r.get("action")
    if emotion not in emos:
        emotion = "normal"
    if action not in acts:
        action = "none"
    out = {"text": text[:MAX_TEXT], "emotion": emotion, "action": action}
    hint = r.get("mood_hint")
    if hint in MOOD_KEYS:
        out["mood_hint"] = hint
    remember = r.get("remember")
    if isinstance(remember, str) and remember.strip():
        out["remember"] = remember.strip()[:200]
    return out


def _enabled_states(enabled: set[str]) -> set[str]:
    out = set()
    for name in enabled:
        if name in _EFFECT_STATES:
            out.add(_EFFECT_STATES[name])
        else:
            out.add(name)
    return out


def derive_allowed(sku: str, expr_states, action_registry, effects_cfg) -> dict:
    """从运行时真实产物反推词表。

    - emotion 只取 expr_states（`ExprBank.STATES` / `expr_state.STATES`），
      **不看 apply() 的 smile→half 别名**，并排除 blink / smile；
    - 按逐 SKU `effects.enabled` 裁剪；缺省（None）表示不裁；
    - action 只取 ActionBridge.registry 的 id，手填配置只作裁剪上限；
    - sku 只用于调用侧日志，不改变结果。
    """
    del sku  # 契约保留参数；词表不随 SKU 名变化，只随真实产物变化
    states = [str(s) for s in (expr_states or ())]
    enabled = None
    if isinstance(effects_cfg, dict):
        raw = effects_cfg.get("enabled")
        if isinstance(raw, (list, tuple)):
            enabled = {str(x) for x in raw}
    keep = _enabled_states(enabled) if enabled is not None else None

    emos = ["normal"]
    for state in states:
        # B-2 R2-B：smile 已接线（PetWindow 独立覆盖层），进词表
        if state in ("blink", "normal"):
            continue
        if keep is not None and state not in keep:
            continue
        if state not in emos:
            emos.append(state)

    reg = action_registry
    if hasattr(reg, "registry"):
        reg = reg.registry
    if isinstance(reg, dict):
        actions = [str(k) for k in reg.keys() if str(k)]
    elif isinstance(reg, (list, tuple, set)):
        actions = [str(k) for k in reg if str(k)]
    else:
        actions = []
    return {"emotion": emos, "action": actions}


def chat_enabled(cfg: dict) -> bool:
    """配置总开关；缺省 / 非法一律按关闭处理。"""
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    return chat.get("enabled") is True if isinstance(chat, dict) else False


def retry_attempts(cfg: dict, limit: int = 2) -> int:
    """``chat.retry`` → 总尝试次数；默认 0 = 只跑一次。

    上限默认 2 次重试（共 3 次尝试）。「已吐字是否重试」由调用侧判断，
    这里只给次数，不改变流式语义。
    """
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    chat = chat if isinstance(chat, dict) else {}
    try:
        retry = int(chat.get("retry") or 0)
    except (TypeError, ValueError):
        retry = 0
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = 2
    return 1 + max(0, min(retry, cap))


def load_agent_config(path=None) -> dict:
    """读 `desktop_pet/agent.json`；缺失或损坏时返回「关闭」配置，不抛异常。"""
    p = Path(path) if path else Path(__file__).with_name("agent.json")
    cfg = {"chat": dict(_DEFAULT_CHAT), "provider": "", "profiles": {},
           "skus": {}, "agent": {}, "voice": {}, "cloud": {},
           "multi_agent": {}, "weights": {}, "routing": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as exc:
        print(f"[chat] 配置读取失败，按关闭处理：{exc}", file=sys.stderr)
        return cfg
    if not isinstance(data, dict):
        print("[chat] agent.json 顶层必须是对象，按关闭处理", file=sys.stderr)
        return cfg
    chat = data.get("chat")
    if isinstance(chat, dict):
        cfg["chat"].update(chat)
    for key in ("provider",):
        if isinstance(data.get(key), str):
            cfg[key] = data[key]
    for key in ("profiles", "skus", "agent", "voice", "cloud",
                "multi_agent", "weights", "routing"):
        if isinstance(data.get(key), dict):
            cfg[key] = data[key]
    return cfg
