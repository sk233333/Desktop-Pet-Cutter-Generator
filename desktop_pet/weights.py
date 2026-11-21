#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户自备权重路径解析：只查本地路径，不自动获取，也不加载模型。

权重来源优先级：配置 ``weights.<key>`` → 环境变量
``DESKTOP_PET_<KEY>_WEIGHT``。只做 ``expanduser`` 与存在性检查；
路径不存在返回 ``None``，不会创建目录、不会写仓库。
"""

from __future__ import annotations

import os
from pathlib import Path

WEIGHT_KEYS = ("sam2", "grounding_dino")


def _weights_section(cfg) -> dict:
    raw = cfg.get("weights") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def _env_name(key: str) -> str:
    return f"DESKTOP_PET_{key.upper()}_WEIGHT"


def resolve_weight(cfg, key):
    """返回展开后的存在路径；缺失或不存在时返回 ``None``。"""
    key = str(key or "")
    if key not in WEIGHT_KEYS:
        return None

    raw = _weights_section(cfg).get(key)
    value = raw if isinstance(raw, str) and raw.strip() else ""
    if not value:
        value = os.environ.get(_env_name(key), "") or ""
    value = value.strip()
    if not value:
        return None

    path = Path(value).expanduser()
    return path if path.exists() else None


def weight_available(cfg, key) -> bool:
    return resolve_weight(cfg, key) is not None


def describe_cuda() -> dict:
    """只读环境变量，不导入任何计算库。"""
    value = os.environ.get("CUDA_VISIBLE_DEVICES", "") or ""
    return {"cuda_visible": bool(value), "value": value}


def cache_dirs() -> list[Path]:
    """候选缓存目录；只返回路径，不创建目录。"""
    home = Path.home()
    return [home / ".u2net", home / ".cache" / "desktop_pet" / "weights"]
