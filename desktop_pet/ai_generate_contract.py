"""AI 生成候选的 Qt-free 契约：配置、请求、候选与统一入口。

这里不是第二条落盘路径：

- ``propose_candidates()`` 只出候选，不写盘；
- 候选转 ChangeSet 在 ``ai_generate_adapter``；
- 真正写盘仍只走 ``upgrade_core.commit_changes()``。

默认手动：``enabled=false`` 时零网络，直接拒绝。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

DEFAULT_KEY_ENV = "DESKTOP_PET_AI_GENERATE_KEY"
DEFAULT_TIMEOUT_MS = 15000
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class AIGenError(RuntimeError):
    """AI 生成失败：配置缺失 / 网络 / 协议 / 候选无效。入口应整体回手动。"""


@dataclass(frozen=True)
class AIGenConfig:
    """配置期 AI 候选的唯一真源；不写死 host / 模型 / key。"""

    enabled: bool = False
    provider: str = ""
    base_url: str = ""
    model: str = ""
    key_env: str = DEFAULT_KEY_ENV
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    upload_consent: bool = False
    cache_dir: str = ""
    thinking: bool = False


@dataclass
class AIGenRequest:
    """一次请求的全部输入；只带字节，不带本地路径 / key / 日志。"""

    sku: str
    stem: str
    image_bytes: bytes
    width: int = 0
    height: int = 0


@dataclass
class AIGenCandidate:
    """一个待人工确认的 AI 候选：软 alpha / 区域框 / 置信度 / 警告 / 出处类别。"""

    id: str
    alpha: np.ndarray | None = None          # HxW float32，0..1
    box: list[int] | None = None             # [x, y, w, h]，像素
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def default_config_path() -> Path:
    return Path(__file__).with_name("ai_generate.json")


def load_config(path=None) -> AIGenConfig:
    """读本地配置；文件不存在时返回「关闭」的默认值（默认手动）。"""
    p = Path(path) if path else default_config_path()
    if not p.exists():
        return AIGenConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                 # noqa: BLE001
        raise AIGenError(f"ai_generate.json 解析失败：{type(exc).__name__}") from exc
    if isinstance(data, dict) and isinstance(data.get("ai_generate"), dict):
        data = data["ai_generate"]
    if not isinstance(data, dict):
        raise AIGenError("ai_generate.json 顶层必须是对象")

    def _int(key: str, default: int) -> int:
        val = data.get(key, default)
        return val if isinstance(val, int) and not isinstance(val, bool) else default

    return AIGenConfig(
        enabled=bool(data.get("enabled", False)),
        provider=str(data.get("provider") or ""),
        base_url=str(data.get("base_url") or ""),
        model=str(data.get("model") or ""),
        key_env=str(data.get("key_env") or DEFAULT_KEY_ENV),
        timeout_ms=max(1, _int("timeout_ms", DEFAULT_TIMEOUT_MS)),
        max_image_bytes=max(1, _int("max_image_bytes", DEFAULT_MAX_IMAGE_BYTES)),
        upload_consent=bool(data.get("upload_consent", False)),
        cache_dir=str(data.get("cache_dir") or ""),
        thinking=bool(data.get("thinking", False)),
    )


def validate_config(cfg: AIGenConfig, env=None) -> list[str]:
    """请求前检查；返回空列表表示可以发请求。关闭时只返回一句提示。"""
    env = os.environ if env is None else env
    if not cfg.enabled:
        return ["enabled=false：只走手动切割（零网络）"]
    issues: list[str] = []
    if not cfg.base_url.strip():
        issues.append("缺 base_url")
    if not cfg.model.strip():
        issues.append("缺 model")
    if not cfg.upload_consent:
        issues.append("upload_consent=false：不发送原图")
    key_env = cfg.key_env or DEFAULT_KEY_ENV
    if not str(env.get(key_env, "")).strip():
        issues.append(f"环境变量 {key_env} 未设置")
    return issues


def ensure_ready(cfg: AIGenConfig, env=None) -> None:
    """发请求前的唯一闸门：未就绪直接抛错，保证零网络。"""
    issues = validate_config(cfg, env=env)
    if issues:
        raise AIGenError("；".join(issues))


def _candidate_ok(cand: AIGenCandidate, width: int, height: int) -> bool:
    alpha = cand.alpha
    if not isinstance(alpha, np.ndarray) or alpha.ndim != 2:
        return False
    if width and height and alpha.shape[:2] != (height, width):
        return False
    if alpha.size == 0 or not np.isfinite(alpha).all():
        return False
    if float(alpha.max()) <= 0.0:
        return False
    return True


def propose_candidates(provider, request: AIGenRequest, config: AIGenConfig,
                       env=None) -> list[AIGenCandidate]:
    """统一入口：配置闸门 → provider.propose → 候选有效性过滤。

    无效候选逐个跳过并记 warning；全部无效时整体失败（入口回手动）。
    """
    ensure_ready(config, env=env)
    if len(request.image_bytes or b"") > config.max_image_bytes:
        raise AIGenError("原图超过 max_image_bytes，未发起请求")

    raw = provider.propose(request, config)
    if not isinstance(raw, list):
        raise AIGenError("provider 返回值不是候选列表")

    width = request.width
    height = request.height
    if (not width or not height) and raw:
        first = raw[0].alpha
        if isinstance(first, np.ndarray) and first.ndim == 2:
            height, width = first.shape[:2]

    valid: list[AIGenCandidate] = []
    dropped = 0
    for cand in raw:
        if not isinstance(cand, AIGenCandidate) or not _candidate_ok(cand, width, height):
            dropped += 1
            continue
        valid.append(cand)
    if not valid:
        suffix = f"（跳过 {dropped} 个无效候选）" if dropped else ""
        raise AIGenError(f"没有可用候选{suffix}")
    if dropped:
        valid[0] = replace(valid[0], warnings=[
            *valid[0].warnings, f"另有 {dropped} 个候选因尺寸 / 全透明 / 损坏被跳过"])
    return valid
