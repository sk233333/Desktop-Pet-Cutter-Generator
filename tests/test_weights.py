"""v0.3：用户自备权重路径解析；只查本地路径，不获取、不加载模型。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import weights as w                                       # noqa: E402


def test_v03_weight_from_config_with_expanduser(tmp_path, monkeypatch):
    monkeypatch.delenv("DESKTOP_PET_SAM2_WEIGHT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "weights" / "sam2.pt"
    target.parent.mkdir()
    target.write_bytes(b"x")

    cfg = {"weights": {"sam2": "~/weights/sam2.pt"}}
    assert w.resolve_weight(cfg, "sam2") == target
    assert w.weight_available(cfg, "sam2") is True


def test_v03_weight_from_env_with_expanduser(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "dino.pth"
    target.write_bytes(b"x")
    monkeypatch.setenv("DESKTOP_PET_GROUNDING_DINO_WEIGHT", "~/dino.pth")

    assert w.resolve_weight({}, "grounding_dino") == target
    assert w.weight_available({}, "grounding_dino") is True


def test_v03_weight_config_takes_precedence_over_env(tmp_path, monkeypatch):
    target = tmp_path / "real.pt"
    target.write_bytes(b"x")
    monkeypatch.setenv("DESKTOP_PET_SAM2_WEIGHT", str(tmp_path / "missing.pt"))

    cfg = {"weights": {"sam2": str(target)}}
    assert w.resolve_weight(cfg, "sam2") == target


def test_v03_weight_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("DESKTOP_PET_SAM2_WEIGHT", raising=False)
    cfg = {"weights": {"sam2": str(tmp_path / "nope.pt")}}
    assert w.resolve_weight(cfg, "sam2") is None
    assert w.weight_available(cfg, "sam2") is False

    monkeypatch.setenv("DESKTOP_PET_SAM2_WEIGHT", str(tmp_path / "nope.pt"))
    assert w.resolve_weight({}, "sam2") is None

    assert w.resolve_weight({"weights": {"sam2": ""}}, "sam2") is None
    assert w.resolve_weight({}, "unknown") is None


def test_v03_describe_cuda_without_env(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert w.describe_cuda() == {"cuda_visible": False, "value": ""}


def test_v03_describe_cuda_with_env(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    assert w.describe_cuda() == {"cuda_visible": True, "value": "0,1"}


def test_v03_cache_dirs_are_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dirs = w.cache_dirs()
    assert dirs == [tmp_path / ".u2net",
                    tmp_path / ".cache" / "desktop_pet" / "weights"]
    assert all(isinstance(p, Path) for p in dirs)
    assert not any(p.exists() for p in dirs)


def test_v03_weights_source_has_no_fetch_or_ml_imports():
    src = (ROOT / "desktop_pet" / "weights.py").read_text(encoding="utf-8")
    for token in ("requests", "urllib.request", "download", "torch", "numpy"):
        assert token not in src


def test_v03_weights_do_not_write_into_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "sam2.pt"
    target.write_bytes(b"x")
    monkeypatch.setenv("DESKTOP_PET_SAM2_WEIGHT", "~/sam2.pt")
    assert w.resolve_weight({}, "sam2") == target

    repo = ROOT / "desktop_pet"
    assert not (repo / "sam2.pt").exists()
    assert not (repo / ".u2net").exists()
