"""v0.3：外部办公事件适配层默认关闭、零网络、载荷最小化。

全部 Qt-free；poster 由测试注入，不发起任何真实连接。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

import cloud_office as co                                 # noqa: E402


def _ready_cfg(**overrides):
    base = {"enabled": True, "consent": True,
            "base_url": "https://office.example/api",
            "key_env": "DESKTOP_PET_OFFICE_KEY", "max_text": 400}
    base.update(overrides)
    return base


# ---------------------------------------------------------------- 默认关闭
def test_v03_cloud_default_config_is_disabled_and_never_posts(monkeypatch):
    monkeypatch.delenv("DESKTOP_PET_OFFICE_KEY", raising=False)
    conf = co.CloudConfig.from_dict({})
    assert conf.enabled is False
    assert conf.consent is False
    assert conf.base_url == ""
    assert conf.key_env == ""
    assert conf.max_text == 400
    assert co.cloud_ready(conf) is False

    posted = []
    logs = []
    got = co.send_event(conf, {"kind": "mail", "text": "hi"},
                        poster=posted.append, logger=logs.append)
    assert got == {"delivered": False, "reason": "disabled"}
    assert posted == []
    assert logs == ["cloud_event disabled"]


def test_v03_cloud_consent_false_does_not_send(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_OFFICE_KEY", "secret-value")
    conf = co.CloudConfig.from_dict(_ready_cfg(consent=False))
    assert co.cloud_ready(conf) is False

    posted = []
    got = co.send_event(conf, {"kind": "mail", "text": "hi"},
                        poster=posted.append)
    assert got == {"delivered": False, "reason": "not_ready"}
    assert posted == []


# ---------------------------------------------------------------- 就绪条件
def test_v03_cloud_requires_https_and_key_env(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_OFFICE_KEY", "secret-value")
    assert co.cloud_ready(co.CloudConfig.from_dict(
        _ready_cfg(base_url="http://office.example"))) is False

    monkeypatch.delenv("DESKTOP_PET_OFFICE_KEY", raising=False)
    assert co.cloud_ready(co.CloudConfig.from_dict(
        _ready_cfg(key_env=""))) is False
    assert co.cloud_ready(co.CloudConfig.from_dict(
        _ready_cfg(key_env="DESKTOP_PET_MISSING_KEY"))) is False


# ---------------------------------------------------------------- 载荷
def test_v03_cloud_ready_payload_is_minimal_and_truncated(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_OFFICE_KEY", "secret-value")
    conf = co.CloudConfig.from_dict(_ready_cfg(max_text=5))
    assert co.cloud_ready(conf) is True

    posted = []
    got = co.send_event(
        conf,
        {"kind": "mail", "text": "abcdefgh", "emotion": "smile",
         "action": "send_email", "confirm": True, "url": "https://x.invalid"},
        poster=posted.append)
    assert got == {"delivered": True, "reason": "ok"}
    assert len(posted) == 1
    assert set(posted[0]) == {"kind", "text", "emotion"}
    assert posted[0] == {"kind": "mail", "text": "abcde", "emotion": "smile"}


def test_v03_cloud_poster_failure_reports_post_failed(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_OFFICE_KEY", "secret-value")
    conf = co.CloudConfig.from_dict(_ready_cfg())
    logs = []

    def boom(payload):
        raise RuntimeError("post failed")

    got = co.send_event(conf, {"kind": "mail", "text": "hi"},
                        poster=boom, logger=logs.append)
    assert got == {"delivered": False, "reason": "post_failed"}
    assert logs == ["cloud_event post_failed"]


# ---------------------------------------------------------------- 脱敏
def test_v03_cloud_redact_removes_url_path_and_key():
    raw = ("goto https://office.example/a?token=1 and "
           "/home/alice/secret.txt and ~/private/notes.md "
           "key sk-live-abc123")
    out = co.redact(raw)
    assert "[redacted]" in out
    assert "http" not in out
    assert "/home" not in out
    assert "~/" not in out
    assert "sk-live-abc123" not in out
    assert out.count("[redacted]") == 4


def test_v03_cloud_logger_only_gets_redacted_categories(monkeypatch):
    monkeypatch.setenv("DESKTOP_PET_OFFICE_KEY", "secret-value")
    conf = co.CloudConfig.from_dict(_ready_cfg())
    logs = []
    co.send_event(conf, {"kind": "mail", "text": "hi",
                         "url": "https://office.example/secret"},
                  poster=lambda payload: None, logger=logs.append)
    assert logs == ["cloud_event ok"]
    assert all(co.redact(line) == line for line in logs)


# ---------------------------------------------------------------- 源码
def test_v03_cloud_source_has_no_network_server_tokens():
    src = (ROOT / "desktop_pet" / "cloud_office.py").read_text(encoding="utf-8")
    for token in ("socket", "bind", "listen", "0.0.0.0", "http.server"):
        assert token not in src
