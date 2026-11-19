"""升级向导的 Qt-free 核心：扫 SKU、体检、allowed 提案、统一落盘与回滚。

这里不是第二份状态源：``commit`` 的 ChangeSet 先在内存里合并、过
``validate_manifest``，通过后才写独立覆盖层 + manifest；任一步失败按快照回滚。
v1 manifest 走 ``manifest_io.v1_to_v2`` 同一迁移入口，不另写一套转换。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from completers.base import ChangeSet, PathGuardError, Report, apply_patch, guard_paths
from manifest_io import SCHEMA_VERSION, validate_manifest, v1_to_v2

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
MANIFEST_NAME = "manifest.json"


@dataclass
class SkuInfo:
    name: str
    dir: Path
    manifest_path: Path
    manifest: dict
    schema_version: int
    sku_field: str | None
    error: str | None = None

    @property
    def consistent(self) -> bool:
        return self.error is None and self.sku_field == self.name


@dataclass
class CommitResult:
    report: Report
    merged: dict | None = None
    migrated: bool = False
    rows: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    before: dict = field(default_factory=dict)


def scan_skus(root=None) -> list[SkuInfo]:
    """扫 ``assets/*/manifest.json``；SKU 主键是目录名，不臆造 ``skus`` 顶层键。"""
    base = Path(root or ASSETS_DIR)
    out: list[SkuInfo] = []
    for mp in sorted(base.glob(f"*/{MANIFEST_NAME}")):
        name = mp.parent.name
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
            err = None if isinstance(data, dict) else "manifest 顶层不是对象"
            data = data if isinstance(data, dict) else {}
        except Exception as exc:             # noqa: BLE001
            data, err = {}, f"{type(exc).__name__}: {exc}"
        sv = data.get("schema_version", 1)
        out.append(SkuInfo(name=name, dir=mp.parent, manifest_path=mp,
                           manifest=data,
                           schema_version=sv if isinstance(sv, int) else 1,
                           sku_field=data.get("sku"), error=err))
    return out


def load_sku(info: SkuInfo) -> SkuInfo:
    """重新读盘（体检 / 预览前刷新一次，避免拿旧内容做候选）。"""
    try:
        data = json.loads(info.manifest_path.read_text(encoding="utf-8"))
        err = None if isinstance(data, dict) else "manifest 顶层不是对象"
        data = data if isinstance(data, dict) else {}
    except Exception as exc:                 # noqa: BLE001
        data, err = {}, f"{type(exc).__name__}: {exc}"
    sv = data.get("schema_version", 1)
    return SkuInfo(name=info.name, dir=info.dir, manifest_path=info.manifest_path,
                   manifest=data,
                   schema_version=sv if isinstance(sv, int) else 1,
                   sku_field=data.get("sku"), error=err)


def health_items(info: SkuInfo) -> list[dict]:
    """把 Issue / CHANGELOG / HANDOFF 里的 pending 变成一张可见清单。"""
    rows: list[dict] = []
    if info.error:
        rows.append({"id": "parse", "label": "manifest 解析",
                     "status": "blocked", "detail": info.error,
                     "selectable": False})
        return rows
    if not info.consistent:
        rows.append({"id": "sku", "label": "目录名 / sku 一致性",
                     "status": "blocked",
                     "detail": f"目录 {info.name} ≠ manifest sku {info.sku_field}；"
                               f"不静默纠偏",
                     "selectable": False})
    rows.append({
        "id": "schema",
        "label": "manifest schema",
        "status": "ok" if info.schema_version == SCHEMA_VERSION else "pending",
        "detail": "v2" if info.schema_version == SCHEMA_VERSION
                  else f"当前 v{info.schema_version}，落盘时会走同一 v1→v2 迁移入口",
        "selectable": False,
    })

    items = [it for it in (info.manifest.get("items") or [])
             if isinstance(it, dict)]
    for it in items:
        stem = it.get("stem", "?")
        eyes = len(it.get("eyes") or [])
        parts = [p for p in (it.get("parts") or []) if isinstance(p, dict)]
        has_mask = bool(it.get("eyes_mask")) or any(p.get("mask") for p in parts)
        if eyes and not has_mask:
            rows.append({
                "id": f"mask:{stem}",
                "label": f"{stem} 缺 socket",
                "status": "pending",
                "detail": f"{eyes} 只眼没有 mask（Issue #1）；可用 "
                          f"mask.from_ellipse 逐只补，或人工重画",
                "selectable": True,
            })
        if eyes == 1:
            rows.append({
                "id": f"wink:{stem}",
                "label": f"{stem} 单眼 wink",
                "status": "pending",
                "detail": "单眼素材的 wink_l/wink_r 会退化；建议 "
                          "effects.enabled 关掉 wink（本轮不自动写）",
                "selectable": False,
            })

    rows.extend([
        {"id": "issue2", "label": "Issue #2 · 窄侧位移抽查",
         "status": "pending",
         "detail": "三张新补 mask 未做窄侧位移抽查；calib.* 排 v0.1.1",
         "selectable": False},
        {"id": "sway25", "label": "摆动 25° 阈值",
         "status": "pending",
         "detail": "SWAY_AXIS_PARALLEL_DEG=25 未对真实素材标定；本轮只展示",
         "selectable": False},
        {"id": "bias", "label": "bias 行内控件（C-5）",
         "status": "ok",
         "detail": "已在「摆动」页暴露，上下限与 SWAY_RANGES 同源；"
                   "本行仅保留可见性",
         "selectable": False},
        {"id": "smile", "label": "smile 独立状态",
         "status": "v0.1.1",
         "detail": "待 B-2 拍板；B-1 只产几何数据槽（未接线），"
                   "不改 STATES / 别名表，状态仍不可勾选",
         "selectable": False},
    ])
    return rows


