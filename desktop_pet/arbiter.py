"""优先级仲裁器：timeline(3) > interaction(2) > idle(1)。

纯 Python、零第三方依赖；时钟可注入（`clock` 或显式 `now_ms`），便于测试。
规则见《收敛补丁清单》第 8 节与《裁定回执》：

- 低优先级不得打断高优先级；同优先级不抢占（防 idle 自己换来换去）
- 每次占用带 `until`，`tick()` 释放过期占用后回落 DEFAULT
- `blink` 通道由时间轴独占，其它来源写入一律拒绝
- 时间轴占用期间 `expr` 强制覆盖为 'half' / 'blink'（防「眨了一半又睁开」）
- `gaze` 可与 blink 并行；`blink_zero_gaze` 默认 False（保持仓库现状，不归零）
- `touch()` 后 `interaction_guard_ms` 内 idle 全拒，且该 guard 同时抑制 mood 切换

★ 半闭是否启用由 timeline item 的 `half` 决定（默认 false），仲裁器只负责接收
  调用方算好的 phase；`timeline_phase()` 是本模块提供的可选辅助。
"""

from __future__ import annotations

import time

PRIORITY = {"timeline": 3, "interaction": 2, "idle": 1}
CHANNELS = ("blink", "expr", "gaze", "body", "bubble")
DEFAULT = {"blink": None, "expr": "normal", "gaze": (0.0, 0.0),
           "body": None, "bubble": None}

# 只有时间轴能写的通道
_TIMELINE_ONLY = {"blink"}


def _mono_ms() -> float:
    """单调时钟（毫秒），不受系统时间跳变影响。"""
    return time.monotonic() * 1000.0


def timeline_phase(now_ms: float, cycle_ms: float, items, half_enabled: bool = False):
    """算出此刻时间轴处于什么相位。

    items: [{"t": 毫秒, "hold": 毫秒, "half": bool?, ...}]，t 为周期内起点。
    返回 (phase, item)：phase ∈ {"half", "full", None}。
    跨周期回绕；多组重叠时取「最近一次开始」的那条（沿用指南 6.2）。
    """
    if not cycle_ms or not items:
        return None, None
    u = float(now_ms) % float(cycle_ms)
    best = None
    for it in items:
        t = float(it.get("t", 0.0))
        hold = float(it.get("hold", 0.0))
        if hold <= 0.0:
            continue
        dt = (u - t) % float(cycle_ms)          # 跨周期回绕
        if dt < hold and (best is None or t > best[0]):
            best = (t, hold, dt, it)
    if best is None:
        return None, None
    _, hold, dt, it = best
    half = bool(half_enabled and it.get("half", False) and dt < hold / 2.0)
    return ("half" if half else "full"), it


class Arbiter:
    """每帧唯一状态源：`resolve()` 之后各通道照读，不再各自改状态。"""

    MOOD_COOLDOWN_MS = 20000.0

    def __init__(self, interaction_guard_ms=3000, hold_ms=1800,
                 blink_zero_gaze=False, clock=None):
        self.guard_ms = float(interaction_guard_ms)
        self.hold_ms = float(hold_ms)
        self.blink_zero_gaze = bool(blink_zero_gaze)
        self._clock = clock or _mono_ms
        self._hold = {c: None for c in CHANNELS}    # channel -> (source, value, until)
        self._last_touch = -1e18
        self._last_mood = -1e18
        self._timeline_expr = None                  # 'half' | 'blink' | None

    # ---------------- 内部 ----------------
    def _now(self, now_ms=None) -> float:
        return self._clock() if now_ms is None else float(now_ms)

    def _release(self, channel, source):
        h = self._hold[channel]
        if h is not None and h[0] == source:
            self._hold[channel] = None

    # ---------------- 时间轴 ----------------
    def set_timeline_blink(self, phase, now_ms=None):
        """phase ∈ {None, 'half', 'full'}，由时间轴每帧算好后传入。

        ⚠️ 来源约束：phase 只应由 `timeline_phase()` 产出，且 'half' 仅在
        item.half == true 时出现。EffectDirector 等 idle 通道不得直接调用本方法，
        否则 Q2 的「半闭默认关」会在接线层被绕开。
        """
        if phase is None:
            self._timeline_expr = None
            self._release("blink", "timeline")
            self._release("expr", "timeline")
            return
        value = "half" if phase == "half" else "blink"
        self._timeline_expr = value
        until = self._now(now_ms) + 1e9        # 由 phase=None 显式释放，不靠超时
        self._hold["blink"] = ("timeline", value, until)
        self._hold["expr"] = ("timeline", value, until)

    # ---------------- 用户交互 ----------------
    def touch(self, now_ms=None):
        """用户摸/拖/点了桌宠：刷新 guard 时间戳。"""
        self._last_touch = self._now(now_ms)

    def mood_allowed(self, now_ms=None) -> bool:
        """刚交互过、或刚切过 mood，都不允许再切。"""
        now = self._now(now_ms)
        return (now - self._last_touch >= self.guard_ms and
                now - self._last_mood >= self.MOOD_COOLDOWN_MS)

    def mark_mood_switch(self, now_ms=None):
        self._last_mood = self._now(now_ms)

    # ---------------- 请求 ----------------
    def request(self, source, channel, value, hold_ms=None, now_ms=None) -> bool:
        """提交一次占用；返回是否被接受。被拒不排队、不延迟执行。"""
        if source not in PRIORITY or channel not in CHANNELS:
            return False
        if channel in _TIMELINE_ONLY and source != "timeline":
            return False                     # blink 由时间轴独占
        now = self._now(now_ms)

        # 交互 guard：idle 在用户操作后一段时间内全拒
        if source == "idle" and now - self._last_touch < self.guard_ms:
            return False

        # 时间轴占用 expr 期间，其它来源一律拒绝
        if channel == "expr" and self._timeline_expr is not None \
                and source != "timeline":
            return False

        h = self._hold[channel]
        if h is not None and h[2] > now:
            if PRIORITY[h[0]] >= PRIORITY[source]:   # 低不打断高；同级不抢占
                return False

        ms = self.hold_ms if hold_ms is None else float(hold_ms)
        self._hold[channel] = (source, value, now + ms)
        return True

    # ---------------- 每帧 ----------------
    def tick(self, now_ms=None):
        """释放过期占用（时间轴的由 set_timeline_blink(None) 释放）。"""
        now = self._now(now_ms)
        for c in CHANNELS:
            h = self._hold[c]
            if h is not None and h[2] <= now and h[0] != "timeline":
                self._hold[c] = None

    def resolve(self, now_ms=None) -> dict:
        """返回本帧各通道的最终值。"""
        now = self._now(now_ms)
        self.tick(now)
        out = {}
        for c in CHANNELS:
            h = self._hold[c]
            out[c] = h[1] if (h is not None and h[2] > now) else DEFAULT[c]
        if self._timeline_expr is not None:       # 兜底强制覆盖
            out["expr"] = self._timeline_expr
        if self.blink_zero_gaze and out["blink"] is not None:
            out["gaze"] = (0.0, 0.0)
        return out
