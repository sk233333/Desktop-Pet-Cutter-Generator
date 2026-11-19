"""W3 只读标注报告：不写盘、不改 schema、不自动判真眼。"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop_pet"))

from ai_annotate import (AnnotationReport, build_report, format_report,
                         parse_report)
from ai_motion_contract import AIGenError, AIMotionConfig


def _cfg(**kw):
    base = dict(base_url="https://example.test/v1", model="m", api_key="k")
    base.update(kw)
    return AIMotionConfig(**base)


class _Fake:
    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def chat(self, config, messages, *, max_tokens=512, json_mode=False,
             env=None):
        self.calls += 1
        assert json_mode is True
        return self.raw


def test_parse_report_keeps_all_eye_candidates_and_warns():
    raw = json.dumps({
        "eyes": [
            {"cx": 0.2, "cy": 0.3, "rx": 0.05, "ry": 0.04},
            {"cx": 0.8, "cy": 0.3, "rx": 0.05, "ry": 0.04},
            {"cx": 0.5, "cy": 0.7, "rx": 0.02, "ry": 0.02},
        ],
        "mouth": {"cx": 0.5, "cy": 0.6, "rx": 0.08, "ry": 0.03},
        "hair": [{"cx": 0.5, "cy": 0.2, "rx": 0.2, "ry": 0.15}],
        "sway": {"top": {"cx": 0.5, "cy": 0.1, "rx": 0.3, "ry": 0.1}},
    })
    r = parse_report(raw)
    assert isinstance(r, AnnotationReport)
    assert len(r.eyes) == 3                 # 不自动判真眼
    assert any("人工确认真眼" in w for w in r.warnings)
    assert r.mouth["cx"] == 0.5
    assert r.sway["top"]["cy"] == 0.1


def test_parse_report_clamps_and_drops_bad_regions():
    raw = json.dumps({
        "eyes": [{"cx": 1.4, "cy": -0.2, "rx": 2.0, "ry": 0.1},
                 {"cx": 0.1, "cy": 0.1}],
        "mouth": "bad",
        "hair": [],
        "sway": {},
    })
    r = parse_report(raw)
    assert r.eyes == [{"cx": 1.0, "cy": 0.0, "rx": 1.0, "ry": 0.1}]
    assert any("坐标不完整" in w for w in r.warnings)
    assert r.mouth is None


def test_parse_report_rejects_non_json():
    with pytest.raises(AIGenError):
        parse_report("not json")


def test_build_report_missing_config_fails_before_provider():
    fake = _Fake('{"eyes":[]}')
    with pytest.raises(AIGenError):
        build_report(b"not-an-image", AIMotionConfig(), provider=fake)
    assert fake.calls == 0


def test_build_report_uses_fake_provider_and_writes_nothing(tmp_path):
    img = np.full((8, 8, 3), 200, np.uint8)
    ok, buf = __import__("cv2").imencode(".png", img)
    assert ok
    fake = _Fake('{"eyes":[{"cx":0.5,"cy":0.5,"rx":0.1,"ry":0.1}],'
                 '"mouth":null,"hair":[],"sway":{}}')
    r = build_report(buf.tobytes(), _cfg(), provider=fake)
    assert fake.calls == 1
    assert len(r.eyes) == 1
    assert list(tmp_path.iterdir()) == []
    text = format_report(r, 200, 100)
    assert "eyes:" in text and "cx=100.0" in text


def test_annotate_module_is_report_only():
    src = (ROOT / "desktop_pet" / "ai_annotate.py").read_text(encoding="utf-8")
    for bad in ("commit_changes", "import manifest_io", "from manifest_io",
                "write_text", "imwrite", "os.replace"):
        assert bad not in src
    assert '"eyes"' in src and '"mouth"' in src
