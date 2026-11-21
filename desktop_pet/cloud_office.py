#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部办公事件适配层：默认关闭，只翻译事件，不发起网络连接。

配置默认全关：``enabled`` / ``consent`` 都必须是布尔 ``True``，``base_url``
必须以 ``https://`` 开头，``key_env`` 指向的环境变量非空，才认为就绪。
就绪后也只调用调用方注入的 ``poster(payload)``；本模块不执行任何办公动作，
不写盘、不加载 Qt，也不回传确认结果。日志只接收脱敏后的类别 / 状态文本。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass

DEFAULT_MAX_TEXT = 400
DEFAULT_TIMEOUT_MS = 8000

_URL_RE = re.compile(r"https?://[^\s]+")
_PATH_RE = re.compile(r"(?:~|/home)/[^\s]*")
_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]+")


@dataclass(frozen=True)
class CloudConfig:
    enabled: bool = False
    consent: bool = False
    base_url: str = ""
    key_env: str = ""
    max_text: int = DEFAULT_MAX_TEXT
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @classmethod
    def from_dict(cls, raw) -> "CloudConfig":
        if isinstance(raw, cls):
            return raw
        data = raw if isinstance(raw, dict) else {}

        def _flag(key: str) -> bool:
            return data.get(key) is True

        def _text(key: str) -> str:
            val = data.get(key)
            return val if isinstance(val, str) else ""

        max_text = data.get("max_text")
        if not isinstance(max_text, int) or isinstance(max_text, bool) \
                or max_text <= 0:
            max_text = DEFAULT_MAX_TEXT
        timeout_ms = data.get("timeout_ms")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) \
                or timeout_ms <= 0:
            timeout_ms = DEFAULT_TIMEOUT_MS
        return cls(enabled=_flag("enabled"), consent=_flag("consent"),
                   base_url=_text("base_url"), key_env=_text("key_env"),
                   max_text=max_text, timeout_ms=timeout_ms)


def _as_config(cfg) -> CloudConfig:
    if isinstance(cfg, CloudConfig):
        return cfg
    return CloudConfig.from_dict(cfg)


def _env_key(cfg: CloudConfig) -> str:
    if not cfg.key_env:
        return ""
    return os.environ.get(cfg.key_env, "") or ""


def cloud_ready(cfg) -> bool:
    """仅四项都满足才算就绪；任何一项缺失都返回 False。"""
    conf = _as_config(cfg)
    return (
        conf.enabled is True
        and conf.consent is True
        and conf.base_url.startswith("https://")
        and bool(_env_key(conf))
    )


def redact(text) -> str:
    """去掉 URL、用户目录路径与 ``sk-`` 形式的 key。"""
    if not isinstance(text, str):
        return ""
    out = _URL_RE.sub("[redacted]", text)
    out = _PATH_RE.sub("[redacted]", out)
    out = _KEY_RE.sub("[redacted]", out)
    return out


def _log(logger, message: str) -> None:
    if logger is None:
        return
    try:
        logger(redact(message))
    except Exception:
        pass


def _payload(cfg: CloudConfig, event) -> dict:
    raw = event if isinstance(event, dict) else {}
    text = raw.get("text")
    text = text[:cfg.max_text] if isinstance(text, str) else ""
    kind = raw.get("kind")
    kind = kind if isinstance(kind, str) else ""
    emotion = raw.get("emotion")
    emotion = emotion if isinstance(emotion, str) else "normal"
    return {"kind": kind, "text": text, "emotion": emotion}


def send_event(cfg, event, *, poster=None, logger=None) -> dict:
    """未就绪时不调用 poster；就绪时只把白名单载荷交给注入的 poster。"""
    conf = _as_config(cfg)
    if conf.enabled is not True:
        _log(logger, "cloud_event disabled")
        return {"delivered": False, "reason": "disabled"}
    if not cloud_ready(conf):
        _log(logger, "cloud_event not_ready")
        return {"delivered": False, "reason": "not_ready"}

    payload = _payload(conf, event)
    try:
        poster(payload)
    except Exception:
        _log(logger, "cloud_event post_failed")
        return {"delivered": False, "reason": "post_failed"}
    _log(logger, "cloud_event ok")
    return {"delivered": True, "reason": "ok"}


def http_poster(cfg, *, opener=None):
    """返回一个「被调用时才发请求」的 poster；默认关时由 send_event 挡住。

    只在 ``cloud_ready`` 为真且调用方主动调用时才会走到网络；key 取自
    ``key_env`` 指向的环境变量，不写日志、不回传响应正文。
    """
    conf = _as_config(cfg)
    url = conf.base_url
    key = _env_key(conf)
    timeout = max(0.1, float(conf.timeout_ms or DEFAULT_TIMEOUT_MS) / 1000.0)
    open_url = opener or urllib.request.urlopen

    def post(payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with open_url(req, timeout=timeout) as resp:
            resp.read()

    return post
