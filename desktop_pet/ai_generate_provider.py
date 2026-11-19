"""AI 生成 provider：FakeProvider（测试）与 HttpAIProvider（标准库 urllib）。

约束：
- 只出候选，不写盘；
- 缺 key / 未授权上传 / 超时 / 429 / 5xx / 非 JSON 一律抛 ``AIGenError``；
- 不重试、不打日志、不把 URL / 路径 / key / 原图名写进任何输出；
- 配置闸门由 ``ai_generate_contract.ensure_ready()`` 统一把守，本层再自查一次。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

import cv2
import numpy as np

from ai_generate_contract import (AIGenCandidate, AIGenConfig, AIGenError,
                                  AIGenRequest, DEFAULT_KEY_ENV)


class FakeProvider:
    """测试 / 离线 POC 用的确定性 provider；CI 永不连真实 API。"""

    name = "fake"

    def __init__(self, candidates=None, fail: Exception | None = None,
                 alpha_shape=(64, 64)):
        self._candidates = candidates
        self._fail = fail
        self._alpha_shape = alpha_shape
        self.calls = 0

    def propose(self, request: AIGenRequest, config: AIGenConfig) -> list[AIGenCandidate]:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        if self._candidates is not None:
            return list(self._candidates)
        h = int(request.height or self._alpha_shape[0])
        w = int(request.width or self._alpha_shape[1])
        alpha = np.zeros((h, w), np.float32)
        cv2.ellipse(alpha, (w // 2, h // 2),
                    (max(1, w // 4), max(1, h // 4)), 0, 0, 360, 1.0, -1)
        return [AIGenCandidate(
            id="fake-1", alpha=alpha,
            box=[w // 4, h // 4, max(1, w // 2), max(1, h // 2)],
            confidence=0.9, provenance={"category": "fake"})]


class HttpAIProvider:
    """OpenAI 风格 JSON 接口的最小适配；调用方注入 opener 便于测试拦截。"""

    name = "http"

    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen
        self.calls = 0

    def _guard(self, request: AIGenRequest, config: AIGenConfig) -> str:
        if not config.enabled:
            raise AIGenError("AI 生成未启用（默认手动，零网络）")
        if not config.upload_consent:
            raise AIGenError("upload_consent=false：不发送原图")
        if not config.base_url.strip() or not config.model.strip():
            raise AIGenError("配置缺 base_url 或 model")
        key_env = config.key_env or DEFAULT_KEY_ENV
        key = str(os.environ.get(key_env, "")).strip()
        if not key:
            raise AIGenError(f"环境变量 {key_env} 未设置")
        if len(request.image_bytes or b"") > config.max_image_bytes:
            raise AIGenError("原图超过 max_image_bytes")
        return key

    def propose(self, request: AIGenRequest, config: AIGenConfig) -> list[AIGenCandidate]:
        key = self._guard(request, config)
        self.calls += 1

        body = {
            "model": config.model,
            "sku": request.sku,
            "stem": request.stem,
            "response_format": "candidates",
            "image_b64": base64.b64encode(request.image_bytes or b"").decode("ascii"),
        }
        req = urllib.request.Request(
            config.base_url.strip().rstrip("/") + "/ai/candidates",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        timeout = max(0.1, float(config.timeout_ms) / 1000.0)
        try:
            with self._opener(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise AIGenError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AIGenError("网络请求失败或超时") from exc

        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise AIGenError("响应不是 JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
            raise AIGenError("响应缺少 candidates 数组")

        out: list[AIGenCandidate] = []
        for i, raw_c in enumerate(data["candidates"]):
            cand = _decode_candidate(raw_c, i)
            if cand is not None:
                out.append(cand)
        if not out:
            raise AIGenError("响应里没有可用候选")
        return out


_LOCAL_SESSIONS: dict[str, object] = {}


class LocalMattingProvider:
    """进程内本地分割（rembg）；零网络、不读 key、不上传。

    依赖与模型都不进仓库：``rembg`` 只在真正调用时惰性导入，CI 五包不装；
    模型文件由 rembg 下载到用户缓存目录（默认 ``~/.u2net``）。
    首次加载较慢，调用方应放到后台线程。
    """

    name = "local_rembg"
    default_model = "isnet-general-use"

    def __init__(self, model: str | None = None):
        self.model = model or self.default_model
        self.calls = 0

    def _guard(self, request: AIGenRequest, config: AIGenConfig) -> None:
        if not config.enabled:
            raise AIGenError("AI 生成未启用（默认手动，零网络）")
        if config.transport != "local":
            raise AIGenError("配置不是本地模式（transport != local）")
        if len(request.image_bytes or b"") > config.max_image_bytes:
            raise AIGenError("原图超过 max_image_bytes")

    def _session(self):
        model = self.model
        if model not in _LOCAL_SESSIONS:
            from rembg import new_session            # 惰性：CI 不装
            _LOCAL_SESSIONS[model] = new_session(model)
        return _LOCAL_SESSIONS[model]

    def propose(self, request: AIGenRequest, config: AIGenConfig) -> list[AIGenCandidate]:
        self._guard(request, config)
        try:
            from rembg import remove
        except ImportError as exc:
            raise AIGenError("未安装本地分割依赖 rembg（可选安装，不进仓库）") from exc
        try:
            raw = remove(request.image_bytes, session=self._session(),
                         only_mask=True)
        except AIGenError:
            raise
        except Exception as exc:                 # noqa: BLE001
            raise AIGenError("本地推理失败") from exc
        self.calls += 1

        if isinstance(raw, bytes):
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        else:
            img = np.asarray(raw)
        if img is None or img.size == 0:
            raise AIGenError("本地分割没有返回可用掩膜")
        alpha = img.astype(np.float32)
        if alpha.max() > 1.0:
            alpha /= 255.0
        if request.width and request.height \
                and alpha.shape[:2] != (request.height, request.width):
            alpha = cv2.resize(alpha, (request.width, request.height),
                               interpolation=cv2.INTER_LINEAR)
        alpha = np.clip(alpha, 0.0, 1.0)
        if float(alpha.max()) <= 0.0:
            raise AIGenError("本地分割结果全透明")
        return [AIGenCandidate(id="local-1", alpha=alpha, box=None,
                               confidence=0.0,
                               provenance={"category": "local"})]


def _decode_candidate(raw, index: int) -> AIGenCandidate | None:
    """单条候选容错解析；损坏 / 尺寸不明的候选返回 None（由上层整体判定）。"""
    if not isinstance(raw, dict):
        return None
    alpha = None
    b64 = raw.get("alpha_png_b64") or raw.get("alpha_png")
    if isinstance(b64, str) and b64:
        try:
            buf = np.frombuffer(base64.b64decode(b64, validate=True), np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        except Exception:                    # noqa: BLE001
            img = None
        if img is not None:
            arr = img.astype(np.float32)
            if arr.ndim == 3:
                alpha = arr[..., 3] if arr.shape[2] >= 4 else arr[..., 0]
            elif arr.ndim == 2:
                alpha = arr
            if alpha is not None and float(np.nanmax(alpha)) > 1.0:
                alpha = alpha / 255.0
            if alpha is not None:
                alpha = np.clip(alpha, 0.0, 1.0)
    if alpha is None:
        return None

    box = raw.get("box")
    if not (isinstance(box, list) and len(box) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in box)):
        box = None
    warnings = raw.get("warnings")
    warnings = [str(w) for w in warnings] if isinstance(warnings, list) else []
    try:
        conf = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return AIGenCandidate(
        id=str(raw.get("id") or f"cand-{index + 1}"), alpha=alpha, box=box,
        confidence=max(0.0, min(1.0, conf)), warnings=warnings,
        provenance={"category": "http"})
