#!/usr/bin/env python3
"""v1 → v2 manifest 迁移：保留备份、打印分组表、迁移后自检。

用法：
    python3 tools/migrate_manifest_v1_v2.py desktop_pet/assets/*/manifest.json
    python3 tools/migrate_manifest_v1_v2.py --check  <manifest...>   # 只校验不写
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop_pet"))

from manifest_io import (active_eye_parts, legacy_consistent,  # noqa: E402
                         read_manifest, validate_manifest, v1_to_v2)


def _report_channels(v2: dict) -> None:
    """把「哪些 part 被过滤、为什么」打出来——过滤结果必须可见。"""
    rows = []
    for it in v2.get("items") or []:
        parts = [p for p in it.get("parts") or [] if isinstance(p, dict)]
        act = {p.get("id") for p in active_eye_parts(it)}
        dropped = []
        for p in parts:
            pid = p.get("id")
            if pid in act:
                continue
            dropped.append(f"{pid}({'group=null' if p.get('group') is None else 'mask=null'})")
        if dropped:
            tag = "  [pending 需补 mask]" if not act else ""
            rows.append(f"  [filter] {it.get('stem', '?')}: "
                        f"active {len(act)}/{len(parts)}，"
                        f"被过滤 {', '.join(dropped)}{tag}")
    if rows:
        print("通道过滤（这些 part 不参与眨眼/视线/表情）：")
        print("\n".join(rows))


def _check_derived(v2: dict, tag: str) -> bool:
    """派生字段逐项一致自检：并存旧字段与 parts/timeline 必须完全一致。"""
    ok = True
    for it in v2.get("items") or []:
        good, diffs = legacy_consistent(it)
        if not good:
            ok = False
            print(f"[error] {tag}：{it.get('stem', '?')} 派生字段不一致："
                  + ", ".join(diffs))
    return ok


def migrate(path: Path, check: bool = False) -> bool:
    data = read_manifest(path)
    if data.get("schema_version") == 2:
        ok, msg = validate_manifest(data)
        if ok:
            ok = _check_derived(data, path.name)
        print(f"[skip] {path}: 已是 v2（{msg}）")
        return ok

    v2, rows = v1_to_v2(data)
    print(f"\n== {path}")
    print("推导出的分组表（隐式「每两只一组」已物化，请人工核对）：")
    for r in rows:
        print("  " + r)

    ok, msg = validate_manifest(v2)
    if not ok:
        print(f"[error] 迁移结果未通过 schema 校验：{msg}")
        return False
    if not _check_derived(v2, path.name):
        return False
    _report_channels(v2)
    if check:
        # --check 纯只读：不创建 .v1.bak、不写任何文件
        print("[check] 校验通过，未写盘")
        return True

    bak = path.with_suffix(path.suffix + ".v1.bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"[backup] {bak.name}")
    path.write_text(json.dumps(v2, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"[write] {path}  schema_version=2，items={len(v2['items'])}")
    return True


def main():
    ap = argparse.ArgumentParser(description="v1 → v2 manifest 迁移")
    ap.add_argument("paths", nargs="+", help="manifest.json 路径（支持 glob 展开）")
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()
    bad = 0
    for raw in args.paths:
        p = Path(raw)
        if not p.is_file():
            print(f"[skip] {raw}: 不是文件")
            continue
        if not migrate(p, args.check):
            bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
