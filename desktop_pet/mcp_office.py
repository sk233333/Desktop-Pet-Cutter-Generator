#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP / 办公事件桥的适配层：只翻译协议，不持有第二份状态。

- 工具白名单只有只读 + 候选 + 通知；不暴露 commit / 写盘 / exec；
- `propose_candidate` 只回正规化后的候选（`persisted: False`），默认零勾选；
- `notify_bubble` 只调用注入的 `notify` 回调（桌宠侧把它接到
  `arbiter.request("interaction", ...)`），本模块不碰 Qt / arbiter / 磁盘；
- 本模块只读 manifest，且只回白名单字段（basename，不回绝对路径）。
"""

from __future__ import annotations

from pathlib import Path

try:
    from .chat_contract import MOOD_KEYS, clamp
    from .manifest_io import read_manifest
except ImportError:  # 直接以脚本 / 平铺 sys.path 运行时
    from chat_contract import MOOD_KEYS, clamp
    from manifest_io import read_manifest

PROTOCOL = "desktop-pet-mcp/1"
MAX_TEXT = 200
MAX_BUBBLE = 30
TOOL_NAMES = ("get_health", "get_allowed", "get_manifest_item",
              "propose_candidate", "notify_bubble")


def normalize_notify(args, allowed) -> dict | None:
    """把外部通知裁到词表内；文本缺失/非字符串返回 None。"""
    if not isinstance(args, dict):
        return None
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    emos = (allowed or {}).get("emotion") or ["normal"]
    emotion = args.get("emotion")
    if emotion not in emos:
        emotion = "normal"
    hint = args.get("mood_hint")
    if hint not in MOOD_KEYS:
        hint = None
    out = {"text": text.strip()[:MAX_TEXT], "emotion": emotion}
    if hint:
        out["mood_hint"] = hint
    return out


class OfficeHost:
    """桌宠侧的最小 host；所有落点由注入的 `notify` 决定。"""

    def __init__(self, *, allowed=None, sku: str = "", manifest_path=None,
                 stem: str = "", notify=None, health=None):
        self.allowed = allowed if isinstance(allowed, dict) else {
            "emotion": ["normal"], "action": []}
        self.sku = str(sku or "")
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.stem = str(stem or "")
        self._notify = notify
        self._health = dict(health or {})

    def tool_specs(self) -> list[dict]:
        return [
            {"name": "get_health", "description": "桌宠开关与协议状态（只读）",
             "input": {}},
            {"name": "get_allowed", "description": "当前表情/动作词表（只读）",
             "input": {}},
            {"name": "get_manifest_item",
             "description": "单个素材条目的白名单字段（只读）",
             "input": {"stem": "可选，默认当前素材"}},
            {"name": "propose_candidate",
             "description": "递一个候选（不落盘，等人工确认）",
             "input": {"text": "候选文本", "emotion": "可选",
                       "action": "可选", "mood_hint": "可选"}},
            {"name": "notify_bubble",
             "description": "气泡播报 + 可选表情（只走 arbiter）",
             "input": {"text": "必填", "emotion": "可选",
                       "mood_hint": "可选"}},
        ]

    def get_health(self) -> dict:
        out = {"ok": True, "protocol": PROTOCOL, "sku": self.sku}
        out.update(self._health)
        return out

    def get_allowed(self) -> dict:
        return {"emotion": list(self.allowed.get("emotion") or ["normal"]),
                "action": list(self.allowed.get("action") or [])}

    def get_manifest_item(self, stem=None) -> dict | None:
        """只读；只回 basename 与白名单字段，不回路径 / 原始像素。"""
        path = self.manifest_path
        if path is None or not path.is_file():
            return None
        try:
            data = read_manifest(path)
        except Exception:
            return None
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return None
        target = stem if isinstance(stem, str) and stem.strip() else self.stem
        hit = next((it for it in rows
                    if isinstance(it, dict) and it.get("stem") == target), None)
        if hit is None:
            return None
        files = [Path(f).name for f in (hit.get("files") or [])
                 if isinstance(f, str)]
        overlays = hit.get("expr_overlays")
        return {
            "stem": hit.get("stem"),
            "files": files,
            "mouth_level": hit.get("mouth_level"),
            "expr_overlays": sorted(overlays) if isinstance(overlays, dict) else [],
            "has_smile_geometry": isinstance(hit.get("smile_geometry"), dict),
        }

    def propose_candidate(self, args) -> dict:
        """只回候选；`persisted` 恒为 False，不写 manifest、不写盘。"""
        raw = args if isinstance(args, dict) else {}
        candidate = clamp({
            "text": raw.get("text") if isinstance(raw.get("text"), str) else "",
            "emotion": raw.get("emotion"),
            "action": raw.get("action"),
            "mood_hint": raw.get("mood_hint"),
        }, self.allowed)
        candidate["text"] = candidate["text"][:MAX_BUBBLE]
        return {"persisted": False, "requires_user_confirm": True,
                "candidate": candidate}

    def notify_bubble(self, text=None, emotion=None, mood_hint=None) -> dict:
        norm = normalize_notify(
            {"text": text, "emotion": emotion, "mood_hint": mood_hint},
            self.allowed)
        if norm is None:
            return {"delivered": False}
        ok = False
        if self._notify is not None:
            try:
                ok = bool(self._notify(norm["text"], norm["emotion"],
                                       norm.get("mood_hint")))
            except Exception:
                ok = False
        return {"delivered": ok}
