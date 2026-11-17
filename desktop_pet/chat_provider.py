#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A1 网络适配层：标准库 urllib + SSE，零第三方依赖。

厂商差异全部收在 `agent.json` 的 profiles 里：
`surface` / `json_mode` / `stream`(delta|cumulative|none) / `auth`。
本模块不 import Qt，也不做重试；重试与降级由 chat_worker 统一处理。
"""

from __future__ import annotations

import json
import os
import urllib.request


def _endpoint(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _content_of(obj: dict) -> str:
    """兼容 OpenAI 的 delta 与部分网关的 message / text 字段。"""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    ch = choices[0] if isinstance(choices[0], dict) else {}
    for holder in (ch.get("delta"), ch.get("message")):
        if isinstance(holder, dict):
            piece = holder.get("content")
            if isinstance(piece, str):
                return piece
            if isinstance(piece, list):        # 少数网关把 content 拆成段
                return "".join(
                    str(p.get("text", "")) for p in piece if isinstance(p, dict))
    piece = ch.get("text")
    return piece if isinstance(piece, str) else ""


def _iter_sse(resp):
    for raw in resp:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue                       # 单行乱码跳过，不让整段失败
        if isinstance(obj, dict):
            yield obj


def stream_chat(cfg: dict, sys_msg: str, messages: list[dict]):
    """yield 增量文本 token；异常向上抛给 worker 统一降级。

    - `chat.enabled != true` 时直接抛错，保证关闭状态零网络；
    - `json_mode == "object"`：同时给 `response_format` 与 prompt 里的 json 字样；
    - 超时取 `chat.timeout_ms`；不重试。
    """
    chat = cfg.get("chat") if isinstance(cfg, dict) else None
    chat = chat if isinstance(chat, dict) else {}
    if not chat.get("enabled"):
        raise RuntimeError("chat.enabled=false，不发起网络请求")
    base_url = str(chat.get("base_url") or "").strip()
    model = str(chat.get("model") or "").strip()
    if not base_url or not model:
        raise RuntimeError("agent.json 缺少 base_url 或 model")

    profiles = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    profile = profiles.get(cfg.get("provider") or "") or {}
    profile = profile if isinstance(profile, dict) else {}
    surface = str(profile.get("surface") or "openai_chat")
    if surface != "openai_chat":
        raise ValueError(f"暂不支持的 provider surface：{surface}")

    key_env = str(chat.get("key_env") or "DESKTOP_PET_API_KEY")
    key = os.environ.get(key_env, "")
    auth = str(profile.get("auth") or "bearer")
    if auth == "bearer" and not key:
        raise RuntimeError(f"环境变量 {key_env} 未设置")

    stream_mode = str(profile.get("stream") or "delta")
    json_mode = str(profile.get("json_mode") or chat.get("json_mode") or "none")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_msg}] + list(messages),
        "stream": stream_mode != "none",
    }
    max_tokens = chat.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        body["max_tokens"] = max_tokens
    if json_mode == "object":
        # 有些服务端只认 response_format、有些必须 prompt 同时出现 json；
        # prompt 侧由 build_system_prompt 保证，这里补协议侧。
        body["response_format"] = {"type": "json_object"}
    # thinking:false 是配置期开关（Q2），表示不请求推理内容；不向通用
    # openai_chat 面强塞厂商私有字段，换模型只改配置/扩展 profile。

    headers = {"Content-Type": "application/json"}
    if stream_mode != "none":
        headers["Accept"] = "text/event-stream"
    else:
        headers["Accept"] = "application/json"
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(
        _endpoint(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    timeout = max(0.1, float(chat.get("timeout_ms") or 8000) / 1000.0)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if stream_mode == "none":
            data = json.loads(resp.read().decode("utf-8", "replace"))
            piece = _content_of(data) if isinstance(data, dict) else ""
            if piece:
                yield piece
            return

        acc = ""
        for obj in _iter_sse(resp):
            piece = _content_of(obj)
            if not piece:
                continue
            if stream_mode == "cumulative":
                full = piece
                if full == acc:
                    continue
                piece = full[len(acc):] if full.startswith(acc) else full
                acc = full
            yield piece
