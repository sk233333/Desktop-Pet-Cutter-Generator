"""补全器的公共数据结构、路径守卫与图像小工具。

约束（升级向导首期）：
- ``propose`` / ``preview`` / ``commit`` / ``verify`` 全程不写盘；
- ``ChangeSet.paths`` 必须落在当前 SKU 目录内，拒绝 ``..`` / 绝对路径 / 符号链接逃逸；
- ``commit`` 只返回字节与 manifest 字段 patch，真正的落盘由 ``upgrade_core`` 统一执行。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


class PathGuardError(ValueError):
    """ChangeSet 里的路径越出当前 SKU 目录（或试图直写 manifest）。"""


@dataclass
class Candidate:
    """一个待人工确认的候选。

    ``payload`` 只放补全器自己用的只读数据；界面可以在提交前修改
    ``payload["selected"]`` 这类选择项，但不得改素材文件。
    """

    id: str
    kind: str                     # "mask" | "mouth" | "hint"
    payload: dict = field(default_factory=dict)
    note: str = ""
    commit_allowed: bool = True


@dataclass
class ChangeSet:
    """commit() 的返回值：待写入的覆盖层 + manifest 字段 patch。

    ``files`` 的 key 是相对 SKU 目录的路径；``manifest_patch`` 的 key 是
    JSON pointer（如 ``items/0/parts/2/mask``），值是新值。v1 manifest 会由
    ``upgrade_core`` 先走 ``v1_to_v2`` 同一迁移入口，再应用 patch。
    """

    sku: str
    completer: str
    candidate_id: str
    files: dict[str, bytes] = field(default_factory=dict)
    manifest_patch: dict[str, object] = field(default_factory=dict)
    notes: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def paths(self) -> list[str]:
        return list(self.files)


@dataclass
class Report:
    """verify() / 落盘结果；``ok=False`` 时向导整体回滚。"""

    ok: bool
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class OverlayImage:
    """并排预览用：真实图上叠好候选的 RGB 图 + 只含候选的 RGBA 覆盖层。"""

    base: np.ndarray              # HxWx3 uint8（原图铺白底）
    overlay: np.ndarray           # HxWx4 uint8（透明背景）
    note: str = ""


def guard_paths(sku_dir, paths) -> list[Path]:
    """把相对路径锁死在 SKU 目录内；越界或直写 manifest 一律拒绝。"""
    root = Path(sku_dir).resolve()
    out: list[Path] = []
    for raw in paths:
        rel = Path(str(raw))
        if rel.is_absolute() or ".." in rel.parts:
            raise PathGuardError(f"路径越界：{raw}")
        if rel.name == "manifest.json" or str(rel) == "manifest.json":
            raise PathGuardError("manifest.json 只能由向导统一写入")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise PathGuardError(f"路径越界：{raw}")
        out.append(target)
    return out


def apply_patch(data: dict, patch: dict) -> dict:
    """按 JSON pointer 深拷贝后写值；容器不存在即报错（不猜结构）。"""
    out = copy.deepcopy(data)
    for pointer, value in (patch or {}).items():
        if not pointer or pointer == "/":
            raise ValueError("manifest patch 的 pointer 不能为空")
        segs = [s.replace("~1", "/").replace("~0", "~")
                for s in pointer.strip("/").split("/")]
        node = out
        for seg in segs[:-1]:
            node = node[int(seg)] if isinstance(node, list) else node[seg]
        last = segs[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return out


def load_base_rgb(sku_dir, stem: str) -> np.ndarray:
    """读 SKU 里的原图并铺白底；找不到时抛 FileNotFoundError。"""
    p = Path(sku_dir) / f"{stem}.png"
    if not p.exists():
        raise FileNotFoundError(str(p))
    rgba = np.array(Image.open(p).convert("RGBA"))
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32) * a + 255.0 * (1.0 - a)
    return rgb.astype(np.uint8)


def composite(base_rgb: np.ndarray, overlay_rgba: np.ndarray) -> np.ndarray:
    """source-over：只用于预览，不参与落盘。"""
    out = base_rgb.astype(np.float32).copy()
    a = overlay_rgba[..., 3:4].astype(np.float32) / 255.0
    out = out * (1.0 - a) + overlay_rgba[..., :3].astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


def encode_png(canvas: np.ndarray) -> bytes:
    """BGR / BGRA → PNG 字节；不落盘。"""
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("PNG 编码失败")
    return buf.tobytes()
