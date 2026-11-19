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
