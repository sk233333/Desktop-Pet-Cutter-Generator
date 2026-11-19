"""AI 动作参数 provider：FakeMotionProvider（测试）与 HttpMotionProvider。

约束：
- 只出 JSON 提案，不写盘、不改 manifest；
- 缺 key / 超时 / 429 / 5xx / 非 JSON / 响应结构不对一律抛 ``AIGenError``；
- 不重试、不打日志、不把 URL / 路径 / key 写进任何输出；
- 配置闸门由 ``ai_motion_contract.ensure_ready()`` 统一把守，本层再自查一次。

接口按 OpenAI 兼容的 ``chat/completions`` 组织；``base_url`` 可以写根地址
（自动补 ``/chat/completions``），也可以直接写完整端点。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ai_motion_contract import (AIGenError, AIMotionConfig, AIMotionRequest,
                                build_messages, parse_json_content,
                                resolve_api_key)


class FakeMotionProvider:
    """测试 / 离线 POC 用的确定性 provider；CI 永不连真实 API。"""

    name = "fake"

    def __init__(self, payload=None, fail: Exception | None = None):
        self._payload = payload
        self._fail = fail
        self.calls = 0

    def propose(self, request: AIMotionRequest, config: AIMotionConfig) -> dict:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        if self._payload is not None:
            return json.loads(json.dumps(self._payload))
        return {
            "anim": {
                "hair_speed": 1.2, "hair_amp": 1.1, "hair_stiff": 1.0,
                "hair_damp": 1.0, "sway_deg": 3.0, "sway_speed": 1.1,
                "bounce": 0.5, "fall": 1.0, "plat": 0.6,
            },
            "sway": {
                "hair": [{"amp": 1.2, "dir": None, "bias": 0.0}
                         for _ in range(request.hair_count)],
                "cloth": [{"amp": 1.0, "dir": None, "bias": 0.0}
                          for _ in range(request.cloth_count)],
            },
            "expressions": {
                "joy": {"lid_drop": 0.18, "lid_sag": 0.06,
                        "arc_ratio": 0.5, "blush_alpha": 72},
                "sleepy": {"lid_drop": 0.34, "lid_sag": 0.1,
                           "arc_ratio": 0.08, "blush_alpha": 18},
            },
        }


class HttpMotionProvider:
    """OpenAI 兼容 JSON 接口的最小适配；调用方注入 opener 便于测试拦截。"""

    name = "http"

    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen
        self.calls = 0

    def _guard(self, config: AIMotionConfig, env=None) -> str:
        if not config.base_url.strip() or not config.model.strip():
            raise AIGenError("配置缺 base_url 或 model")
        key = resolve_api_key(config, env=env)
        if not key:
            raise AIGenError("未填 API 密钥（模型接口弹窗或环境变量）")
        return key

    @staticmethod
    def _endpoint(base_url: str) -> str:
        url = base_url.strip().rstrip("/")
        return url if url.endswith("/chat/completions") \
            else url + "/chat/completions"

    def _post(self, config: AIMotionConfig, body: dict, env=None,
              timeout_cap: float | None = None) -> dict:
        """唯一的 HTTP 出口：鉴权、超时、状态码、JSON 顶层检查。"""
        key = self._guard(config, env=env)
        self.calls += 1
        req = urllib.request.Request(
            self._endpoint(config.base_url),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        timeout = max(0.1, float(config.timeout_ms) / 1000.0)
        if timeout_cap is not None:
            timeout = min(timeout, float(timeout_cap))
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
        if not isinstance(data, dict):
            raise AIGenError("响应顶层不是对象")
        return data

    @staticmethod
    def _content(data: dict) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIGenError("响应缺少 choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, list):
            # 少数服务把 content 拆成分段；只接受纯文本分段
            content = "".join(str(p.get("text", ""))
                              for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            reasoning = msg.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                raise AIGenError(
                    "模型只返回了思考内容（输出预算被用完），"
                    "请增大 max_tokens 或换非推理模型")
            raise AIGenError("响应缺少 message.content")
        return content

    def ping(self, config: AIMotionConfig, env=None) -> None:
        """连通性测试：只发一条极短消息，不校验输出内容。"""
        data = self._post(
            config,
            {"model": config.model,
             "messages": [{"role": "user", "content": "ping"}],
             "max_tokens": 8},
            env=env, timeout_cap=15.0)
        if not data.get("choices"):
            raise AIGenError("响应缺少 choices")

    @staticmethod
    def _apply_thinking(body: dict, config: AIMotionConfig) -> None:
        """LongCat 的思考开关是对象；其它服务沿用布尔值。"""
        if "longcat" in config.base_url.lower():
            body["thinking"] = {"type": "enabled" if config.thinking
                                else "disabled"}
        elif config.thinking:
            body["thinking"] = True

    def chat(self, config: AIMotionConfig, messages: list[dict], *,
             max_tokens: int = 512, json_mode: bool = False,
             env=None) -> str:
        """通用文本 / 视觉对话；返回 ``message.content`` 原文。"""
        body = {"model": config.model, "messages": messages,
                "temperature": 0.0, "max_tokens": int(max_tokens)}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        self._apply_thinking(body, config)
        return self._content(self._post(config, body, env=env))

    def propose(self, request: AIMotionRequest, config: AIMotionConfig) -> dict:
        body = {
            "model": config.model,
            "messages": build_messages(request),
            "temperature": float(config.temperature),
            "max_tokens": int(config.max_tokens),
        }
        if config.json_mode:
            body["response_format"] = {"type": "json_object"}
        self._apply_thinking(body, config)
        return parse_json_content(self._content(self._post(config, body)))
