"""MCP / 办公事件桥的 Qt-free 断言：白名单、字段裁剪、超长/畸形、无写盘。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_stdio  # noqa: E402
from mcp_office import OfficeHost, normalize_notify  # noqa: E402


def _host(tmp_path=None, notify=None, allowed=None):
    man = None
    if tmp_path is not None:
        man = tmp_path / "manifest.json"
        man.write_text(json.dumps({
            "schema_version": 2,
            "items": [{"stem": "pet", "files": ["/abs/pet.png"],
                       "mouth_level": "smile",
                       "expr_overlays": {"joy": {"overlay": "a.png"}},
                       "smile_geometry": {"overlay": "s.png"}}],
        }), encoding="utf-8")
    return OfficeHost(
        allowed=allowed or {"emotion": ["normal", "surprise"],
                            "action": ["wave"]},
        sku="bgy", manifest_path=man, stem="pet",
        notify=notify, health={"chat_enabled": False})


def _call(mid, name, args=None):
    return {"id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}


def test_tools_list_is_exactly_the_whitelist():
    out = mcp_stdio.dispatch({"id": 1, "method": "tools/list"}, _host())
    names = [t["name"] for t in out["result"]["tools"]]
    assert names == ["get_health", "get_allowed", "get_manifest_item",
                     "propose_candidate", "notify_bubble"]
    assert "commit" not in " ".join(names)


def test_health_and_allowed_are_read_only():
    host = _host()
    health = mcp_stdio.dispatch(_call(1, "get_health"), host)["result"]
    assert health["ok"] is True and health["sku"] == "bgy"
    allowed = mcp_stdio.dispatch(_call(2, "get_allowed"), host)["result"]
    assert allowed == {"emotion": ["normal", "surprise"], "action": ["wave"]}


def test_manifest_item_only_returns_basenames(tmp_path):
    out = mcp_stdio.dispatch(_call(1, "get_manifest_item"), _host(tmp_path))
    item = out["result"]["item"]
    assert item["stem"] == "pet"
    assert item["files"] == ["pet.png"]          # 不回绝对路径
    assert item["expr_overlays"] == ["joy"]
    assert item["has_smile_geometry"] is True
    assert "manifest.json" not in json.dumps(item)


def test_manifest_item_missing_returns_none(tmp_path):
    host = _host(tmp_path)
    out = mcp_stdio.dispatch(_call(1, "get_manifest_item", {"stem": "nope"}),
                             host)
    assert out["result"]["item"] is None


def test_propose_candidate_is_normalized_and_not_persisted():
    out = mcp_stdio.dispatch(_call(1, "propose_candidate", {
        "text": "很长" * 40, "emotion": "angry", "action": "explode"}),
        _host())
    res = out["result"]
    assert res["persisted"] is False
    assert res["requires_user_confirm"] is True
    assert res["candidate"]["emotion"] == "normal"     # 越界归 normal
    assert res["candidate"]["action"] == "none"        # 越界归 none
    assert len(res["candidate"]["text"]) == 30


def test_notify_normalizes_and_reaches_the_sink():
    seen = []
    host = _host(notify=lambda t, e, h: seen.append((t, e, h)) or True)
    out = mcp_stdio.dispatch({
        "id": 1, "method": "tools/call",
        "params": {"name": "notify_bubble",
                   "arguments": {"text": "构建过了", "emotion": "joy",
                                 "mood_hint": "angry"}}}, host)
    assert out["result"]["delivered"] is True
    assert seen == [("构建过了", "normal", "angry")]   # 越界情绪归 normal


def test_normalize_notify_rejects_bad_text():
    assert normalize_notify({}, {}) is None
    assert normalize_notify({"text": 123}, {}) is None
    assert normalize_notify({"text": "   "}, {}) is None
    got = normalize_notify({"text": "x" * 500, "mood_hint": "nope"},
                           {"emotion": ["normal"]})
    assert got == {"text": "x" * 200, "emotion": "normal"}


def test_tools_call_without_arguments_defaults_to_empty():
    out = mcp_stdio.dispatch(
        {"id": 1, "method": "tools/call", "params": {"name": "get_health"}},
        _host())
    assert out["result"]["ok"] is True


def test_unknown_tool_and_method_are_rejected():
    with_err = mcp_stdio.handle_line(
        json.dumps(_call(1, "commit_changes")), _host())
    assert with_err["error"]["code"] == -32601
    with_err = mcp_stdio.handle_line(
        json.dumps({"id": 2, "method": "exec"}), _host())
    assert with_err["error"]["code"] == -32601


def test_malformed_and_overlong_do_not_crash():
    out = mcp_stdio.handle_line("{not json", _host())
    assert out["error"]["code"] == -32700
    out = mcp_stdio.handle_line("x" * (mcp_stdio.MAX_LINE + 10), _host())
    assert "64KB" in out["error"]["message"]


def test_run_stdio_handles_lines_and_eof():
    events = []
    host = _host(notify=lambda t, e, h: events.append(t) or True)
    fin = io.StringIO("\n".join([
        json.dumps({"id": 1, "method": "tools/list"}),
        "{bad json",
        json.dumps({"id": 2, "method": "notify",
                    "params": {"text": "任务完成"}}),
    ]) + "\n")
    fout = io.StringIO()
    mcp_stdio.run_stdio(host, fin, fout)
    lines = [json.loads(x) for x in fout.getvalue().splitlines()]
    assert lines[0]["result"]["tools"]
    assert lines[1]["error"]["code"] == -32700
    assert lines[2]["result"]["delivered"] is True
    assert events == ["任务完成"]


def test_idless_notify_success_sends_no_packet():
    events = []
    host = _host(notify=lambda t, e, h: events.append(t) or True)
    out = mcp_stdio.handle_line(
        json.dumps({"method": "notify", "params": {"text": "hi"}}), host)
    assert out is None and events == ["hi"]


def test_mcp_modules_have_no_write_or_exec_surface():
    for name in ("mcp_office.py", "mcp_stdio.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        for bad in ("commit_changes", "import subprocess", "import socket",
                    "from socket", "os.system", '"agent": 2'):
            assert bad not in src, name
        assert "open(" not in src, name


def test_pet_window_mcp_lands_only_on_arbiter():
    src = (ROOT / "pet_window.py").read_text(encoding="utf-8")
    start = src.index("    def _mcp_notify(")
    end = src.index("    # ---------- AI 对话", start)
    section = src[start:end]
    assert 'self.arbiter.request("interaction"' in section
    for bad in ("commit_changes", "_render_rgba =", "manifest.json"):
        assert bad not in section
    assert 'ap.add_argument("--mcp-stdio", action="store_true"' in src
