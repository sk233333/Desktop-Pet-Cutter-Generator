"""AI 生成入口：配置 / provider / 失败回退（Qt-free，fake provider，不连真实 API）。

真实 API 永不进 CI：所有网络路径都用注入 opener 或 FakeProvider 截断。
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

from ai_generate_contract import (AIGenCandidate, AIGenConfig,  # noqa: E402
                                  AIGenError, AIGenRequest, load_config,
                                  propose_candidates)
from ai_generate_provider import FakeProvider, HttpAIProvider   # noqa: E402

BASE_COMMIT = "7531ff3"
TAG_COMMIT = "b03cdb7"          # tag v0.1 不得移动
PROTECTED = (
    "desktop_pet/anim.py",
    "desktop_pet/arbiter.py",
    "desktop_pet/expr_state.py",
    "desktop_pet/manifest_io.py",
    "desktop_pet/manifest.schema.json",
    "desktop_pet/effect_table.json",
)
GUI_TEST = "test_pet_window_mouth.py"


def _req(size=(8, 8), body=b"x") -> AIGenRequest:
    return AIGenRequest(sku="demo", stem="pet", image_bytes=body,
                        width=size[0], height=size[1])


def _cfg(**kw) -> AIGenConfig:
    base = dict(enabled=True, base_url="https://example.invalid/v1", model="m",
                key_env="AIG_TEST_KEY", upload_consent=True)
    base.update(kw)
    return AIGenConfig(**base)


# ------------------------------------------------------------------ 默认 / 配置
def test_aig_default_manual_zero_network(tmp_path):
    cfg = load_config(tmp_path / "not-exists.json")
    assert cfg.enabled is False and cfg.upload_consent is False

    fake = FakeProvider()
    with pytest.raises(AIGenError):
        propose_candidates(fake, _req(), cfg)
    assert fake.calls == 0, "默认手动时不得调用 provider（零网络）"


def test_aig_missing_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("AIG_TEST_KEY", raising=False)
    touched = []

    def opener(*_a, **_k):
        touched.append(1)
        raise AssertionError("缺 key 时不得发起网络请求")

    prov = HttpAIProvider(opener=opener)
    with pytest.raises(AIGenError) as ei:
        prov.propose(_req(), _cfg())
    assert "未设置" in str(ei.value)
    assert touched == [] and prov.calls == 0


def test_aig_upload_consent_off_fails_before_network(monkeypatch):
    monkeypatch.setenv("AIG_TEST_KEY", "k")
    touched = []
    prov = HttpAIProvider(opener=lambda *a, **k: touched.append(1))
    with pytest.raises(AIGenError) as ei:
        prov.propose(_req(), _cfg(upload_consent=False))
    assert "upload_consent" in str(ei.value)
    assert touched == []


def test_aig_example_has_no_model_id_host_or_key():
    text = (ROOT / "desktop_pet" / "ai_generate.json.example").read_text(
        encoding="utf-8")
    data = json.loads(text)
    assert data["enabled"] is False
    assert data["upload_consent"] is False
    assert data["thinking"] is False
    assert data["key_env"] == "DESKTOP_PET_AI_GENERATE_KEY"
    for key in ("provider", "base_url", "model", "cache_dir"):
        val = str(data[key])
        assert val.startswith("<") and val.endswith(">"), key
    assert "http://" not in text and "https://" not in text
    assert not re.search(r"sk-[A-Za-z0-9]", text)
    assert not re.search(r"(?i)gpt|claude|qwen|gemini|llama", text)


# ------------------------------------------------------------------ provider
def test_aig_fake_provider_returns_candidates():
    fake = FakeProvider()
    cands = fake.propose(_req((32, 24)), AIGenConfig(enabled=True))
    assert len(cands) == 1
    cand = cands[0]
    assert isinstance(cand, AIGenCandidate)
    assert cand.alpha.shape == (24, 32)
    assert cand.box and cand.confidence > 0


def test_aig_invalid_candidates_are_skipped(monkeypatch):
    monkeypatch.setenv("AIG_TEST_KEY", "k")
    h, w = 16, 16
    transparent = AIGenCandidate(id="empty", alpha=np.zeros((h, w), np.float32))
    wrong_size = AIGenCandidate(id="size", alpha=np.ones((h + 1, w), np.float32))
    good = AIGenCandidate(id="good", alpha=np.ones((h, w), np.float32) * 0.5)
    fake = FakeProvider(candidates=[transparent, wrong_size, good])
    out = propose_candidates(fake, _req((w, h)), _cfg())
    assert [c.id for c in out] == ["good"]
    assert any("跳过" in w for w in out[0].warnings)

    fake_all_bad = FakeProvider(candidates=[transparent])
    with pytest.raises(AIGenError):
        propose_candidates(fake_all_bad, _req((w, h)), _cfg())


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.mark.parametrize("case", ["429", "500", "timeout", "garbage",
                                  "no-candidates", "oversize"])
def test_aig_failure_matrix_returns_to_manual(case, monkeypatch):
    monkeypatch.setenv("AIG_TEST_KEY", "k")

    def opener(req, timeout=None):
        if case in ("429", "500"):
            raise urllib.error.HTTPError(req.full_url, int(case), "err", None, None)
        if case == "timeout":
            raise urllib.error.URLError("timed out")
        if case == "garbage":
            return _Resp(b"not json at all")
        return _Resp(b'{"candidates": []}')

    prov = HttpAIProvider(opener=opener)
    body = b"x" * (8 * 1024 * 1024 + 1) if case == "oversize" else b"x"
    with pytest.raises(AIGenError):
        propose_candidates(prov, _req(body=body), _cfg())
    if case == "oversize":
        assert prov.calls == 0, "超限图片应在发起网络前失败"


# ------------------------------------------------------------------ 仓库约束
def _git(*args, cwd=ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True)


def _require_git():
    if shutil.which("git") is None:
        pytest.skip("没有 git，跳过基线核对")
    probe = _git("rev-parse", "--git-dir")
    if probe.returncode != 0:
        pytest.skip("不是 git 工作树，跳过基线核对")


def test_aig_state_table_and_protected_files_unchanged():
    _require_git()
    head = _git("rev-parse", "--short", BASE_COMMIT)
    if head.returncode != 0:
        pytest.skip(f"本地没有基线 commit {BASE_COMMIT}")

    diff = _git("diff", "--name-only", BASE_COMMIT, "HEAD", "--",
                *PROTECTED, ".github/workflows/")
    assert diff.returncode == 0
    assert diff.stdout.strip() == "", f"保护文件出现 diff：{diff.stdout}"

    dirty = _git("status", "--porcelain", "--", *PROTECTED, ".github/workflows/")
    assert dirty.stdout.strip() == "", f"保护文件被改：{dirty.stdout}"

    tag = _git("rev-parse", "--short", "v0.1^{commit}")
    if tag.returncode == 0:
        assert tag.stdout.strip() == TAG_COMMIT, "tag v0.1 不得移动"


def test_aig_no_weights_no_new_deps():
    _require_git()
    tracked = _git("ls-files", "*.pt", "*.pth", "*.ckpt", "*.safetensors")
    assert tracked.stdout.strip() == "", "仓库不得出现权重文件"

    allowed = {
        "__future__", "ast", "base64", "json", "os", "urllib", "pathlib",
        "dataclasses", "datetime", "re", "sys", "numpy", "cv2", "PIL",
        "ai_generate_contract", "ai_generate_provider", "upgrade_core",
        "completers",
    }
    for name in ("ai_generate_contract.py", "ai_generate_provider.py",
                 "ai_generate_adapter.py"):
        src = (ROOT / "desktop_pet" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
        extra = roots - allowed
        assert not extra, f"{name} 引入未登记依赖：{sorted(extra)}"
        assert "PyQt6" not in roots


def test_aig_skip_policy_unchanged():
    needle = "from " + "PyQt6"
    gui = {p.name for p in (ROOT / "tests").glob("test_*.py")
           if needle in p.read_text(encoding="utf-8")}
    assert gui == {GUI_TEST}, f"GUI 测试文件应只有 {GUI_TEST}，实际 {sorted(gui)}"
