"""AI 自动标注：只读报告，不写 manifest / 不改 schema。

W3 选 2：模型只给眼眶 / 嘴 / 发缕 / 摆动分区的候选坐标，人工照报告用
现有补全器（parts / ``mask.from_ellipse`` / 手点）补标。本模块：
  * 不引用 manifest_io / upgrade_core，不落任何文件；
  * 不判断哪只眼是真眼——候选超过两只时只写 warning，交给人挡挂件；
  * 坐标统一夹到 0..1，非法项丢弃并记 warning。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np

from ai_motion_contract import (AIGenError, AIMotionConfig, validate_config)
from ai_motion_provider import HttpMotionProvider, parse_json_content

MAX_SIDE = 1024
_JPEG_QUALITY = 88
REPORT_SCHEMA = "ai-annotate/1"

_SYSTEM = (
    "You locate facial and motion regions in a cut-out character image. "
    "Return ONLY a JSON object; no prose, no code fences."
)
_USER = (
    "Return candidate regions for manual annotation. Coordinates are "
    "normalized 0..1 with origin at top-left. "
    'Format: {"eyes":[{"cx":0.5,"cy":0.4,"rx":0.05,"ry":0.04}],'
    '"mouth":{"cx":0.5,"cy":0.6,"rx":0.08,"ry":0.03}|null,'
    '"hair":[{"cx":0.5,"cy":0.2,"rx":0.2,"ry":0.15}],'
    '"sway":{"top":null,"bottom":null}}'
    " Include decorative eye-like shapes as separate eye candidates; do NOT "
    "decide which eyes are real. Hair/sway regions are rough boxes for human "
    "confirmation only. If a region is uncertain, use null/empty array."
)


def _data_url(image_bytes: bytes) -> str:
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise AIGenError("无法读取图片")
    h, w = arr.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w, 1))
    if scale < 1.0:
        arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", arr,
                           [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise AIGenError("图片编码失败")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def build_messages(image_bytes: bytes, instruction: str = "") -> list[dict]:
    text = _USER
    instruction = (instruction or "").strip()[:500]
    if instruction:
        text = f'The user adds: "{instruction}". ' + text
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
        ]},
    ]


def _clamp(v: Any, lo: float = 0.0, hi: float = 1.0) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(x):
        return None
    return max(lo, min(hi, x))


def _region(raw: Any, warnings: list[str], label: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    vals = {k: _clamp(raw.get(k)) for k in ("cx", "cy", "rx", "ry")}
    if any(v is None for v in vals.values()):
        warnings.append(f"{label} 坐标不完整，已丢弃")
        return None
    if vals["rx"] <= 0 or vals["ry"] <= 0:
        warnings.append(f"{label} 半径非正，已丢弃")
        return None
    return {k: round(float(v), 4) for k, v in vals.items()}


def _regions(raw: Any, warnings: list[str], label: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for i, item in enumerate(raw):
        region = _region(item, warnings, f"{label}[{i}]")
        if region is not None:
            out.append(region)
    return out


@dataclass
class AnnotationReport:
    schema: str = REPORT_SCHEMA
    eyes: list[dict] = field(default_factory=list)
    mouth: dict | None = None
    hair: list[dict] = field(default_factory=list)
    sway: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "eyes": self.eyes,
            "mouth": self.mouth,
            "hair": self.hair,
            "sway": self.sway,
            "warnings": self.warnings,
        }


def parse_report(raw: str) -> AnnotationReport:
    """解析模型 JSON；只读，不判断真眼，不落盘。"""
    try:
        data = parse_json_content(raw)
    except Exception as exc:
        raise AIGenError(f"标注报告不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise AIGenError("标注报告必须是 JSON 对象")

    warnings: list[str] = []
    eyes = _regions(data.get("eyes"), warnings, "eyes")
    if len(eyes) > 2:
        warnings.append(
            f"检测到 {len(eyes)} 个眼部候选，请人工确认真眼与挂件；本报告不自动判定")
    mouth = _region(data.get("mouth"), warnings, "mouth")
    hair = _regions(data.get("hair"), warnings, "hair")
    sway_raw = data.get("sway") if isinstance(data.get("sway"), dict) else {}
    sway = {}
    for key in ("top", "bottom"):
        region = _region(sway_raw.get(key), warnings, f"sway.{key}")
        if region is not None:
            sway[key] = region
    return AnnotationReport(eyes=eyes, mouth=mouth, hair=hair, sway=sway,
                            warnings=warnings)


def routed_config(config: AIMotionConfig, agent_cfg=None,
                  task: str = "vision") -> AIMotionConfig:
    """按 ``routing.tasks.<task>.model`` 覆盖本次视觉模型；默认原样返回。

    base_url / key 仍来自 ``ai_motion.json``，只换模型名；不新增 host、
    不联网、不改全局配置。routing 关闭或该任务没写 model 时零变化。
    """
    if agent_cfg is None:
        try:
            from chat_contract import load_agent_config
        except ImportError:  # pragma: no cover
            from .chat_contract import load_agent_config
        agent_cfg = load_agent_config()
    try:
        from model_router import task_model
    except ImportError:  # pragma: no cover
        from .model_router import task_model
    model = task_model(agent_cfg, task)
    if not model or model == config.model:
        return config
    return replace(config, model=model)


def build_report(image_bytes: bytes, config: AIMotionConfig, *,
                 provider=None, instruction: str = "",
                 max_tokens: int = 1024) -> AnnotationReport:
    """调模型生成只读报告；缺配置 / 网络失败前置抛 AIGenError。"""
    issues = validate_config(config)
    if issues:
        raise AIGenError("；".join(issues))
    provider = provider or HttpMotionProvider()
    raw = provider.chat(config, build_messages(image_bytes, instruction),
                        max_tokens=max_tokens, json_mode=True)
    return parse_report(raw)


def format_report(report: AnnotationReport, width: int, height: int) -> str:
    """把 0..1 坐标同时写成归一化与像素值，方便人工照着手点。"""
    def _px(r: dict) -> str:
        return (f"cx={r['cx'] * width:.1f}, cy={r['cy'] * height:.1f}, "
                f"rx={r['rx'] * width:.1f}, ry={r['ry'] * height:.1f}")

    lines = [f"schema: {report.schema}", "eyes:"]
    if report.eyes:
        for i, r in enumerate(report.eyes):
            lines.append(f"  [{i}] norm=({r['cx']}, {r['cy']}, {r['rx']}, {r['ry']}) "
                         f"px=({_px(r)})")
    else:
        lines.append("  (none)")
    lines.append("mouth:")
    lines.append("  " + (_px(report.mouth) if report.mouth else "(none)"))
    lines.append("hair:")
    if report.hair:
        for i, r in enumerate(report.hair):
            lines.append(f"  [{i}] px=({_px(r)})")
    else:
        lines.append("  (none)")
    lines.append("sway:")
    for key in ("top", "bottom"):
        r = report.sway.get(key)
        lines.append(f"  {key}: " + (_px(r) if r else "(none)"))
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {w}" for w in report.warnings)
    return "\n".join(lines)
