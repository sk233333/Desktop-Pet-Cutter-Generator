#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B-4 说话驱动嘴型切档：三档发现 + 切档时钟（Qt-free）。

边界：

- 只认 `mouth.open` 的命名约定 `<stem>_mouth_<level>.png`（闭 / 微笑 / 小 o）；
  不符合约定的 `mouth_dir` 只加载自身，不做切档，保持 Q2 静态行为；
- 只读：候选路径整体交给 `MouthOverlay.load()` 的路径守卫，不在这里绕过；
- 不新增状态名、不碰 `ExprBank` / `arbiter` / manifest；切档只决定取哪张
  已预计算的帧，不在帧循环里重做合成。
"""

from __future__ import annotations

from pathlib import Path

try:
    from .mouth_overlay import MouthOverlay
except ImportError:  # 直接以脚本 / 平铺 sys.path 运行时
    from mouth_overlay import MouthOverlay

MOUTH_LEVELS = ("closed", "smile", "o")
CYCLE_INTERVAL = 0.16          # 每个口型停留秒数；只影响观感，不影响状态


def variant_names(name, levels=MOUTH_LEVELS):
    """把 `mouth_dir` 展开成同族三档；返回 `({level: 相对路径}, 默认档)`。

    命中的默认档就是文件名里的那一档；不符合约定时返回 `({}, None)`。
    子目录会被保留，路径安全仍由调用方 `MouthOverlay.load()` 校验。
    """
    if not isinstance(name, str) or not name.strip():
        return {}, None
    path = Path(name)
    stem = path.stem
    for level in levels:
        token = f"_mouth_{level}"
        if stem.endswith(token):
            base = stem[:-len(token)]
            return ({lvl: str(path.with_name(f"{base}_mouth_{lvl}{path.suffix}"))
                     for lvl in levels}, level)
    return {}, None


def load_mouth_set(sku_dir, name, size, levels=MOUTH_LEVELS):
    """加载 `mouth_dir` 及同族档位；返回 `({level: MouthOverlay}, 默认档)`。

    - 默认档读不到 → `({}, None)`，调用方按原有单档逻辑回退；
    - 其余档位缺失 / 损坏 / 越界 → 静默跳过（缺档不 warn，别的失败照常 warn）；
    - 只有默认档时也返回单元素字典，`cycle_level` 会自动退化为常驻。
    """
    names, default = variant_names(name, levels)
    if default is None:
        return {}, None
    out = {}
    for level in levels:
        ov = MouthOverlay.load(sku_dir, names[level], size,
                               quiet_missing=(level != default))
        if ov is not None:
            out[level] = ov
    if default not in out:
        return {}, None
    return out, default


def cycle_level(levels, t, interval=CYCLE_INTERVAL) -> str | None:
    """按动画时间决定当前口型；不到两档时恒为该档，空列表返回 None。"""
    levels = tuple(levels or ())
    if not levels:
        return None
    if len(levels) == 1:
        return levels[0]
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = CYCLE_INTERVAL
    if interval <= 0:
        interval = CYCLE_INTERVAL
    try:
        t = max(0.0, float(t))
    except (TypeError, ValueError):
        t = 0.0
    return levels[int(t / interval) % len(levels)]


def should_cycle(levels, speaking: bool) -> bool:
    """说话窗口内且至少两档才切；否则走原静态帧，不改变四路优先级。"""
    return bool(speaking) and len(tuple(levels or ())) >= 2
