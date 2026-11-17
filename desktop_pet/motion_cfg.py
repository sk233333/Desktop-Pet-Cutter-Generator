#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时摆动配置（按 SKU 分开）。

文件位置：`$XDG_CONFIG_HOME/desktop_pet/motion.json`（缺省 `~/.config/...`）。

新格式：
    {"skus": {"<sku>": {"scale": 1.0, "dir": null}}}

旧扁平格式 `{"scale": ..., "dir": ...}` 读取时降级成「所有 SKU 共用」，
**不会**自动改写旧文件；只有用户下一次改幅度 / 方向时，才按新格式保存
（且只写当前 SKU，其他 SKU 的配置原样保留）。

纯标准库，便于无 Qt 环境下的测试。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def motion_cfg_path() -> Path:
    """跟随 XDG_CONFIG_HOME 的配置文件路径。"""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "desktop_pet" / "motion.json"


def _norm_scale(v) -> float:
    try:
        return min(3.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 1.0


def _norm_dir(v):
    if v is None:
        return None
    try:
        return float(v) % 360.0
    except (TypeError, ValueError):
        return None


def load_motion(path: Path, sku: str) -> tuple[float, float | None]:
    """读某个 SKU 的 (scale, dir)；文件缺失 / 损坏 / 旧格式都安全降级。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 1.0, None
    if not isinstance(data, dict):
        return 1.0, None
    skus = data.get("skus")
    if isinstance(skus, dict):
        entry = skus.get(sku)
        if not isinstance(entry, dict):
            return 1.0, None
    else:
        entry = data          # 旧扁平格式：读时降级，不写回
    return _norm_scale(entry.get("scale", 1.0)), _norm_dir(entry.get("dir"))


def save_motion(path: Path, sku: str, scale: float, dir_deg: float | None) -> None:
    """把当前 SKU 的幅度 / 方向写进新格式，保留其他 SKU 的配置。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    skus = data.get("skus") if isinstance(data, dict) else None
    skus = dict(skus) if isinstance(skus, dict) else {}
    skus[sku] = {"scale": _norm_scale(scale), "dir": _norm_dir(dir_deg)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"skus": skus}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
