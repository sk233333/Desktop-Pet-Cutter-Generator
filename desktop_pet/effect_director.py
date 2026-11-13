"""待机效果编排：用 mood 权重把多个通道组合起来，而不是加更多贴片。

    6 表情 × 若干视线偏移 × 气泡符号 = 上百种观感，但只多这一个对象。

约束（交接文档 5.7）：
  * 同一效果不连播；用户刚交互完 3 秒内不打断
  * mood 切换带冷却（20~40 秒），不在 calm / sleepy 之间来回抖
  * idle 间隔不缩短，变化只来自组合
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path


class EffectDirector:
    def __init__(self, table: dict, rng: random.Random | None = None,
                 cfg: dict | None = None):
        self.moods = dict(table.get("moods") or {})
        self.effects = dict(table.get("effects") or {})
        self.rng = rng or random.Random()
        cfg = dict(cfg or {})
        # 逐素材覆盖（manifest 的 effects 段）：只开列出的效果 / 指定起始 mood
        allowed = cfg.get("enabled")
        self.allowed = set(allowed) if isinstance(allowed, (list, tuple)) else None
        mood = cfg.get("mood")
        self.mood = mood if mood in self.moods else next(iter(self.moods), None)
        now = time.monotonic()
        self._next_at = 0.0
        self._last = ""
        self._touched = now - 99.0
        self._mood_until = now + 25.0
        self.gaze = None          # (gx, gy, until, since)

    @classmethod
    def load(cls, path=None, cfg: dict | None = None):
        p = Path(path) if path else Path(__file__).with_name("effect_table.json")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(data, cfg=cfg) if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # ---------------- 外部信号 ----------------
    def touch(self, now: float | None = None):
        """用户摸了/拖了桌宠：短时间内不主动打断。"""
        self._touched = now if now is not None else time.monotonic()

    def gaze_at(self, now: float):
        """当前效果要求的视线偏移（±1 的比例），过期返回 None。"""
        g = self.gaze
        return (g[0], g[1]) if g and now < g[2] and now >= g[3] else None

    # ---------------- 主循环 ----------------
    def tick(self, now: float | None = None) -> dict | None:
        now = now if now is not None else time.monotonic()
        if not self.mood or now < self._next_at:
            return None
        cfg = self.moods.get(self.mood) or {}
        lo, hi = cfg.get("idle_ms", [3500, 8000])
        self._next_at = now + self.rng.uniform(lo, hi) / 1000.0

        if now - self._touched < 3.0:      # 刚交互完，安静一会儿
            return None
        if now > self._mood_until:
            self._rotate_mood(now)
            cfg = self.moods.get(self.mood) or {}

        picked = self._pick(cfg)
        if picked is None:
            return None
        name, spec = picked
        if name == self._last and len(self.effects) > 1:   # 同一效果不连播
            again = self._pick(cfg)
            if again is None or again[0] == self._last:
                return None
            name, spec = again
        self._last = name
        hold = float(spec.get("hold_ms", 1200))
        g = spec.get("gaze")
        self.gaze = ((float(g.get("gx", 0.0)), float(g.get("gy", 0.0)),
                      now + hold / 1000.0 + 0.3, now)) if g else None
        return {"id": name, "expr": spec.get("expr", "normal"),
                "hold_ms": hold, "bubble": spec.get("bubble", "")}

    # ---------------- 内部 ----------------
    def _pick(self, cfg: dict):
        weights = {k: float(v) for k, v in (cfg.get("weights") or {}).items()
                   if k in self.effects and float(v) > 0
                   and (self.allowed is None or k in self.allowed)}
        if not weights:
            return None
        total = sum(weights.values())
        r = self.rng.random() * total
        for name, w in weights.items():
            r -= w
            if r <= 0:
                return name, self.effects[name]
        name = next(iter(weights))
        return name, self.effects[name]

    def _rotate_mood(self, now: float):
        # 刚被摸过就先不换 mood：用户正在看，别突然从 calm 跳到 sleepy
        if now - self._touched < 3.0:
            self._mood_until = now + 3.0
            return
        others = [m for m in self.moods if m != self.mood]
        if others:
            self.mood = self.rng.choice(others)
        self._mood_until = now + self.rng.uniform(20.0, 40.0)
