#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W3 选 1：把只读标注报告落成 manifest 的可选草案。

草案只写 ``items[n].annotation_draft``，不展开到 parts / hair / cloth / sway；
人工确认后仍走现有 ChangeSet → commit_changes() 这一条写盘路径。
本模块不 import Qt，不自动判真眼。
"""

from __future__ import annotations

from pathlib import Path

try:  # 包内 / 脚本两种导入方式
    from .ai_annotate import AnnotationReport
    from .completers.base import ChangeSet
    from .upgrade_core import CommitResult, SkuInfo, commit_changes
except ImportError:  # pragma: no cover - 直接以脚本方式运行
    from ai_annotate import AnnotationReport
    from completers.base import ChangeSet
    from upgrade_core import CommitResult, SkuInfo, commit_changes

DRAFT_SCHEMA = "ai-annotate/1"


def build_draft(report: AnnotationReport) -> dict:
    """把只读报告转成草案对象；保留 warning，坐标系仍是 0..1。"""
    data = report.to_dict()
    return {
        "schema": DRAFT_SCHEMA,
        "eyes": data.get("eyes") or [],
        "mouth": data.get("mouth"),
        "hair": data.get("hair") or [],
        "sway": data.get("sway") or {},
        "warnings": data.get("warnings") or [],
    }


def save_draft(info: SkuInfo, item_index: int, report: AnnotationReport,
               dry_run: bool = False) -> CommitResult:
    """把草案写进 ``items[item_index].annotation_draft``。

    只改这一个字段；parts / 素材文件零改动。写入仍由 commit_changes()
    统一做路径守卫、迁移、validate 与原子写。
    """
    items = (info.manifest or {}).get("items") or []
    if not isinstance(item_index, int) or not (0 <= item_index < len(items)):
        raise ValueError(f"item_index 越界：{item_index}")
    stem = items[item_index].get("stem") or f"item{item_index}"
    cs = ChangeSet(
        sku=info.name,
        completer="ai_annotate",
        candidate_id=f"annotation_draft:{stem}",
        manifest_patch={
            f"items/{item_index}/annotation_draft": build_draft(report),
        },
        notes="W3 草案：只读候选，确认前不展开到 parts",
    )
    return commit_changes(info, cs, dry_run=dry_run)
