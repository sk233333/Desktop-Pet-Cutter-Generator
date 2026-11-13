"""交接包验收：B1–B12（半眼 / 惊讶 / 眨眼线宽 / 待机效果）。"""
import json
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import (DISK_RGB, IRIS_RGB, SPEC_RGB, apply_patch, mask_of)

from expr_state import STATES, ExprBank

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def bank(eye_scene):
    return ExprBank.build(eye_scene["L"], eye_scene["socket"])


# ---------------- B2/B3：半眼零形变 ----------------
def test_half_keeps_iris_width(eye_scene, bank):
    """遮挡式半眼不动眼球：虹膜水平宽度必须与 normal 完全一致。"""
    frame = eye_scene["frame"]
    half = apply_patch(frame, bank.patches["half"])
    w = lambda m: int(np.nonzero(m)[1].max() - np.nonzero(m)[1].min() + 1)  # noqa: E731
    assert w(mask_of(frame, IRIS_RGB)) == w(mask_of(half, IRIS_RGB))


def test_half_keeps_highlight_circle(eye_scene, bank):
    frame = eye_scene["frame"]
    half = apply_patch(frame, bank.patches["half"])
    sn, sh = mask_of(frame, SPEC_RGB, 6), mask_of(half, SPEC_RGB, 6)
    assert sn.sum() > 20 and sh.sum() > 0
    assert not (sh & ~sn).any(), "高光出现 normal 里没有的像素 = 被拉伸"
    box = lambda m: (int(np.ptp(np.nonzero(m)[1])), int(np.ptp(np.nonzero(m)[0])))  # noqa: E731
    assert box(sn) == box(sh)


def test_half_actually_covers(eye_scene, bank):
    frame = eye_scene["frame"]
    half = apply_patch(frame, bank.patches["half"])
    assert np.abs(half - frame).mean() > 5
    # 遮挡片取的是眼周皮肤色：睑缘上方应出现大片奶油色，而不是脏橙块
    skin = mask_of(half, (245, 228, 210), 20)
    assert skin[34:50].sum() > 60
    orange = (half[..., 0] > 200) & (half[..., 1] < 150)
    assert (orange[34:50] & (eye_scene["socket"][34:50] > 0)).sum() < 10


def test_half_drop_over_035_buries_the_eye(eye_scene):
    """上限约束的证据：0.38 时可见的虹膜/高光显著减少。"""
    L, s, frame = eye_scene["L"], eye_scene["socket"], eye_scene["frame"]
    ok = ExprBank.build(L, s, half_drop=0.30).patches["half"]
    bad = ExprBank.build(L, s, half_drop=0.38).patches["half"]
    vis = lambda p: mask_of(apply_patch(frame, p), IRIS_RGB).sum()  # noqa: E731
    assert vis(bad) < vis(ok) * 0.9


# ---------------- B5：normal 零操作 ----------------
def test_normal_is_noop(eye_scene, bank):
    assert bank.patches["normal"] is None
    assert np.array_equal(bank.apply(eye_scene["frame"], "normal"),
                          eye_scene["frame"])


# ---------------- B6：旧签名 / 三种调用形态 ----------------
def test_build_call_forms(eye_scene, tmp_path):
    L, s = eye_scene["L"], eye_scene["socket"]
    assert ExprBank.build(L, s).patches["half"] is not None
    assert ExprBank.build(L, s, tmp_path / "mouth").patches["half"] is not None
    b = ExprBank.build(L, s, mouth_dir=tmp_path / "m", half_drop=0.2)
    assert b.patches["half"] is not None
    assert set(STATES) <= set(b.patches)


# ---------------- B12：surprise 不缩放整个眼球，lash 逐像素不变 ----------------
def test_surprise_keeps_lash_pixels(eye_scene, bank):
    frame = eye_scene["frame"]
    sur = apply_patch(frame, bank.patches["surprise"])
    lash_n = mask_of(frame, (30, 30, 35), 12)
    lash_s = mask_of(sur, (30, 30, 35), 12)
    assert lash_n.sum() > 50
    assert np.array_equal(lash_n, lash_s), "惊讶把上眼睑（lash）改掉了"


def test_surprise_keeps_sclera_width(eye_scene, bank):
    frame = eye_scene["frame"]
    sur = apply_patch(frame, bank.patches["surprise"])
    span = lambda r: int(np.ptp(np.nonzero(mask_of(r, DISK_RGB))[1]))  # noqa: E731
    assert span(frame) == span(sur)


# ---------------- B11：眨眼线宽按 lash 厚度，且只统计眶内 ----------------
def _max_vertical_run(mask):
    best = 0
    for x in range(mask.shape[1]):
        run = 0
        for v in mask[:, x]:
            run = run + 1 if v else 0
            best = max(best, run)
    return best


def test_blink_line_uses_lash_thickness(eye_scene, bank):
    frame, sock = eye_scene["frame"], eye_scene["socket"]
    blink = apply_patch(frame, bank.patches["blink"])
    dark = mask_of(blink, (30, 30, 35), 40) & (sock > 0)   # 只统计眶内
    thick = _max_vertical_run(dark)
    # 线宽按 lash 厚度自适应：原 lash 是 7px 描边，手画 2px 细线会明显偏细
    assert 4 <= thick <= 12, f"闭眼线宽 {thick}px"
    ys = np.nonzero(dark)[0]
    assert np.ptp(ys) < 40, "线宽把眼眶外的眉毛也算进去了"


