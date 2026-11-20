"""AI 动作提案 → ChangeSet 适配层。

职责边界：
- 把已夹取的 ``AIMotionProposal`` 转成现有 manifest 能接受的产物；
- 只返回 ChangeSet（覆盖层 PNG + anim / hair / cloth / expr_overlays patch）；
- 不写盘、不改 base / mask；
- 所有落盘统一调用 ``upgrade_core.commit_changes()``，与手动路径同一闸门。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

import upgrade_core
from ai_motion_contract import (ANIM_DEFAULTS, ANIM_RANGES, AIGenError,
                                AIMotionProposal, AIMotionRequest,
                                EXPRESSION_STATES)
from completers import smile_geometry
from completers.base import ChangeSet, encode_png, guard_paths
from manifest_io import SWAY_DEFAULTS, SWAY_RANGES

def safe_stem(stem: str) -> str:
    """SKU / 素材名只允许单段文件名；拒绝 ``..``、绝对路径与分隔符。"""
    s = str(stem or "").strip()
    if (not s or s in (".", "..") or "/" in s or "\\" in s
            or Path(s).is_absolute() or Path(s).name != s):
        raise AIGenError(f"非法 SKU / 素材名：{stem!r}")
    return s


def _num(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if v != v else v


def current_anim(item: dict) -> dict:
    """manifest 里的九项动作参数；缺项用默认值补齐。"""
    raw = item.get("anim") if isinstance(item.get("anim"), dict) else {}
    return {k: _num(raw.get(k), ANIM_DEFAULTS[k]) for k in ANIM_RANGES}


def current_sway(item: dict, kind: str) -> list[dict]:
    """逐区域 sway（amp / dir / bias）；保留原顺序与条目数量。"""
    out: list[dict] = []
    for w in item.get(kind) or []:
        if not isinstance(w, dict):
            continue
        d = w.get("dir")
        out.append({
            "amp": _num(w.get("amp"), SWAY_DEFAULTS["amp"]),
            "dir": None if d is None else _num(d, 0.0) % 360.0,
            "bias": _num(w.get("bias"), SWAY_DEFAULTS["bias"]),
        })
    return out


def request_from_item(sku: str, stem: str, item: dict) -> AIMotionRequest:
    """从 manifest item 构造请求；只带标注几何与当前值。"""
    overlays = item.get("expr_overlays")
    existing = [s for s in EXPRESSION_STATES
                if isinstance(overlays, dict) and isinstance(overlays.get(s), dict)]
    return AIMotionRequest(
        sku=safe_stem(sku), stem=safe_stem(stem),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        eyes=smile_geometry._pick_eyes(item),
        hair_count=len(item.get("hair") or []),
        cloth_count=len(item.get("cloth") or []),
        anim=current_anim(item),
        sway={"hair": current_sway(item, "hair"),
              "cloth": current_sway(item, "cloth")},
        expressions=existing,
    )


def _draw_brows(canvas: np.ndarray, eyes: list[dict], width: int,
                height: int, mode: str) -> None:
    """在眼睑线上方补一道眉毛：angry 内低外高，sad 内高外低。"""
    color = smile_geometry._color(eyes)
    thick = 2 if max(width, height) >= 256 else 1
    ex = sum(e["cx"] for e in eyes) / len(eyes)
    for e in eyes:
        inner = -1.0 if e["cx"] <= ex else 1.0
        half = max(2.0, e["rx"] * 0.9)
        cy = e["cy"] - e["ry"] * 1.55
        dy = max(1.0, e["ry"] * 0.5)
        y_in, y_out = ((cy + dy, cy - dy * 0.2) if mode == "angry"
                       else (cy - dy * 0.2, cy + dy))
        p1 = (int(round(min(max(e["cx"] + inner * half, 0), width - 1))),
              int(round(min(max(y_in, 0), height - 1))))
        p2 = (int(round(min(max(e["cx"] - inner * half, 0), width - 1))),
              int(round(min(max(y_out, 0), height - 1))))
        cv2.line(canvas, p1, p2, color, thick, cv2.LINE_AA)


def _render_expression(item: dict, state: str, params: dict) -> np.ndarray:
    """用 smile_geometry 的同一套几何画覆盖层（RGBA uint8）。

    基态绑定只影响几何取景：joy/shy 允许嘴角弧、sleepy/angry/sad 不画弧；
    angry/sad 额外补一道眉毛，保证参数全 0 时覆盖层也不为空。
    """
    eyes = smile_geometry._pick_eyes(item)
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if not eyes or width <= 0 or height <= 0:
        raise AIGenError("素材缺少眼睛标注或尺寸，无法生成表情覆盖层")
    # 素材已有 mouth_dir 时与 smile 同规则：不再画第二张嘴
    with_arc = state in ("joy", "shy") and not bool(item.get("mouth_dir"))
    canvas = np.zeros((height, width, 4), np.uint8)
    smile_geometry._draw(canvas, eyes, width, height,
                         smile_geometry.DEFAULT_PRESET, with_arc=with_arc,
                         params=params)
    if state in ("angry", "sad"):
        _draw_brows(canvas, eyes, width, height, state)
    if not (canvas[..., 3] > 0).any():
        raise AIGenError(f"{state} 覆盖层为空（眼睛几何不可用）")
    return canvas


def _merge_anim(item: dict, proposal: AIMotionProposal, keys) -> dict | None:
    picked = [k for k in ANIM_RANGES if k in (keys or ())]
    if not picked:
        return None
    out = current_anim(item)
    for k in picked:
        out[k] = proposal.anim[k]
    return out


def _merge_sway(item: dict, proposal: AIMotionProposal,
                kind: str, indices) -> list[dict] | None:
    picked = sorted({int(i) for i in (indices or ())})
    if not picked:
        return None
    src = item.get(kind)
    if not isinstance(src, list) or not src:
        return None
    out = [dict(w) if isinstance(w, dict) else w for w in src]
    amp_lo, amp_hi = SWAY_RANGES["amp"]
    bias_lo, bias_hi = SWAY_RANGES["bias"]
    for i in picked:
        if not (0 <= i < len(out)) or not isinstance(out[i], dict):
            continue
        p = proposal.sway.get(kind, [])[i] if i < len(
            proposal.sway.get(kind, [])) else {}
        amp = max(amp_lo, min(amp_hi, _num(p.get("amp"),
                                          _num(out[i].get("amp"), 1.0))))
        bias = max(bias_lo, min(bias_hi, _num(p.get("bias"),
                                              _num(out[i].get("bias"), 0.0))))
        d = p.get("dir", out[i].get("dir"))
        out[i] = dict(out[i], amp=round(amp, 2),
                      dir=None if d is None else round(_num(d, 0.0) % 360.0, 1),
                      bias=round(bias, 1))
    return out


def build_changeset(*, sku: str, stem: str, item_index: int, item: dict,
                    proposal: AIMotionProposal, selected) -> ChangeSet:
    """按勾选项生成 ChangeSet；未勾选的字段逐字节保持原值。"""
    sku, stem = safe_stem(sku), safe_stem(stem)
    sel = set(selected or ())
    if not sel:
        raise AIGenError("未勾选任何生成项")

    anim_keys = [k for k in ANIM_RANGES if f"anim/{k}" in sel]
    hair_idx = [i for i in range(len(item.get("hair") or []))
                if f"sway/hair/{i}" in sel]
    cloth_idx = [i for i in range(len(item.get("cloth") or []))
                 if f"sway/cloth/{i}" in sel]
    expr_states = [s for s in EXPRESSION_STATES if f"expr/{s}" in sel]

    anim = _merge_anim(item, proposal, anim_keys)
    hair = _merge_sway(item, proposal, "hair", hair_idx)
    cloth = _merge_sway(item, proposal, "cloth", cloth_idx)
    if anim is None and hair is None and cloth is None and not expr_states:
        raise AIGenError("勾选项没有可写入的字段")

    patch: dict = {}
    if anim is not None:
        patch[f"items/{item_index}/anim"] = anim
    if hair is not None:
        patch[f"items/{item_index}/hair"] = hair
    if cloth is not None:
        patch[f"items/{item_index}/cloth"] = cloth

    files: dict[str, bytes] = {}
    overlays = item.get("expr_overlays")
    overlays = dict(overlays) if isinstance(overlays, dict) else {}
    for state in expr_states:
        params = proposal.expressions.get(state)
        if not isinstance(params, dict):
            continue
        name = f"{stem}_{state}.png"
        canvas = _render_expression(item, state, params)
        # cv2.imencode 按 BGRA 解释通道，落盘前显式换序
        files[name] = encode_png(cv2.cvtColor(canvas, cv2.COLOR_RGBA2BGRA))
        overlays[state] = {"overlay": name, "source": "ai_motion",
                           "params": dict(params)}
    if expr_states and not files:
        raise AIGenError("AI 没有给出可渲染的表情参数")
    if files:
        patch[f"items/{item_index}/expr_overlays"] = overlays

    guard_paths(Path("."), files)
    return ChangeSet(
        sku=sku, completer="ai_motion", candidate_id="ai_motion",
        files=files, manifest_patch=patch, meta={"stem": stem,
                                                 "item_index": item_index},
        notes="AI 动作参数（逐项勾选）：" + "、".join(sorted(sel)),
    )


def commit_selected(info: upgrade_core.SkuInfo, changeset: ChangeSet,
                    dry_run: bool = False) -> upgrade_core.CommitResult:
    """唯一写盘出口：委托给手动路径同一 ``commit_changes()``。"""
    return upgrade_core.commit_changes(info, changeset, dry_run=dry_run)
