#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 SKU 的长期记忆：用户层、显式触发、原子写、不喂词表。

落点 ``~/.config/desktop_pet/memory/<sku>.json``；不走
``upgrade_core.commit_changes()``，也不改 manifest / schema。
只有用户说出显式触发词（「记住…」）才写；读取失败按空记忆处理，
不把异常带进对话链路。本模块零 Qt、零第三方依赖。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

TRIGGERS = ("请记住", "帮我记住", "记住", "记一下")
SKU_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_ENTRIES = 50
MAX_TEXT = 200
WARN_DEDUP: set[str] = set()


def default_root() -> Path:
    return Path.home() / ".config" / "desktop_pet" / "memory"


def valid_sku(sku) -> bool:
    return isinstance(sku, str) and bool(SKU_RE.fullmatch(sku))


def memory_path(sku, root=None) -> Path | None:
    """sku 不合法时返回 None（调用侧静默跳过，不写任何文件）。"""
    if not valid_sku(sku):
        return None
    return Path(root) / f"{sku}.json" if root else default_root() / f"{sku}.json"


def _warn(key: str, msg: str) -> None:
    """同一 sku 只提醒一次，避免每轮对话刷屏。"""
    if key in WARN_DEDUP:
        return
    WARN_DEDUP.add(key)
    print(f"[memory] {msg}", file=sys.stderr)


def load_memory(sku, root=None) -> list[dict]:
    """读结构化条目；文件缺失 / 损坏 / 结构不对一律返回空列表。"""
    p = memory_path(sku, root)
    if p is None:
        _warn(f"sku:{sku}", f"SKU 名不合法，跳过长期记忆：{sku!r}")
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        _warn(str(p), f"记忆读取失败，按空处理：{exc}")
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[dict] = []
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            out.append({"text": item["text"], "at": str(item.get("at") or "")})
    return out


def extract_trigger(text) -> str | None:
    """只有「记住…」这类显式触发才返回待记内容，其余一律 None。"""
    s = (text or "").strip() if isinstance(text, str) else ""
    for trigger in TRIGGERS:
        if s.startswith(trigger):
            rest = s[len(trigger):].lstrip("：:，, 。").strip()
            return rest[:MAX_TEXT] or None
    return None


def append_memory(sku, text, root=None, now=None) -> dict | None:
    """追加一条记忆；重复内容不写。成功返回条目，失败 / 跳过返回 None。"""
    text = (text or "").strip()[:MAX_TEXT] if isinstance(text, str) else ""
    if not text:
        return None
    p = memory_path(sku, root)
    if p is None:
        _warn(f"sku:{sku}", f"SKU 名不合法，跳过长期记忆：{sku!r}")
        return None
    entries = load_memory(sku, root)
    if any(e.get("text") == text for e in entries):
        return None
    at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    entry = {"text": text, "at": at}
    entries.append(entry)
    entries = entries[-MAX_ENTRIES:]
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"sku": sku, "entries": entries},
                                  ensure_ascii=False, indent=2),
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError as exc:
        _warn(f"write:{p}", f"记忆写盘失败：{exc}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    return entry


def memory_lines(entries, limit: int = 8) -> list[str]:
    """给 system prompt 的结构化短句；不存也不回放聊天全文。"""
    out = []
    for e in (entries or [])[-max(0, int(limit)):]:
        text = e.get("text") if isinstance(e, dict) else None
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out