# ---------------- 装饰眼：第 3 个连通域不参与任何表情 ----------------
@pytest.fixture()
def decorated_scene(eye_scene):
    """在 socket 右下角加一块「配饰上的猫眼」，制造 3 连通域。"""
    L = {k: v.copy() for k, v in eye_scene["L"].items()}
    sock = eye_scene["socket"].copy()
    cv2.circle(sock, (96, 104), 8, 255, -1)
    m = np.zeros((120, 120), np.uint8)
    cv2.circle(m, (96, 104), 8, 255, -1)
    frame = eye_scene["frame"].copy()
    frame[m > 0, :3] = (28, 26, 30)            # 深色装饰眼，不属任何眼层
    return {"L": L, "socket": sock, "frame": frame, "deco_xy": (96, 104)}


def test_third_socket_is_ignored(decorated_scene):
    L, s = decorated_scene["L"], decorated_scene["socket"]
    report = []
    bank = ExprBank.build(L, s, report=report)
    assert report, "3 个连通域必须写进 report"
    x, y = decorated_scene["deco_xy"]
    for name in ("blink", "half", "surprise", "wink_l", "wink_r"):
        out = bank.apply(decorated_scene["frame"], name)
        assert np.array_equal(out[y, x], decorated_scene["frame"][y, x]), \
            f"{name} 把配饰上的装饰眼抹掉了"



def _table():
    return json.loads((ROOT / "desktop_pet" / "effect_table.json")
                      .read_text(encoding="utf-8"))


def test_director_no_repeat_and_idle(eye_scene):
    from effect_director import EffectDirector
    d = EffectDirector(_table(), rng=random.Random(7))
    d._touched = -1e9
    seen, last, gaps = [], None, []
    t = 100.0
    for _ in range(300):
        e = d.tick(now=t)
        if e:
            assert e["id"] != last, "同一效果连播"
            last = e["id"]
            seen.append(e["id"])
            gaps.append(d._next_at - t)
        t += 1.0
    assert len(seen) > 8 and len(set(seen)) >= 4
    assert min(gaps) >= 2.4, "idle_ms 被缩短了"


def test_director_respects_touch(eye_scene):
    from effect_director import EffectDirector
    d = EffectDirector(_table(), rng=random.Random(3))
    d.touch(now=100.0)
    assert all(d.tick(now=100.0 + i * 0.5) is None for i in range(6))


def test_director_per_sku_enabled_filter(eye_scene):
    from effect_director import EffectDirector
    d = EffectDirector(_table(), rng=random.Random(5), cfg={"enabled": ["bubble", "zzz"]})
    d._touched = -1e9
    got = set()
    t = 50.0
    for _ in range(400):
        e = d.tick(now=t)
        if e:
            got.add(e["id"])
        t += 1.0
    assert got and got <= {"bubble", "zzz"}, got


def test_director_forced_mood(eye_scene):
    from effect_director import EffectDirector
    d = EffectDirector(_table(), cfg={"mood": "sleepy"})
    assert d.mood == "sleepy"


def test_director_gaze_channel(eye_scene):
    from effect_director import EffectDirector
    table = {"moods": {"m": {"weights": {"g": 1.0}, "idle_ms": [1, 1]}},
             "effects": {"g": {"expr": "normal", "hold_ms": 500,
                               "gaze": {"gx": -0.7, "gy": 0.1}}}}
    d = EffectDirector(table, rng=random.Random(1))
    d._next_at, d._touched = 0, -1e9
    assert d.tick(now=10.0)["id"] == "g"
    assert d.gaze_at(10.1) == (-0.7, 0.1)
    assert d.gaze_at(11.5) is None


def test_effect_table_size():
    t = _table()
    assert len(t["effects"]) >= 8, "效果太少，加行是零成本的"
    assert set(t["moods"]) >= {"calm", "curious", "sleepy"}


# ---------------- 烘焙入口：眼眶 mask 的裁边 + 缩放换算 ----------------
def test_map_eye_masks_applies_crop_and_resize():
    """crop_box + resize 必须同时作用到 mask，否则素材上会整体偏移。"""
    from cutout_to_pet import map_eye_masks
    src = np.zeros((200, 100), bool)
    src[50:60, 20:30] = True                    # 源图坐标的一块
    crop = (10, 40, 40, 40)                     # x0, y0, cw, ch
    mapped, _ = map_eye_masks([src], None, crop, (80, 80))   # 40→80，放大 2 倍
    ys, xs = np.nonzero(mapped[0])
    # 裁后 (10..19, 10..19)，再 ×2 → (20..39, 20..39)
    assert abs(int(xs.min()) - 20) <= 2 and abs(int(xs.max()) - 39) <= 2
    assert abs(int(ys.min()) - 20) <= 2 and abs(int(ys.max()) - 39) <= 2
