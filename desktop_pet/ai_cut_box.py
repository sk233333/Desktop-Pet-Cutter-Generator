"""AI 抠图的第一步：视觉模型只给前景框，像素级掩膜仍由本地 GrabCut/SAM 完成。

这样普通 OpenAI 兼容的「看图」模型就能参与抠图，不要求服务端返回 mask。
图片会以 data URL 发给模型接口（与 AI 动作参数共用 ``ai_motion.json``）；
本模块不打印图片内容、URL、密钥，也不写盘。
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import replace

import cv2
import numpy as np

from ai_motion_contract import AIGenError, load_config, validate_config
from ai_motion_provider import HttpMotionProvider

MAX_SIDE = 1024
_JPEG_QUALITY = 88

# 这些是“边缘怎么切”的要求，框模型做不到；拆出来交给本地黑边填充
EDGE_HINTS = ("白边", "黑边", "描边", "贴纸边", "内层边", "外轮廓")


def split_edge_instruction(text: str) -> tuple[str, bool]:
    """把「只要内层黑边/不要白边」从主体描述里拆出去。

    返回 (主体描述, 是否需要贴黑边)。按标点切句，含边缘关键词的整句丢弃；
    例：「只保留兔子，去掉白边」→ ("只保留兔子", True)。
    """
    text = (text or "").strip()
    wants = any(h in text for h in EDGE_HINTS)
    if not wants:
        return text, False
    keep = []
    for clause in re.split(r"[，,。；;、\n]+", text):
        clause = clause.strip()
        if clause and not any(h in clause for h in EDGE_HINTS):
            keep.append(clause)
    return "，".join(keep)[:500], True

_SYSTEM = (
    "You locate the main subject in an image for background removal. "
    "Return ONLY a JSON object with normalized coordinates, no prose, no code fences."
)

_USER = (
    "Find the tight bounding box of the single main subject "
    "(figure, toy, pet, product) and exclude background, text, hands, "
    "shadows and props. Coordinates are normalized 0..1 with origin at top-left. "
    'Return {"box":[x0,y0,x1,y1]}. '
    'If there is no clear single subject, return {"box":null}.'
)


def _data_url(image_bytes: bytes) -> str:
    """缩小到最长边 1024 再转 JPEG data URL，控制请求体积。"""
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise AIGenError("无法读取图片")
    h, w = arr.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w, 1))
    if scale < 1.0:
        arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", arr,
                           [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise AIGenError("图片编码失败")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def build_messages(image_bytes: bytes, instruction: str = "") -> list[dict]:
    """OpenAI 视觉消息格式：文本（可附用户目标）+ image_url(data URL)。"""
    text = _USER
    instruction = (instruction or "").strip()[:500]
    if instruction:
        # 只要框，不要精细分割：先保证主体完整，别为了排除装饰把腿/头发截掉
        text = (f'The user specifically wants: "{instruction}". '
                "Return a box that FULLY contains that requested content from "
                "head to toe, including legs, feet, hair and accessories "
                "(do not crop any part of it). If it is a character, animal "
                "or person, never return a box that covers only the head or "
                "face. It is OK if nearby decorations are also inside the "
                "box. Coordinates are normalized 0..1 "
                "with origin at top-left. Return {\"box\":[x0,y0,x1,y1]}. "
                "If the requested content is absent, return {\"box\":null}.")
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": _data_url(image_bytes)}},
        ]},
    ]


def _load_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    try:
        obj = json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise AIGenError("模型没有返回 JSON") from None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            raise AIGenError("模型返回的 JSON 无法解析") from None
    if not isinstance(obj, dict):
        raise AIGenError("模型返回的 JSON 不是对象")
    return obj


def parse_box(text: str, width: int, height: int) -> tuple[float, float, float, float]:
    """解析归一化框；兼容像素框，最后夹到 0..1 并保证 x0<x1 / y0<y1。"""
    obj = _load_json(text)
    raw = obj.get("box")
    if raw is None and all(k in obj for k in ("x0", "y0", "x1", "y1")):
        raw = [obj["x0"], obj["y0"], obj["x1"], obj["y1"]]
    if raw is None:
        raise AIGenError("模型没有识别到主体")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise AIGenError("模型返回的 box 不是四个数")
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        raise AIGenError("模型返回的 box 不是数字") from None

    # 明显是像素坐标（>1.5）时按原图尺寸换算；归一化坐标原样使用
    if max(vals) > 1.5:
        vals = [vals[0] / max(width, 1), vals[1] / max(height, 1),
                vals[2] / max(width, 1), vals[3] / max(height, 1)]

    x0, x1 = sorted((max(0.0, min(1.0, vals[0])),
                     max(0.0, min(1.0, vals[2]))))
    y0, y1 = sorted((max(0.0, min(1.0, vals[1])),
                     max(0.0, min(1.0, vals[3]))))
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise AIGenError("模型给的框太小或为空")
    return x0, y0, x1, y1


def to_pixel_box(box: tuple[float, float, float, float],
                 width: int, height: int, pad: float = 0.04) -> tuple[int, int, int, int]:
    """归一化框 → 像素框，四周留一点背景给 GrabCut。"""
    x0, y0, x1, y1 = box
    pad_x = max(2, int(round((x1 - x0) * width * pad)))
    pad_y = max(2, int(round((y1 - y0) * height * pad)))
    X0 = max(0, int(round(x0 * width)) - pad_x)
    Y0 = max(0, int(round(y0 * height)) - pad_y)
    X1 = min(width, int(round(x1 * width)) + pad_x)
    Y1 = min(height, int(round(y1 * height)) + pad_y)
    if X1 - X0 < 8 or Y1 - Y0 < 8:
        raise AIGenError("AI 框太小，无法分割")
    return X0, Y0, X1, Y1


def outline_mask(rgb: np.ndarray, box, *, dark_threshold: int = 100,
                 close_px: int = 5) -> np.ndarray:
    """沿主体内层黑色描边填充掩膜，用于去掉贴纸外层白边。

    只取 box（略留边）内的最大暗色闭合轮廓并填满。找不到可靠描边时抛
    ``AIGenError``，调用方回退到 SAM/GrabCut。
    """
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in box]
    pad = max(8, int(round(max(x1 - x0, y1 - y0) * 0.02)))
    X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
    X1, Y1 = min(w, x1 + pad), min(h, y1 + pad)
    roi = rgb[Y0:Y1, X0:X1]
    if roi.size == 0:
        raise AIGenError("描边区域为空")
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    dark = (gray < int(dark_threshold)).astype(np.uint8) * 255
    k = max(1, int(close_px))
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE,
                            np.ones((k, k), np.uint8))
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise AIGenError("没有找到黑色描边")
    main = max(cnts, key=cv2.contourArea)
    ratio = cv2.contourArea(main) / float(roi.shape[0] * roi.shape[1])
    if ratio < 0.005:
        raise AIGenError("黑色描边围成的区域太小")
    m = np.zeros(gray.shape, np.uint8)
    cv2.drawContours(m, [main], -1, 1, -1)   # 与 estimate_mask 一样用 0/1
    out = np.zeros((h, w), np.uint8)
    out[Y0:Y1, X0:X1] = m
    return out


def detect_box(config, image_bytes: bytes, width: int, height: int,
               provider=None, instruction: str = "") -> tuple[float, float, float, float]:
    """调一次视觉模型拿归一化前景框；缺配置 / 网络 / 解析失败都抛 AIGenError。"""
    issues = validate_config(config)
    if issues:
        raise AIGenError("；".join(issues))
    # 推理型模型会先输出 reasoning_content；预算太小会只思考不回答。
    # 复杂图可能思考 3000+ tokens，给到 4096 并放宽到至少 90s。
    cfg = replace(config, timeout_ms=max(90000, int(config.timeout_ms)))
    text = (provider or HttpMotionProvider()).chat(
        cfg, build_messages(image_bytes, instruction=instruction),
        max_tokens=max(4096, int(config.max_tokens)))
    return parse_box(text, width, height)


def detect_box_from_file(path, config=None, provider=None,
                         instruction: str = ""):
    """便捷入口：读本地图片 → 归一化框。"""
    cfg = config or load_config()
    data = path.read_bytes()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise AIGenError("无法读取图片")
    h, w = arr.shape[:2]
    return detect_box(cfg, data, w, h, provider=provider,
                      instruction=instruction)
