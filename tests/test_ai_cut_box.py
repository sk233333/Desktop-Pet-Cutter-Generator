"""AI 识别抠图（视觉模型给框）的 Qt-free 测试；不连真实网络。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_cut_box import (build_messages, detect_box, outline_mask,  # noqa: E402
                        parse_box, split_edge_instruction, to_pixel_box)
from ai_motion_contract import AIGenError, AIMotionConfig  # noqa: E402


def _png(w=48, h=64) -> bytes:
    img = np.full((h, w, 3), 210, np.uint8)
    img[h // 3:2 * h // 3, w // 3:2 * w // 3] = (30, 80, 160)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


class _FakeVision:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None
        self.max_tokens = None

    def chat(self, config, messages, *, max_tokens=512, json_mode=False,
             env=None):
        self.messages = messages
        self.max_tokens = max_tokens
        return json.dumps(self.payload)


def test_build_messages_contains_jpeg_data_url():
    msgs = build_messages(_png())
    assert msgs[0]["role"] == "system"
    parts = msgs[1]["content"]
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_build_messages_includes_user_instruction():
    msgs = build_messages(_png(), instruction="只保留中间的人物")
    text = msgs[1]["content"][0]["text"]
    assert "只保留中间的人物" in text
    assert '{"box":[x0,y0,x1,y1]}' in text


def test_parse_box_normalized_and_pixel():
    assert parse_box('{"box":[0.1,0.2,0.5,0.8]}', 100, 100) == \
        pytest.approx((0.1, 0.2, 0.5, 0.8))
    # 像素坐标自动按原图尺寸换算
    assert parse_box('{"box":[10,20,50,80]}', 100, 100) == \
        pytest.approx((0.1, 0.2, 0.5, 0.8))
    # 颠倒的角点会纠正；fenced JSON 也能解
    got = parse_box('```json\n{"x0":0.8,"y0":0.7,"x1":0.2,"y1":0.1}\n```',
                    100, 100)
    assert got == pytest.approx((0.2, 0.1, 0.8, 0.7))


@pytest.mark.parametrize("text", [
    '{"box":null}',
    '{"box":[0.5,0.5,0.5,0.6]}',
    '{"box":[0.5,0.5,0.5001,0.9]}',
    'not json at all',
    '{"foo":[1,2,3,4]}',
])
def test_parse_box_rejects_bad_payload(text):
    with pytest.raises(AIGenError):
        parse_box(text, 100, 100)


def test_to_pixel_box_pads_and_clamps():
    assert to_pixel_box((0.25, 0.25, 0.75, 0.75), 100, 100) == (23, 23, 77, 77)
    assert to_pixel_box((0.0, 0.0, 1.0, 1.0), 64, 40) == (0, 0, 64, 40)
    with pytest.raises(AIGenError):
        to_pixel_box((0.5, 0.5, 0.51, 0.51), 20, 20)


def test_detect_box_sends_vision_message(monkeypatch):
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m",
                         api_key="k")
    monkeypatch.delenv(cfg.key_env, raising=False)
    fake = _FakeVision({"box": [0.1, 0.2, 0.9, 0.8]})
    got = detect_box(cfg, _png(), 48, 64, provider=fake)
    assert got == pytest.approx((0.1, 0.2, 0.9, 0.8))
    assert fake.messages and fake.messages[1]["content"][1]["type"] == "image_url"
    # 推理模型需要足够的输出预算，否则只返回 reasoning_content
    assert fake.max_tokens == 4096


def test_detect_box_forwards_instruction(monkeypatch):
    cfg = AIMotionConfig(base_url="https://example.invalid/v1", model="m",
                         api_key="k")
    monkeypatch.delenv(cfg.key_env, raising=False)
    fake = _FakeVision({"box": [0.1, 0.2, 0.9, 0.8]})
    detect_box(cfg, _png(), 48, 64, provider=fake,
               instruction="去掉左边的小猫")
    text = fake.messages[1]["content"][0]["text"]
    assert "去掉左边的小猫" in text


def test_split_edge_instruction():
    assert split_edge_instruction("不要外层白边，只要内层黑边") == ("", True)
    assert split_edge_instruction("只保留中间的兔子，去掉白边") == \
        ("只保留中间的兔子", True)
    assert split_edge_instruction("兔子") == ("兔子", False)
    assert split_edge_instruction("只要黑边") == ("", True)


def test_outline_mask_fills_inner_black_outline():
    img = np.full((200, 200, 3), 255, np.uint8)
    cv2.circle(img, (100, 100), 60, (255, 180, 200), -1)
    cv2.circle(img, (100, 100), 60, (0, 0, 0), 4)
    m = outline_mask(img, (30, 30, 170, 170))
    assert m[100, 100] == 1            # 内部保留
    assert m[5, 5] == 0               # 外层白边不要
    assert 0.15 < m.mean() < 0.40


def test_outline_mask_rejects_blank():
    img = np.full((100, 100, 3), 255, np.uint8)
    with pytest.raises(AIGenError):
        outline_mask(img, (10, 10, 90, 90))


def test_detect_box_requires_key(monkeypatch):
    cfg = AIMotionConfig(base_url="https://example.invalid", model="m")
    monkeypatch.delenv(cfg.key_env, raising=False)
    with pytest.raises(AIGenError):
        detect_box(cfg, _png(), 48, 64, provider=_FakeVision({"box": [0, 0, 1, 1]}))
