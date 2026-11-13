"""pytest 公共装置：合成一只眼睛，避免依赖具体素材。

仓库里带眼眶 mask 的素材是手工标注的，位置/档位会随素材变；
半眼与表情的形态学断言要用「已知结构」的眼睛来验，所以这里造一只。
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "desktop_pet") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop_pet"))

H = W = 120
IRIS_RGB = (200, 60, 40)
DISK_RGB = (232, 236, 240)
SPEC_RGB = (255, 255, 255)
LASH_RGB = (30, 30, 35)


def _disc(rgb, r, center=(60, 60)):
    out = np.zeros((H, W, 4), np.float32)
    m = np.zeros((H, W), np.uint8)
    cv2.circle(m, center, r, 255, -1)
    out[..., :3] = rgb
    out[..., 3] = m.astype(np.float32)      # 0/255，别用 uint8 相乘（会溢出）
    return out


def _flat(rgb):
    out = np.zeros((H, W, 4), np.float32)
    out[..., :3] = rgb
    out[..., 3] = 255.0
    return out


def _over(dst, src):
    a = src[..., 3:4] / 255.0
    dst[..., :3] = dst[..., :3] * (1 - a) + src[..., :3] * a
    dst[..., 3] = np.clip(dst[..., 3] + src[..., 3], 0, 255)
    return dst


def apply_patch(frame, patch):
    """模拟 ExprBank.apply：source-over 到整帧（只用于测试）。"""
    if patch is None:
        return frame
    out = frame.copy()
    return _over(out, patch)


def mask_of(rgba, rgb, tol=10):
    return (np.abs(rgba[..., :3] - np.array(rgb, np.float32)).max(-1) < tol) \
        & (rgba[..., 3] > 200)


@pytest.fixture(scope="session")
def eye_scene():
    socket = np.zeros((H, W), np.uint8)
    cv2.circle(socket, (56, 60), 20, 255, -1)
    cv2.circle(socket, (104, 60), 20, 255, -1)

    lash = np.zeros((H, W, 4), np.float32)
    for cx in (56, 104):
        cv2.ellipse(lash, (cx, 44), (20, 9), 0, 180, 360, (*LASH_RGB, 255), 7)
    # 眉毛：眼眶外的粗深色块，用来验证线宽统计不会把它算进去
    cv2.rectangle(lash, (36, 6), (124, 20), (*LASH_RGB, 255), -1)

    L = dict(base=_flat((245, 228, 210)),
             disk=_disc(DISK_RGB, 20),
             iris=_disc(IRIS_RGB, 13, (56, 62)),
             spec=_disc(SPEC_RGB, 4, (56, 74)),
             lash=lash)

    frame = L["base"].copy()
    for k in ("disk", "iris", "spec", "lash"):
        frame = _over(frame, L[k])
    return {"L": L, "socket": socket, "frame": frame}
