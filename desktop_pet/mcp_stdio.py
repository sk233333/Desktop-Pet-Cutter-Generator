#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 stdio 窄协议：一行 JSON 进、一行 JSON 出（Qt-free、零新依赖）。

只实现 MCP 里与桌宠同构的三类消息，方向无关：

- `{"id":1,"method":"tools/list"}` → 白名单工具表；
- `{"id":2,"method":"tools/call","params":{"name":...,"arguments":{}}}`；
- `{"method":"notify","params":{"text":...,"emotion":...}}`（无 id 不回包）。

安全边界：默认不启动（只有桌宠显式带 `--mcp-stdio` 才跑）；没有 socket、
不监听端口；超长行 / 畸形 JSON / 未知工具 / 未知方法一律错误回包，
不崩、不落盘。真正的落点由 `OfficeHost` 注入，本模块不碰 Qt / arbiter。
"""

from __future__ import annotations

import json

try:
    from .mcp_office import TOOL_NAMES
except ImportError:  # 直接以脚本 / 平铺 sys.path 运行时
    from mcp_office import TOOL_NAMES

MAX_LINE = 64 * 1024
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(mid, code: int, message: str) -> dict:
    return {"id": mid, "error": {"code": code, "message": message}}


def _call_tool(name: str, args, host) -> dict:
    if not isinstance(args, dict):
        raise ProtocolError(_INVALID_PARAMS, "arguments 必须是对象")
    if name == "get_health":
        return host.get_health()
    if name == "get_allowed":
        return host.get_allowed()
    if name == "get_manifest_item":
        stem = args.get("stem")
        if stem is not None and not isinstance(stem, str):
            raise ProtocolError(_INVALID_PARAMS, "stem 必须是字符串")
        return {"item": host.get_manifest_item(stem)}
    if name == "propose_candidate":
        return host.propose_candidate(args)
    if name == "notify_bubble":
        return host.notify_bubble(args.get("text"), args.get("emotion"),
                                  args.get("mood_hint"))
    raise ProtocolError(_METHOD_NOT_FOUND, f"未知工具：{name}")


def dispatch(msg, host) -> dict:
    """处理一条已解析消息；协议错误抛 `ProtocolError`。"""
    if not isinstance(msg, dict):
        raise ProtocolError(_INVALID_REQUEST, "顶层必须是对象")
    mid = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError(_INVALID_REQUEST, "缺少 method")
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError(_INVALID_PARAMS, "params 必须是对象")

    if method == "tools/list":
        return {"id": mid, "result": {"tools": host.tool_specs()}}
    if method == "notify":
        result = host.notify_bubble(params.get("text"), params.get("emotion"),
                                    params.get("mood_hint"))
        return {"id": mid, "result": result}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_NAMES:
            raise ProtocolError(_METHOD_NOT_FOUND, f"未知工具：{name}")
        args = params.get("arguments")
        if args is None:                       # JSON-RPC 允许省略 arguments
            args = {}
        return {"id": mid, "result": _call_tool(name, args, host)}
    raise ProtocolError(_METHOD_NOT_FOUND, f"未知方法：{method}")


def handle_line(line: str, host) -> dict | None:
    """解析并处理一行；无 id 的通知成功时不回包，失败仍回错误。"""
    if len(line.encode("utf-8")) > MAX_LINE:
        return _error(None, _INVALID_REQUEST, "消息超过 64KB")
    try:
        msg = json.loads(line)
    except (TypeError, ValueError):
        return _error(None, _PARSE_ERROR, "不是合法 JSON")
    try:
        out = dispatch(msg, host)
    except ProtocolError as exc:
        mid = msg.get("id") if isinstance(msg, dict) else None
        return _error(mid, exc.code, exc.message)
    except Exception:
        mid = msg.get("id") if isinstance(msg, dict) else None
        return _error(mid, _INTERNAL_ERROR, "内部错误")
    if isinstance(msg, dict) and "id" not in msg:
        return None
    return out


def run_stdio(host, fin, fout) -> None:
    """阻塞读 stdin 行、写 stdout；EOF / 关窗时自然结束，不抛异常。"""
    for line in fin:
        if not line or not line.strip():
            continue
        out = handle_line(line, host)
        if out is None:
            continue
        try:
            fout.write(json.dumps(out, ensure_ascii=False) + "\n")
            fout.flush()
        except (OSError, ValueError):
            return
