"""AI 候选 → ChangeSet 适配层。

职责边界：
- 把 soft alpha / 区域框转成现有 manifest 能接受的产物；
- 只返回 ChangeSet（base RGBA、独立预览 PNG、``items[].ai_provenance``）；
- 不写盘、不改已有 base、不写 mask PNG；
- 所有落盘统一调用 ``upgrade_core.commit_changes()``，与手动路径同一闸门。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import upgrade_core
from ai_generate_contract import AIGenCandidate, AIGenError
from completers.base import ChangeSet, encode_png, guard_paths
from upgrade_core import SkuInfo

SCHEMA_TAG = "pet-asset/2"


def safe_stem(stem: str) -> str:
    """SKU / 素材名只允许单段文件名；拒绝 ``..``、绝对路径与分隔符。"""
    s = str(stem or "").strip()
    if (not s or s in (".", "..") or "/" in s or "\\" in s
            or Path(s).is_absolute() or Path(s).name != s):
        raise AIGenError(f"非法 SKU / 素材名：{stem!r}")
    return s


def load_source_rgb(path) -> np.ndarray:
    """读原图并铺白底，返回 HxWx3 uint8 RGB；不缓存、不写回。"""
    from PIL import Image
    rgba = np.array(Image.open(path).convert("RGBA"))
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32) * a + 255.0 * (1.0 - a)
    return rgb.astype(np.uint8)


def merge_alpha(candidates: list[AIGenCandidate], height: int, width: int) -> np.ndarray:
    """多个候选的软 alpha 取并集（逐像素 max），保留发丝 / 半透明边。"""
    merged = np.zeros((height, width), np.float32)
    for cand in candidates:
        alpha = cand.alpha
        if not isinstance(alpha, np.ndarray):
            raise AIGenError(f"候选 {cand.id} 缺少 alpha")
        if alpha.shape[:2] != (height, width):
            raise AIGenError(f"候选 {cand.id} 尺寸与请求不一致")
        merged = np.maximum(merged, np.clip(alpha.astype(np.float32), 0.0, 1.0))
    if float(merged.max()) <= 0.0:
        raise AIGenError("所选候选全透明，未生成 SKU")
    return merged


def build_changeset(*, sku: str, stem: str, rgb: np.ndarray,
                    selected: list[AIGenCandidate], provider: str = "",
                    uploaded: bool = False) -> ChangeSet:
    """候选 → ChangeSet；不写盘，不覆盖已有 base。"""
    sku = safe_stem(sku)
    stem = safe_stem(stem)
    if not selected:
        raise AIGenError("未选择任何候选")

    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise AIGenError("原图不是 RGB")
    height, width = rgb.shape[:2]
    alpha = merge_alpha(list(selected), height, width)

    rgba = np.dstack([rgb[..., :3].astype(np.uint8),
                      np.clip(alpha * 255.0, 0, 255).astype(np.uint8)])
    # cv2.imencode 按 BGR/BGRA 解释通道，落盘前显式换序，避免红蓝互换
    png = encode_png(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))

    item = {
        "stem": stem,
        "width": int(width),
        "height": int(height),
        "parts": [],
        "effects": {"enabled": []},
        "ai_provenance": {
            "source": "ai_generate",
            "provider": provider or "unknown",
            "uploaded": bool(uploaded),
            "candidates": [
                {
                    "id": c.id,
                    "confidence": round(float(c.confidence), 4),
                    "box": list(c.box) if c.box else None,
                    "warnings": list(c.warnings),
                }
                for c in selected
            ],
        },
    }
    files = {
        f"{stem}.png": png,
        f"{stem}_ai_preview.png": png,
    }
    patch = {
        "/schema": SCHEMA_TAG,
        "/sku": sku,
        "/generated_at": datetime.now().isoformat(timespec="seconds"),
        "/items": [item],
    }
    # 路径守卫与手动路径同一把尺子：这里先自查一次，commit_changes 再查一次
    guard_paths(Path("."), files)
    return ChangeSet(sku=sku, completer="ai_generate", candidate_id="ai_generate",
                     files=files, manifest_patch=patch,
                     notes="AI 候选：仅生成 base + 预览 + ai_provenance 可选字段")


def new_sku_info(sku: str, root=None) -> SkuInfo:
    """新建 SKU 的内存 v2 骨架；manifest.json 尚不存在，由 commit_changes 落盘。"""
    name = safe_stem(sku)
    base = Path(root or upgrade_core.ASSETS_DIR)
    d = base / name
    return SkuInfo(
        name=name, dir=d, manifest_path=d / upgrade_core.MANIFEST_NAME,
        manifest={"schema": SCHEMA_TAG, "sku": name, "schema_version": 2,
                  "items": []},
        schema_version=2, sku_field=name)


def commit_selected(info: SkuInfo, changeset: ChangeSet,
                    dry_run: bool = False) -> upgrade_core.CommitResult:
    """唯一写盘出口：委托给手动路径同一 ``commit_changes()``。"""
    return upgrade_core.commit_changes(info, changeset, dry_run=dry_run)


def reject_existing_base(info: SkuInfo, stem: str) -> None:
    """新 SKU 才允许 AI 生成 base；已有 base 一律拒绝覆盖。"""
    target = Path(info.dir) / f"{safe_stem(stem)}.png"
    if target.exists():
        raise AIGenError(f"base 已存在，拒绝覆盖：{target.name}")
