#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""嘴型 overlay：只读加载 + alpha bbox 合成（Qt-free）。

边界见 `docs/HANDOFF_MOUTH_OVERLAY.md`：

- 覆盖层是素材原尺寸 RGBA；
- 只在 alpha bbox 内做 source-over，bbox 外与 alpha 通道逐比特不变；
- 缺失 / 损坏 / 尺寸不符 / 全透明 / 路径越界一律返回 None，不抛异常；
- 不写盘、不碰 manifest、不碰 ExprBank / arbiter。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

_WARNED: set[str] = set()


def _warn(kind: str, sku_dir, name: str, detail: str = ""):
    """同一 SKU 的同一文件同一类失败只打一次；不同 SKU 不互相吞。"""
    key = (str(sku_dir), name, kind)
    if key in _WARNED:
        return
    _WARNED.add(key)
    suffix = f"（{detail}）" if detail else ""
    print(f"[mouth] {sku_dir}: {kind}：{name}{suffix}", file=sys.stderr)


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class MouthOverlay:
    """一张静态嘴型覆盖层；构造只走 `load()`。"""

    def __init__(self, size: tuple[int, int], bbox: tuple[int, int, int, int],
                 rgb: np.ndarray, alpha: np.ndarray):
        self.size = size            # (height, width)，与 frame.shape[:2] 对齐
        self.bbox = bbox            # (y0, y1, x0, x1)，右/下开区间
        self._rgb = rgb             # float32 (h, w, 3)
        self._alpha = alpha         # float32 (h, w, 1)，0..1

    @classmethod
    def load(cls, sku_dir, name, size) -> "MouthOverlay | None":
        """只读加载；任何失败都返回 None，不把异常带进帧循环。

        sku_dir: SKU 目录
        name:    manifest item 的 mouth_dir（相对 SKU 目录）
        size:    (height, width)，必须等于素材 rgba.shape[:2]
        """
        if not isinstance(name, str) or not name.strip():
            return None
        if not (isinstance(size, (tuple, list)) and len(size) == 2):
            return None
        try:
            h, w = int(size[0]), int(size[1])
        except (TypeError, ValueError):
            return None
        if h <= 0 or w <= 0:
            return None
        try:
            root = Path(sku_dir).resolve()
        except (TypeError, OSError):
            return None

        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            _warn("越界路径", root, name)
            return None
        try:
            path = (root / rel).resolve()
        except (OSError, RuntimeError):
            _warn("路径无法解析", root, name)
            return None
        if not _is_within(root, path):
            _warn("越界路径", root, name)
            return None
        if not path.is_file():
            _warn("覆盖层不存在", root, name)
            return None

        try:
            with Image.open(path) as im:
                rgba = np.array(im.convert("RGBA"), dtype=np.uint8)
        except Exception as exc:      # 解码失败统一降级
            _warn("覆盖层读取失败", root, name, type(exc).__name__)
            return None

        if rgba.shape[:2] != (h, w):
            _warn("覆盖层尺寸不符", root, name,
                  f"{rgba.shape[1]}x{rgba.shape[0]} != {w}x{h}")
            return None
        alpha = rgba[..., 3]
        ys, xs = np.nonzero(alpha)
        if ys.size == 0:
            _warn("覆盖层全透明", root, name)
            return None
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        sub = rgba[y0:y1, x0:x1].astype(np.float32)
        return cls((h, w), (y0, y1, x0, x1),
                   sub[..., :3].copy(), (sub[..., 3:4] / 255.0).copy())

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """RGBA 帧返回新数组；只在 bbox 内混 RGB，bbox 外与 dst alpha 逐比特不变。

        合成公式 = 按 src alpha 混 RGB、dst alpha 原样保留（非标准 Porter-Duff）；
        透明窗口轮廓只由素材 alpha 决定，覆盖层不能改它。
        RGB（3 通道）帧不做合成，原样返回同一个数组。
        """
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 \
                or frame.shape[2] != 4 \
                or frame.shape[:2] != self.size:
            return frame
        out = frame.copy()
        y0, y1, x0, x1 = self.bbox
        sub = out[y0:y1, x0:x1, :3].astype(np.float32)
        blended = sub * (1.0 - self._alpha) + self._rgb * self._alpha
        out[y0:y1, x0:x1, :3] = np.clip(blended, 0, 255).astype(out.dtype)
        return out
