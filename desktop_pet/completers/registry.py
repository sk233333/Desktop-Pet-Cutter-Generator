"""补全器注册表：向导只认这里，不硬编码列表。

首期只有三个纯本地补全器；L3 用户脚本默认关闭，本轮不实现扫描。
"""

from __future__ import annotations

from dataclasses import dataclass

from completers import mask_domain_hint, mask_ellipse, mouth_open


@dataclass(frozen=True)
class Entry:
    id: str
    module: object
    domain: str          # "通用" / "shaded / illustration"
    risk: str            # "低" / "中"
    summary: str


_BUILTIN = (
    Entry("mask.from_ellipse", mask_ellipse, "通用", "中",
          "用 eyes 椭圆生成 socket；必须逐只点清真眼，已有 mask 跳过"),
    Entry("mask.domain_hint", mask_domain_hint, "通用", "低",
          "只读候选框：把「哪只可能是真眼」摆给人看，不写盘"),
    Entry("mouth.open", mouth_open, "通用", "低",
          "补 mouth_dir 空槽：独立覆盖层 PNG（闭 / 微笑 / 小 o）"),
)

REGISTRY: dict[str, Entry] = {e.id: e for e in _BUILTIN}


def entries() -> tuple[Entry, ...]:
    return tuple(REGISTRY.values())


def get(completer_id: str) -> Entry:
    return REGISTRY[completer_id]
