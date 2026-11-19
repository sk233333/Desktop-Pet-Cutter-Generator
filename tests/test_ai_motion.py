"""AI 动作参数 / 表情：契约、provider、adapter 与保护文件边界。

Qt-free：只依赖 numpy / cv2 / pytest；CI 永不连真实 API（Http 用假 opener）。
断言口径：AI 只给几何参数；未勾选的字段逐字节不变；落盘只走
``upgrade_core.commit_changes()``。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import upgrade_core                                          # noqa: E402
from ai_motion_adapter import (build_changeset, commit_selected,  # noqa: E402
                               request_from_item)
from ai_motion_contract import (ANIM_RANGES, AIGenError,      # noqa: E402
                                AIMotionConfig, ensure_ready, load_config,
                                parse_json_content, parse_proposal,
                                propose_motion, resolve_api_key, save_config,
                                validate_config)
from ai_motion_provider import FakeMotionProvider, HttpMotionProvider  # noqa: E402
from manifest_io import read_manifest, validate_manifest       # noqa: E402

W, H = 140, 120


def _item() -> dict:
    return {
        "stem": "pet", "width": W, "height": H,
        "eyes": [
            {"cx": 40, "cy": 40, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
            {"cx": 80, "cy": 42, "rx": 8, "ry": 6, "dark": [20, 20, 25]},
        ],
        "parts": [
            {"id": "eye_1", "eye_index": 0, "group": "g1",
             "mask": "pet_eyes.png", "channel": "R"},
            {"id": "eye_2", "eye_index": 1, "group": "g1",
             "mask": "pet_eyes.png", "channel": "G"},
        ],
        "anim": {"hair_speed": 1.0, "hair_amp": 1.0, "hair_stiff": 1.0,
                 "hair_damp": 1.0, "sway_deg": 2.4, "sway_speed": 1.0,
                 "bounce": 0.45, "fall": 1.0, "plat": 0.6},
        "hair": [
            {"mask": "pet_hair.png", "pivot": [70, 60], "tip": [70, 20],
             "amp": 1.0, "dir": None, "bias": 0.0},
            {"mask": "pet_hair.png", "pivot": [72, 62], "tip": [72, 22],
             "amp": 0.8, "dir": 90.0, "bias": 5.0},
        ],
        "cloth": [
            {"mask": "pet_cloth.png", "pivot": [70, 80], "tip": [70, 100],
             "amp": 1.0, "dir": None, "bias": 0.0},
        ],
    }


def _sku(d: Path, name: str = "demo") -> Path:
    d.mkdir()
    base = np.full((H, W, 3), 240, np.uint8)
    base[30:90, 50:100] = (210, 190, 175)
    cv2.imwrite(str(d / "pet.png"), base)
    mask = np.zeros((H, W), np.uint8)
    mask[34:46, 32:48] = 255
    mask[34:46, 72:88] = 255
    cv2.imwrite(str(d / "pet_eyes.png"), mask)
    manifest = {"schema_version": 2, "sku": name, "items": [_item()]}
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _info(d: Path) -> upgrade_core.SkuInfo:
    return next(s for s in upgrade_core.scan_skus(d.parent) if s.name == d.name)


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- 配置
def test_ai_motion_example_has_no_secret_and_is_gitignored():
    tpl = ROOT / "desktop_pet" / "ai_motion.json.example"
    text = tpl.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "base_url" in data and "model" in data
    assert data["key_env"] == "DESKTOP_PET_AI_MOTION_KEY"
    assert data["api_key"] == ""
    assert "sk-" not in text.lower()
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "desktop_pet/ai_motion.json" in ignore


def test_missing_key_blocks_before_network(monkeypatch, tmp_path):
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m")
    monkeypatch.delenv(cfg.key_env, raising=False)
    issues = validate_config(cfg)
    assert any("环境变量" in s for s in issues)
    with pytest.raises(AIGenError):
        ensure_ready(cfg)

    called = {"n": 0}

    def opener(*_a, **_k):
        called["n"] += 1
        raise AssertionError("不应发起网络请求")

    with pytest.raises(AIGenError):
        HttpMotionProvider(opener).propose(request_from_item("demo", "pet", _item()), cfg)
    assert called["n"] == 0


def test_load_config_nested_wrapper(tmp_path):
    p = tmp_path / "ai_motion.json"
    p.write_text(json.dumps({"ai_motion": {
        "base_url": "https://example.invalid/v1", "model": "m",
        "key_env": "K", "timeout_ms": 1234, "json_mode": False}}),
        encoding="utf-8")
    cfg = load_config(p)
    assert cfg.base_url.endswith("/v1") and cfg.model == "m"
    assert cfg.timeout_ms == 1234 and cfg.json_mode is False


def test_save_config_roundtrip_and_permissions(tmp_path):
    p = tmp_path / "ai_motion.json"
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m",
                         api_key="file-key", timeout_ms=4321)
    save_config(cfg, p)
    got = load_config(p)
    assert got.base_url == cfg.base_url and got.model == "m"
    assert got.api_key == "file-key" and got.timeout_ms == 4321
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_api_key_file_fallback_and_env_precedence(monkeypatch):
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m",
                         api_key="file-key")
    monkeypatch.delenv(cfg.key_env, raising=False)
    assert validate_config(cfg) == []
    assert resolve_api_key(cfg) == "file-key"
    monkeypatch.setenv(cfg.key_env, "env-key")
    assert resolve_api_key(cfg) == "env-key"


# ---------------------------------------------------------------- 严格 JSON
def test_parse_json_content_accepts_fence_and_rejects_garbage():
    assert parse_json_content('```json\n{"anim": {}}\n```') == {"anim": {}}
    assert parse_json_content('前缀 {"a": 1} 后缀') == {"a": 1}
    with pytest.raises(AIGenError):
        parse_json_content("not json at all")
    with pytest.raises(AIGenError):
        parse_json_content("")


def test_parse_proposal_clamps_and_fills_current():
    item = _item()
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal({
        "anim": {"sway_deg": 99, "bounce": -5, "plat": 500},
        "sway": {"hair": [{"amp": 9, "dir": -30, "bias": 100}],
                 "cloth": []},
        "expressions": {"joy": {"lid_drop": 9, "blush_alpha": -3},
                        "angry": {"lid_drop": 1}},
    }, req)
    assert prop.anim["sway_deg"] == ANIM_RANGES["sway_deg"][1]
    assert prop.anim["bounce"] == 0.0
    assert prop.anim["plat"] == ANIM_RANGES["plat"][1]
    # 未给出的 anim 键沿用当前值
    assert prop.anim["hair_speed"] == item["anim"]["hair_speed"]
    assert prop.sway["hair"][0]["amp"] == 3.0
    assert prop.sway["hair"][0]["dir"] == 330.0
    assert prop.sway["hair"][0]["bias"] == 45.0
    # 缺项按当前值补齐，数量与 manifest 对齐
    assert len(prop.sway["hair"]) == 2
    assert prop.sway["hair"][1]["amp"] == 0.8
    assert len(prop.sway["cloth"]) == 1
    # 表情只接受封闭白名单
    assert set(prop.expressions) == {"joy"}
    assert prop.expressions["joy"]["lid_drop"] == 0.45
    assert prop.expressions["joy"]["blush_alpha"] == 0.0
    assert any("angry" in w for w in prop.warnings)


def test_parse_proposal_rejects_non_object():
    with pytest.raises(AIGenError):
        parse_proposal([], request_from_item("demo", "pet", _item()))


# ---------------------------------------------------------------- provider
class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_http_provider_posts_chat_completions(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_AI_MOTION_KEY", "test-key")
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m")
    payload = {"anim": {"sway_deg": 3.0}}
    cap: dict = {}

    def opener(req, timeout=None):
        cap["url"] = req.full_url
        cap["headers"] = {k.lower(): v for k, v in req.header_items()}
        cap["body"] = json.loads(req.data.decode("utf-8"))
        content = json.dumps(payload)
        return _Resp(json.dumps(
            {"choices": [{"message": {"content": content}}]}).encode("utf-8"))

    out = HttpMotionProvider(opener).propose(
        request_from_item("demo", "pet", _item()), cfg)
    assert out == payload
    assert cap["url"] == "https://example.invalid/v1/chat/completions"
    assert cap["headers"]["authorization"] == "Bearer test-key"
    assert cap["body"]["model"] == "m"
    assert cap["body"]["response_format"] == {"type": "json_object"}
    assert cap["body"]["messages"][0]["role"] == "system"


def test_http_provider_ping_uses_short_request(tmp_path):
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m",
                         api_key="file-key")
    cap: dict = {}

    def opener(req, timeout=None):
        cap["url"] = req.full_url
        cap["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps(
            {"choices": [{"message": {"content": "pong"}}]}).encode("utf-8"))

    HttpMotionProvider(opener).ping(cfg)
    assert cap["url"] == "https://example.invalid/v1/chat/completions"
    assert cap["body"]["max_tokens"] == 8
    assert "response_format" not in cap["body"]


def test_http_provider_ping_requires_key(monkeypatch):
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m")
    monkeypatch.delenv(cfg.key_env, raising=False)
    with pytest.raises(AIGenError):
        HttpMotionProvider(lambda *_a, **_k: None).ping(cfg)


def test_longcat_sends_object_thinking_switch():
    cfg = AIMotionConfig(base_url="https://api.longcat.chat/openai/v1",
                         model="m", api_key="k", thinking=False)
    cap: dict = {}

    def opener(req, timeout=None):
        cap["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps(
            {"choices": [{"message": {"content": '{"box":[0,0,1,1]}'}}]}
        ).encode("utf-8"))

    HttpMotionProvider(opener).chat(cfg, [{"role": "user", "content": "hi"}])
    assert cap["body"]["thinking"] == {"type": "disabled"}

    HttpMotionProvider(opener).propose(
        request_from_item("demo", "pet", _item()),
        AIMotionConfig(base_url=cfg.base_url, model="m", api_key="k"))
    assert cap["body"]["thinking"] == {"type": "disabled"}


def test_other_provider_omits_thinking_when_disabled():
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m",
                         api_key="k", thinking=False)
    cap: dict = {}

    def opener(req, timeout=None):
        cap["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps(
            {"choices": [{"message": {"content": "ok"}}]}).encode("utf-8"))

    HttpMotionProvider(opener).chat(cfg, [{"role": "user", "content": "hi"}])
    assert "thinking" not in cap["body"]


def test_http_provider_reasoning_only_has_clear_error(monkeypatch):
    """推理模型把 token 用完时只回 reasoning_content，要给出可操作的提示。"""
    monkeypatch.delenv("DESKTOP_PET_AI_MOTION_KEY", raising=False)
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m",
                         api_key="k")
    payload = {"choices": [{"finish_reason": "length",
                            "message": {"role": "assistant",
                                        "reasoning_content": "thinking"}}]}
    provider = HttpMotionProvider(
        lambda *_a, **_k: _Resp(json.dumps(payload).encode("utf-8")))
    with pytest.raises(AIGenError) as ei:
        provider.propose(request_from_item("demo", "pet", _item()), cfg)
    assert "思考" in str(ei.value)


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("u", 429, "rate", None, None),
    urllib.error.URLError("down"),
    TimeoutError("slow"),
])
def test_http_provider_network_failures(monkeypatch, failure):
    monkeypatch.setenv("DESKTOP_PET_AI_MOTION_KEY", "k")
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m")

    def opener(*_a, **_k):
        raise failure

    with pytest.raises(AIGenError):
        HttpMotionProvider(opener).propose(
            request_from_item("demo", "pet", _item()), cfg)


def test_http_provider_bad_payloads(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_AI_MOTION_KEY", "k")
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m")
    req = request_from_item("demo", "pet", _item())

    def make(body: bytes):
        return HttpMotionProvider(lambda *_a, **_k: _Resp(body))

    with pytest.raises(AIGenError):
        make(b"not json").propose(req, cfg)
    with pytest.raises(AIGenError):
        make(json.dumps({"choices": []}).encode()).propose(req, cfg)
    with pytest.raises(AIGenError):
        make(json.dumps({"choices": [{"message": {"content": "nope"}}]}).encode()
             ).propose(req, cfg)


def test_fake_provider_through_gate(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_AI_MOTION_KEY", "k")
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m")
    req = request_from_item("demo", "pet", _item())
    prop = propose_motion(FakeMotionProvider(), req, cfg)
    assert set(prop.anim) == set(ANIM_RANGES)
    assert len(prop.sway["hair"]) == 2
    assert set(prop.expressions) == {"joy", "sleepy"}


# ---------------------------------------------------------------- adapter
def test_changeset_only_writes_selected(tmp_path):
    d = _sku(tmp_path / "demo")
    item = read_manifest(d / "manifest.json")["items"][0]
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal(FakeMotionProvider().propose(req, AIMotionConfig()),
                          req)

    cs = build_changeset(sku="demo", stem="pet", item_index=0, item=item,
                         proposal=prop,
                         selected={"anim/sway_deg", "sway/hair/1", "expr/joy"})
    assert list(cs.files) == ["pet_joy.png"]
    anim = cs.manifest_patch["items/0/anim"]
    assert anim["sway_deg"] == prop.anim["sway_deg"]
    assert anim["hair_speed"] == item["anim"]["hair_speed"]
    hair = cs.manifest_patch["items/0/hair"]
    assert hair[1]["amp"] == prop.sway["hair"][1]["amp"]
    assert hair[0] == item["hair"][0]           # 未勾选的区域逐字段不变
    assert "items/0/cloth" not in cs.manifest_patch
    slot = cs.manifest_patch["items/0/expr_overlays"]["joy"]
    assert slot["overlay"] == "pet_joy.png" and slot["source"] == "ai_motion"
    assert "sleepy" not in cs.manifest_patch["items/0/expr_overlays"]

    img = cv2.imdecode(np.frombuffer(cs.files["pet_joy.png"], np.uint8),
                       cv2.IMREAD_UNCHANGED)
    assert img.shape == (H, W, 4) and (img[..., 3] > 0).any()


def test_commit_selected_writes_and_validates(tmp_path):
    d = _sku(tmp_path / "demo")
    base_before = (d / "pet.png").read_bytes()
    mask_before = (d / "pet_eyes.png").read_bytes()
    item = read_manifest(d / "manifest.json")["items"][0]
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal(FakeMotionProvider().propose(req, AIMotionConfig()),
                          req)
    cs = build_changeset(sku="demo", stem="pet", item_index=0, item=item,
                         proposal=prop,
                         selected={"anim/bounce", "sway/cloth/0",
                                   "expr/joy", "expr/sleepy"})
    res = commit_selected(_info(d), cs)
    assert res.report.ok, res.report.text
    assert (d / "pet.png").read_bytes() == base_before
    assert (d / "pet_eyes.png").read_bytes() == mask_before

    after = read_manifest(d / "manifest.json")
    ok, msg = validate_manifest(after)
    assert ok, msg
    got = after["items"][0]
    assert got["anim"]["bounce"] == prop.anim["bounce"]
    assert got["cloth"][0]["amp"] == prop.sway["cloth"][0]["amp"]
    assert got["expr_overlays"]["joy"]["overlay"] == "pet_joy.png"
    assert got["expr_overlays"]["sleepy"]["overlay"] == "pet_sleepy.png"
    for name in ("pet_joy.png", "pet_sleepy.png"):
        img = cv2.imread(str(d / name), cv2.IMREAD_UNCHANGED)
        assert img is not None and (img[..., 3] > 0).any()


def test_dry_run_never_writes(tmp_path):
    d = _sku(tmp_path / "demo")
    item = read_manifest(d / "manifest.json")["items"][0]
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal(FakeMotionProvider().propose(req, AIMotionConfig()),
                          req)
    cs = build_changeset(sku="demo", stem="pet", item_index=0, item=item,
                         proposal=prop, selected={"expr/joy"})
    before = _snapshot(d)
    res = commit_selected(_info(d), cs, dry_run=True)
    assert res.report.ok and "dry-run" in res.report.text
    assert _snapshot(d) == before


def test_write_failure_rolls_back(tmp_path, monkeypatch):
    d = _sku(tmp_path / "demo")
    item = read_manifest(d / "manifest.json")["items"][0]
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal(FakeMotionProvider().propose(req, AIMotionConfig()),
                          req)
    cs = build_changeset(sku="demo", stem="pet", item_index=0, item=item,
                         proposal=prop, selected={"expr/joy", "anim/fall"})
    before = _snapshot(d)

    real = upgrade_core._atomic_write
    state = {"n": 0}

    def boom(path, data):
        state["n"] += 1
        if state["n"] == 2:            # PNG 成功，manifest 失败
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(upgrade_core, "_atomic_write", boom)
    res = commit_selected(_info(d), cs)
    assert not res.report.ok
    assert _snapshot(d) == before
    assert not (d / "pet_joy.png").exists()


def test_illegal_stem_rejected(tmp_path):
    item = read_manifest(_sku(tmp_path / "demo") / "manifest.json")["items"][0]
    req = request_from_item("demo", "pet", item)
    prop = parse_proposal({}, req)
    with pytest.raises(AIGenError):
        build_changeset(sku="demo", stem="../escape", item_index=0,
                        item=item, proposal=prop, selected={"anim/fall"})
    with pytest.raises(AIGenError):
        build_changeset(sku="demo", stem="pet", item_index=0, item=item,
                        proposal=prop, selected=set())


# ---------------------------------------------------------------- 边界
def test_expr_states_are_overlay_only():
    from expr_state import STATES, ExprBank, ExprState

    # joy / sleepy 不注册 STATES；模型吐这两个词只作为覆盖层候选
    assert STATES == ("normal", "blink", "half", "surprise", "wink_l",
                      "wink_r", "smile")
    assert "joy" not in ExprBank().patches
    assert ExprState().set("joy") == "normal"
    assert ExprState().set("sleepy") == "normal"


def test_overlay_keys_do_not_rebuild_alias():
    """覆盖层键只用于渲染取图，不得变回 ExprBank 的字符串别名。"""
    from chat_contract import derive_allowed
    from expr_state import STATES

    allowed = derive_allowed("x", STATES, [], None)
    assert allowed["emotion"] == ["normal", "half", "surprise", "wink_l",
                                  "wink_r", "smile"]
    assert "joy" not in allowed["emotion"]
    assert "sleepy" not in allowed["emotion"]

    src = (ROOT / "desktop_pet" / "pet_window.py").read_text(encoding="utf-8")
    assert '"smile": "joy"' in src and '"half": "sleepy"' in src
    assert "apply(self._render_rgba, overlay_key)" not in src
    assert "apply(self._render_rgba, state)" in src


def test_ai_cut_source_path_is_shared_and_commit_free(tmp_path):
    """手动 / AI 切割共用源图落盘；只写两个源文件，不碰 manifest。"""
    import inspect
    from launch import save_cut_source

    rgb = np.full((10, 10, 3), 200, np.uint8)
    mask = np.zeros((10, 10), np.uint8)
    mask[3:7, 3:7] = 1
    p1, m1 = save_cut_source(rgb, mask, (0, 0, 10, 10), tmp_path, "s")
    b1, mb1 = p1.read_bytes(), m1.read_bytes()
    p2, m2 = save_cut_source(rgb, mask, (0, 0, 10, 10), tmp_path, "s")
    assert p2.read_bytes() == b1 and m2.read_bytes() == mb1
    assert sorted(x.name for x in tmp_path.iterdir()) == ["s.png", "s_mask.png"]
    src = inspect.getsource(save_cut_source)
    assert "commit_changes" not in src and "manifest.json" not in src
    ui = (ROOT / "desktop_pet" / "ui_app.py").read_text(encoding="utf-8")
    assert "launch.save_cut_source(" in ui


def test_protected_files_unchanged_since_baseline():
    protected = ["desktop_pet/anim.py", "desktop_pet/arbiter.py",
                 "desktop_pet/manifest_io.py",
                 "desktop_pet/manifest.schema.json",
                 "desktop_pet/effect_table.json",
                 ".github/workflows/tests.yml"]
    try:
        rev = subprocess.run(["git", "rev-parse", "--verify", "7531ff3"],
                             cwd=ROOT, capture_output=True, text=True)
    except OSError:
        pytest.skip("git 不可用")
    if rev.returncode != 0:
        pytest.skip("基线 commit 7531ff3 不在当前仓库")
    diff = subprocess.run(["git", "diff", "--name-only", "7531ff3", "--",
                           *protected], cwd=ROOT, capture_output=True, text=True)
    assert diff.returncode == 0
    assert diff.stdout.strip() == ""
