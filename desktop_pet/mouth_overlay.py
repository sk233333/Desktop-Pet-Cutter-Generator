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


def _warn(kind: str, sku_dir, name: str, detail: str = "", key=None):
    """同一 SKU 的同一文件同一类失败只打一次；不同 SKU 不互相吞。

    需要把 stem 等额外维度并入去重时，显式传 `key`。
    """
    key = key if key is not None else (str(sku_dir), name, kind)
    if key in _WARNED:
        return
    _WARNED.add(key)
    suffix = f"（{detail}）" if detail else ""
    print(f"[mouth] {sku_dir}: {kind}：{name}{suffix}", file=sys.stderr)


def warn_files_fallback(manifest, stem: str, filename: str):
    """stem 零命中、按 files basename 精确兜底成功时，打一行可追溯的 warn。"""
    _warn("stem 未命中，按 files 精确兜底", manifest, filename,
          f"stem={stem}", key=(str(manifest), stem, filename))


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def match_items(items, source) -> tuple[list[dict], bool]:
    """按源图 stem 精确匹配；零命中时才按 files 的**精确 basename** 兜底。

    返回 `(命中列表, 是否走了 files 兜底)`；全部命中都返回，由调用方决定
    「唯一才用、多条跳过」——这里不猜第一条，也不做 contains / 前缀匹配。
    """
    try:
        p = Path(source)
    except TypeError:
        return [], False
    stem, name = p.stem, p.name
    rows = [it for it in (items or []) if isinstance(it, dict)]
    hits = [it for it in rows if it.get("stem") == stem]
    if hits:
        return hits, False
    out = []
    for it in rows:
        files = it.get("files")
        if not isinstance(files, list):
            continue
        if any(isinstance(f, str) and Path(f).name == name for f in files):
            out.append(it)
    return out, bool(out)


def mouth_name(item: dict, stem: str) -> str | None:
    """`mouth_dir` 优先；缺失时按 `stem + mouth_level` 生成。

    返回值整体交给 `MouthOverlay.load()` 的路径守卫，不在拼接处绕过。
    """
    if not isinstance(item, dict):
        return None
    name = item.get("mouth_dir")
    if isinstance(name, str) and name.strip():
        return name
    level = item.get("mouth_level")
    if isinstance(level, str) and level.strip():
        return f"{stem}_mouth_{level.strip()}.png"
    return None


class MouthOverlay:
    """一张静态嘴型覆盖层；构造只走 `load()`。"""

    def __init__(self, size: tuple[int, int], bbox: tuple[int, int, int, int],
                 rgb: np.ndarray, alpha: np.ndarray):
        self.size = size            # (height, width)，与 frame.shape[:2] 对齐
        self.bbox = bbox            # (y0, y1, x0, x1)，右/下开区间
        self._rgb = rgb             # float32 (h, w, 3)
        self._alpha = alpha         # float32 (h, w, 1)，0..1

    @classmethod
    def load(cls, sku_dir, name, size, *,
             quiet_missing: bool = False) -> "MouthOverlay | None":
        """只读加载；任何失败都返回 None，不把异常带进帧循环。

        sku_dir: SKU 目录
        name:    manifest item 的 mouth_dir（相对 SKU 目录）
        size:    (height, width)，必须等于素材 rgba.shape[:2]
        quiet_missing: 仅为 True 时，文件不存在不 warn（同族可选档位探测用）；
                       其余失败照常 warn，路径守卫与尺寸校验不放松
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
            if not quiet_missing:
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