_EXCLUDED_STATES = ("smile", "blink")


def allowed_proposal(manifest: dict) -> dict:
    """只产 ``allowed`` 提案文本，不落运行时词表。

    ``derive_allowed()`` 尚未实现；任何消费本提案去写词表的路径都算越界。
    ``smile`` 必须排除：``apply()`` 会把它映射成半眼。
    """
    from expr_state import STATES

    usable = False
    for it in manifest.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("eyes_mask") or any(
                isinstance(p, dict) and p.get("mask")
                for p in (it.get("parts") or [])):
            usable = True
            break
    base = ["normal"]
    if usable:
        base += ["half", "surprise", "wink_l", "wink_r"]
    allowed = [s for s in base if s in STATES and s not in _EXCLUDED_STATES]
    return {
        "source": "proposal-only",
        "runtime_consumer": None,
        "allowed": allowed,
        "excluded": list(_EXCLUDED_STATES),
        "note": "derive_allowed() 未实现：本提案不写运行时词表",
    }


def snapshot_files(paths) -> dict[str, tuple[bytes, int] | None]:
    out: dict[str, tuple[bytes, int] | None] = {}
    for raw in paths:
        p = Path(raw)
        if p.exists():
            out[str(p)] = (p.read_bytes(), p.stat().st_mtime_ns)
        else:
            out[str(p)] = None
    return out


def _restore(snap: dict) -> None:
    for name, val in snap.items():
        p = Path(name)
        if val is None:
            if p.exists():
                p.unlink()
        else:
            p.write_bytes(val[0])


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def diff_pointers(a, b, prefix: str = "") -> list[str]:
    """粗粒度字段 diff，只用于报告里「改了哪些层」。"""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            out += diff_pointers(a.get(k, "<missing>"), b.get(k, "<missing>"),
                                 f"{prefix}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            out += diff_pointers(a[i] if i < len(a) else "<missing>",
                                 b[i] if i < len(b) else "<missing>",
                                 f"{prefix}/{i}")
    elif a != b:
        out.append(prefix or "/")
    return out


def commit_changes(info: SkuInfo, changeset: ChangeSet,
                   dry_run: bool = False) -> CommitResult:
    """统一落盘：路径守卫 → 迁移 → 合并 → validate → 原子写 → 失败回滚。"""
    try:
        targets = guard_paths(info.dir, changeset.paths)
    except PathGuardError as exc:
        return CommitResult(Report(False, [f"拒绝落盘：{exc}"]))

    if not changeset.files and not changeset.manifest_patch:
        return CommitResult(
            Report(True, ["无变更：未写盘（只读候选 / 整体否决）"]),
            merged=copy.deepcopy(info.manifest))

    try:
        raw = info.manifest_path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:                 # noqa: BLE001
        return CommitResult(Report(False, [f"读 manifest 失败：{exc}"]))

    migrated, rows = False, []
    if data.get("schema_version") != SCHEMA_VERSION:
        try:
            data, rows = v1_to_v2(data)
        except Exception as exc:             # noqa: BLE001
            return CommitResult(Report(False, [f"v1→v2 迁移失败：{exc}"]))
        migrated = True

    try:
        merged = apply_patch(data, changeset.manifest_patch)
    except Exception as exc:                 # noqa: BLE001
        return CommitResult(Report(False, [f"manifest patch 失败：{exc}"]))

    ok, msg = validate_manifest(merged)
    if not ok:
        return CommitResult(Report(False, [f"合并后未通过 validate_manifest：{msg}"]),
                            merged=merged, migrated=migrated, rows=rows)

    changed = list(changeset.manifest_patch)
    lines = [f"validate_manifest：{msg}"]
    if migrated:
        lines.append("v1→v2：走 manifest_io.v1_to_v2 同一迁移入口")
    lines.append(f"manifest patch：{len(changed)} 个字段")
    if dry_run:
        lines.append("dry-run：未写盘")
        return CommitResult(Report(True, lines), merged=merged,
                            migrated=migrated, rows=rows, changed=changed)

    snap = snapshot_files([info.manifest_path, *targets])
    written: list[str] = []
    try:
        for name, data_bytes in changeset.files.items():
            _atomic_write(info.dir / name, data_bytes)
            written.append(name)
        _atomic_write(
            info.manifest_path,
            (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    except Exception as exc:                 # noqa: BLE001
        _restore(snap)
        return CommitResult(Report(False, [f"落盘失败，已整体回滚：{exc}"]),
                            merged=merged, migrated=migrated, rows=rows,
                            changed=changed, before=snap)

    lines.append(f"写入：{', '.join(written) if written else '(无覆盖层)'}")
    return CommitResult(Report(True, lines), merged=merged, migrated=migrated,
                        rows=rows, changed=changed, written=written, before=snap)


def rollback(result: CommitResult) -> Report:
    """verify 失败时按 commit 前快照整体回滚。"""
    if not result.before:
        return Report(False, ["没有可用快照，未回滚"])
    try:
        _restore(result.before)
        return Report(True, [f"已回滚 {len(result.before)} 个文件"])
    except Exception as exc:                 # noqa: BLE001
        return Report(False, [f"回滚失败：{exc}"])


def manifest_diff_summary(before: dict, after: dict, limit: int = 12) -> list[str]:
    """报告用：只列有变化的字段路径，超量截断。"""
    changed = diff_pointers(before, after)
    if not changed:
        return ["manifest 无字段变化"]
    head = changed[:limit]
    more = len(changed) - len(head)
    return head + ([f"… 另有 {more} 处"] if more else [])
