#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整帧重绘的中间帧纯函数：只做 RGBA 插值，不碰状态表 / 写盘。

产物由调用方落成 SKU 目录内的 PNG，并在 manifest 的 ``expr_frames`` 里登记；
运行时只按 hold_ms 切索引，不在每帧做重采样。
"""

from __future__ import annotations

import numpy as np

MAX_FRAMES = 8
MIN_HOLD_MS = 16
MAX_HOLD_MS = 2000
MAX_FRAME_BYTES = 1_500_000


def interleave(a: np.ndarray, b: np.ndarray, count: int) -> list[np.ndarray]:
    """在两张同尺寸 RGBA 帧之间生成 count 张过渡帧（含首尾）。

    - count 限制 1~8；1 时只返回首帧；
    - RGB 线性插值；alpha 取两者最大值，避免过渡时边缘突然消失；
    - 不修改入参。
    """
    if not isinstance(count, int) or isinstance(count, bool) \
            or not (1 <= count <= MAX_FRAMES):
        raise ValueError(f"count 必须在 1~{MAX_FRAMES}")
    x = np.asarray(a)
    y = np.asarray(b)
    if x.shape != y.shape or x.ndim != 3 or x.shape[2] != 4:
        raise ValueError("两张帧必须是同尺寸 RGBA")
    if count == 1:
        return [x.copy()]
    out = []
    for i in range(count):
        t = i / (count - 1)
        rgb = (x[..., :3].astype(np.float32) * (1.0 - t)
               + y[..., :3].astype(np.float32) * t)
        alpha = np.maximum(x[..., 3], y[..., 3])
        frame = np.empty_like(x)
        frame[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
        frame[..., 3] = alpha
        out.append(frame)
    return out


def frame_budget_ok(frames: list[np.ndarray]) -> bool:
    """粗预算：单帧未压缩 RGBA 字节不超过上限；供测试与生成侧自检。"""
    return all(np.asarray(f).nbytes <= MAX_FRAME_BYTES for f in frames)
