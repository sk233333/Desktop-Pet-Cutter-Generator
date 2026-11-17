"""摆动收敛：轴线投影 / bias / 内摆保护 / 位移上限 / 元数据链路。

对应审阅清单 C-S1 ~ C-S9。测试不依赖 PyQt6，CI（无 Qt 环境）也能跑。
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

from anim import (MAX_SWAY_SHIFT_PX, HairField, HairWisp,
                  hair_from_dicts, inward_factor, sway_axis, swing_all)
from cutout_to_pet import load_hair_sidecar, map_hair_wisps
from launch import save_hair_polys
from manifest_io import SWAY_DEFAULTS, SWAY_RANGES, validate_manifest
from motion_cfg import load_motion, save_motion

S = 96


def _rgba():
    img = np.zeros((S, S, 4), np.uint8)
    img[10:91, 20:77] = (210, 190, 175, 255)
    # 给主体内部加纹理：整块纯色时 remap 后像素完全一样，测不出"确实动了"
    yy, xx = np.mgrid[0:S, 0:S]
    img[10:91, 20:77, 0] = (xx[10:91, 20:77] * 7) % 255
    img[10:91, 20:77, 1] = (yy[10:91, 20:77] * 11) % 255
    return img


def _wisp(y0=14, x0=34, amp=1.0, dir_deg=None, bias=0.0):
    m = np.zeros((S, S), bool)
    m[y0:y0 + 40, x0:x0 + 14] = True
    return HairWisp(mask=m, pivot=(x0 + 7, y0), tip=(x0 + 7, y0 + 40),
                    amp=amp, dir=dir_deg, bias=bias)


def _field(wisps, amp=1.0):
    rgba = _rgba()
    f = HairField(wisps, rgba, S, S, amp=amp)
    f.wind_speed = 1.0
    return f


# ---------- C-S1：方向与丝长太近时自动取垂直分量 ----------

def test_sway_axis_projects_near_parallel():
    # 丝长朝下 (ux, uy) = (0, 1)；选「↓」几乎平行 → 必须投影到水平轴
    normal, projected = sway_axis(270.0, 0.0, 1.0)
    assert projected is True
    assert abs(normal[0] - (-1.0)) < 1e-6 and abs(normal[1]) < 1e-6
    # 选「→」与丝长垂直 → 原样使用，不投影
    normal, projected = sway_axis(0.0, 0.0, 1.0)
    assert projected is False
    assert normal == (1.0, 0.0)
    # 45° 斜向与竖直丝长夹角 45° > 25° → 不投影
    _n, projected = sway_axis(315.0, 0.0, 1.0)
    assert projected is False


def test_field_keeps_written_dir_but_uses_projected_axis():
    f = _field([_wisp(dir_deg=270.0)])       # 竖直发丝 + 选「↓」
    s = f.items[0]
    assert s.custom is True and s.projected is True
    assert abs(s.normal[0]) > 0.99          # 实际位移轴是水平
    assert abs(s.normal[1]) < 1e-6


# ---------- C-S2：bias=0 与改造前逐帧一致；bias 走同一个 angle 通道 ----------

def _legacy_advance(f, dt, vx, tilt):
    """改造前 step() 的拷贝，用作 golden。"""
    f.phase += dt * 2.6 * f.wind_speed
    vnorm = max(-1.0, min(1.0, vx / 620.0))
    for s in f.items:
        lim = s.angle_max * f.amp * s.gain
        wind = lim * (0.95 if s.free else 0.9) * math.sin(f.phase + s.phase)
        target = s.tip_dir * (wind + lim * 0.85 * vnorm) - 0.35 * tilt
        target = max(-lim, min(lim, target))
        if not s.free and not s.custom:
            sign = -s.out_dir * s.tip_dir
            if target * sign < 0:
                target *= 0.6
        acc = ((target - s.angle) * s.stiff * f.stiffness
               - s.omega * s.damp * f.damping)
        s.omega += acc * dt
        s.angle += s.omega * dt
        if s.angle > lim:
            s.angle, s.omega = lim, 0.0
        elif s.angle < -lim:
            s.angle, s.omega = -lim, 0.0


def test_bias_zero_matches_legacy_frames():
    new = _field([_wisp()])
    old = _field([_wisp()])
    for k in range(160):
        dt, vx, tilt = 0.05, 40.0 * math.sin(k / 9.0), 0.6 * math.sin(k / 7.0)
        new.step(dt, vx, tilt)
        _legacy_advance(old, dt, vx, tilt)
        assert new.items[0].angle == old.items[0].angle
        assert new.items[0].omega == old.items[0].omega


def test_bias_shifts_angle_without_moving_balance_point():
    f = _field([_wisp(bias=6.0)])
    f.wind_speed = 0.0                     # 关风，弹簧平衡点 = 0
    for _ in range(400):
        f.step(0.05, 0.0, 0.0)
    assert abs(f.items[0].angle - 6.0) < 0.5


def test_bias_zero_settles_back_to_zero():
    f = _field([_wisp(bias=0.0)])
    f.wind_speed = 0.0
    for s in f.items:
        s.angle, s.osc = 6.0, 6.0
    for _ in range(400):
        f.step(0.05, 0.0, 0.0)
    assert abs(f.items[0].angle) < 1e-3


def test_projected_axis_dir_is_written_back_unchanged(tmp_path):
    w = _mk_source(tmp_path)[0]
    w["dir"] = 270.0                       # 与竖直发丝平行，只影响渲染，不改写数据
    save_hair_polys(tmp_path, "t", (S, S), [w], (0, 0), force=True)
    got = load_hair_sidecar(tmp_path / "t.png", (S, S, 3))
    assert abs(got[0]["dir"] - 270.0) < 1e-6


# ---------- C-S3：向身体内侧摆幅 ≤ 外摆的 60% ----------

class _S:
    def __init__(self, normal, out_dir, tip_dir=1.0, custom=True, free=False):
        self.normal = normal
        self.out_dir = out_dir
        self.tip_dir = tip_dir
        self.custom = custom
        self.free = free


def test_custom_axis_inward_limited_to_sixty_percent():
    # 左侧区域 out_dir=-1，身体内侧 = +x；normal=+x → 正值目标是向内
    s = _S((1.0, 0.0), -1.0)
    assert abs(inward_factor(s, 1.0) - 0.6) < 1e-9
    assert inward_factor(s, -1.0) == -1.0
    # 垂直轴（纯上下）不涉及内摆，原样保留
    s_y = _S((0.0, -1.0), -1.0)
    assert inward_factor(s_y, 1.0) == 1.0
    # 外缘临透明的发丝保持原例外
    s_free = _S((1.0, 0.0), -1.0, free=True)
    assert inward_factor(s_free, 1.0) == 1.0


def test_default_axis_keeps_old_inward_rule():
    s = _S((0.3, -1.0), -1.0, custom=False)
    # sign = -out_dir * tip_dir = +1；目标为负 = 向里 → 幅值乘 0.6（符号不变）
    assert abs(inward_factor(s, -1.0) + 0.6) < 1e-9
    assert inward_factor(s, 1.0) == 1.0


# ---------- P1-10：多级幅度叠加后的像素位移有上限 ----------

def test_final_shift_is_capped():
    f = _field([_wisp(amp=3.0)], amp=3.0)   # 3×3 = 9×
    for _ in range(600):
        f.step(0.05, 0.0, 0.0)
    s = f.items[0]
    shift = s.length * math.sin(math.radians(s.angle))
    assert shift <= MAX_SWAY_SHIFT_PX + 1e-6


# ---------- C-S4：自定义轴下，包围盒外像素仍逐比特不变 ----------

def test_custom_axis_swing_keeps_outside_bitwise():
    rgba = _rgba()
    f = _field([_wisp(amp=2.0, dir_deg=200.0)])
    for s in f.items:
        s.angle, s.omega = 7.0, 0.0
    out = swing_all((f,), rgba)
    inside = np.zeros((S, S), bool)
    for s in f.items:
        x0, y0, x1, y1 = s.bbox
        inside[y0:y1, x0:x1] = True
    assert np.array_equal(out[~inside], rgba[~inside])
    assert out[inside].tobytes() != rgba[inside].tobytes()


# ---------- C-S8：apply_params 就地更新，不换对象 ----------

def test_apply_params_updates_in_place():
    f = _field([_wisp()])
    s = f.items[0]
    ident = id(s)
    f.apply_params([(2.5, 0.0)])
    assert id(s) == ident
    assert abs(s.gain - 2.5) < 1e-9
    assert s.custom is True and s.projected is False
    assert abs(s.normal[0] - 1.0) < 1e-9 and abs(s.normal[1]) < 1e-9
    f.apply_params([(0.0, None)])
    assert s.gain == 0.0 and s.custom is False
    assert s.normal == s.normal_default


# ---------- C-S6/C-S7：sidecar 往返 + manifest 映射 ----------

def _mk_source(tmp_path: Path):
    mask = np.zeros((S, S), bool)
    mask[20:60, 30:44] = True
    return [{"mask": mask, "pivot": (37, 20), "tip": (37, 60),
             "amp": 1.0, "dir": None, "bias": 0.0}]


def test_sidecar_omits_defaults_and_roundtrips(tmp_path):
    src = tmp_path
    w = _mk_source(tmp_path)[0]
    p = save_hair_polys(src, "t", (S, S), [w], (0, 0), force=True)
    data = json.loads(p.read_text())
    assert "amp" not in data["wisps"][0]
    assert "dir" not in data["wisps"][0]
    assert "bias" not in data["wisps"][0]
    # 读回等于默认值
    got = load_hair_sidecar(src / "t.png", (S, S, 3))
    assert got[0]["amp"] == 1.0
    assert got[0].get("dir") is None      # 省略 = 沿发丝法线
    assert got[0].get("bias", 0.0) == 0.0

    w["amp"], w["dir"], w["bias"] = 1.6, 90.0, 4.0
    save_hair_polys(src, "t", (S, S), [w], (0, 0), force=True)
    got = load_hair_sidecar(src / "t.png", (S, S, 3))
    assert len(got) == 1
    assert abs(got[0]["amp"] - 1.6) < 1e-6
    assert abs(got[0]["dir"] - 90.0) < 1e-6
    assert abs(got[0]["bias"] - 4.0) < 1e-6


def test_map_hair_wisps_carries_fields(tmp_path):
    w = _mk_source(tmp_path)[0]
    w.update({"amp": 2.2, "dir": 45.0, "bias": 3.0})
    save_hair_polys(tmp_path, "t", (S, S), [w], (0, 0), force=True)
    side = load_hair_sidecar(tmp_path / "t.png", (S, S, 3))
    out = map_hair_wisps(side, (0, 0, S, S), (S, S), tmp_path, "t")
    e = out[0]
    assert abs(e["amp"] - 2.2) < 1e-6
    assert abs(e["dir"] - 45.0) < 1e-6
    assert abs(e["bias"] - 3.0) < 1e-6
    # 同一份数据喂给运行时，逐区域参数不能丢
    ws = hair_from_dicts([dict(mask=str(e["mask"]), pivot=e["pivot"],
                               tip=e["tip"], amp=e["amp"], dir=e["dir"],
                               bias=e["bias"])], base_dir=tmp_path)
    assert len(ws) == 1
    assert abs(ws[0].amp - 2.2) < 1e-6
    assert abs(ws[0].bias - 3.0) < 1e-6


def _manifest_with_sway():
    return {
        "schema_version": 2,
        "items": [{
            "stem": "a",
            "parts": [{"id": "eye1", "channel": "R", "group": "g1",
                       "mask": "eye1.png"}],
            "hair": [{"mask": "a_hair1.png", "pivot": [10, 20], "tip": [12, 60],
                      "amp": 1.5, "dir": 90.0, "bias": 2.0}],
            "cloth": [{"mask": "a_cloth1.png", "pivot": [30, 20], "tip": [40, 70]}],
        }],
    }


def test_manifest_validator_accepts_and_rejects_sway_fields():
    ok, msg = validate_manifest(_manifest_with_sway())
    assert ok, msg
    for key, bad in (("amp", 4.0), ("dir", 360.0), ("bias", 60.0)):
        m = _manifest_with_sway()
        m["items"][0]["hair"][0][key] = bad
        ok, _ = validate_manifest(m)
        assert not ok, f"{key}={bad} 应被拒绝"


def test_schema_declares_sway_fields_consistently():
    schema = json.loads((Path(__file__).resolve().parents[1] / "desktop_pet"
                         / "manifest.schema.json").read_text(encoding="utf-8"))
    prop = schema["$defs"]["swayWisp"]["properties"]
    assert prop["amp"]["default"] == SWAY_DEFAULTS["amp"]
    assert prop["amp"]["minimum"] == SWAY_RANGES["amp"][0]
    assert prop["amp"]["maximum"] == SWAY_RANGES["amp"][1]
    assert prop["dir"]["default"] is None
    assert prop["dir"]["exclusiveMaximum"] == 360
    assert prop["bias"]["default"] == SWAY_DEFAULTS["bias"]
    assert prop["bias"]["minimum"] == SWAY_RANGES["bias"][0]
    assert prop["bias"]["maximum"] == SWAY_RANGES["bias"][1]
    item = schema["$defs"]["item"]["properties"]
    for key in ("hair", "cloth"):
        assert item[key]["items"]["$ref"] == "#/$defs/swayWisp"


# ---------- C-S5：只写元数据时不重写 mask ----------

def test_meta_only_save_keeps_masks_untouched(tmp_path):
    w = _mk_source(tmp_path)[0]
    save_hair_polys(tmp_path, "t", (S, S), [w], (0, 0), force=True)
    png = tmp_path / "t_hair1.png"
    before = png.read_bytes()
    before_mtime = png.stat().st_mtime_ns

    w["amp"], w["dir"] = 2.4, 180.0
    save_hair_polys(tmp_path, "t", (S, S), [w], (0, 0), force=True,
                    masks_dirty=False)
    assert png.read_bytes() == before
    assert png.stat().st_mtime_ns == before_mtime
    data = json.loads((tmp_path / "t_hair.json").read_text())
    assert abs(data["wisps"][0]["amp"] - 2.4) < 1e-6
    assert abs(data["wisps"][0]["dir"] - 180.0) < 1e-6


def test_full_save_still_cleans_stale_masks(tmp_path):
    w = _mk_source(tmp_path)[0]
    second = dict(w)
    second["mask"] = np.zeros((S, S), bool)
    second["mask"][20:60, 60:74] = True
    second["pivot"], second["tip"] = (67, 20), (67, 60)
    save_hair_polys(tmp_path, "t", (S, S), [w, second], (0, 0), force=True)
    assert (tmp_path / "t_hair2.png").exists()
    save_hair_polys(tmp_path, "t", (S, S), [w], (0, 0), force=True)
    assert not (tmp_path / "t_hair2.png").exists()


# ---------- C-S9：motion.json 按 SKU 分开 ----------

def test_motion_cfg_is_per_sku(tmp_path):
    p = tmp_path / "motion.json"
    assert load_motion(p, "cat") == (1.0, None)
    save_motion(p, "cat", 1.5, 90.0)
    save_motion(p, "fox", 0.5, None)
    assert load_motion(p, "cat") == (1.5, 90.0)
    assert load_motion(p, "fox") == (0.5, None)
    assert load_motion(p, "unknown") == (1.0, None)
    # 覆盖层：motion.json 有值时以它为准，且写它不会反写 manifest
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({"schema_version": 2, "items": []}), encoding="utf-8")
    before = mf.read_bytes()
    save_motion(p, "cat", 2.5, 180.0)
    assert load_motion(p, "cat") == (2.5, 180.0)
    assert mf.read_bytes() == before


def test_motion_cfg_legacy_flat_is_read_only(tmp_path):
    p = tmp_path / "motion.json"
    p.write_text(json.dumps({"scale": 2.0, "dir": 270.0}), encoding="utf-8")
    before = p.read_bytes()
    assert load_motion(p, "cat") == (2.0, 270.0)
    assert p.read_bytes() == before          # 只读降级，不回写迁移
    save_motion(p, "cat", 1.0, None)         # 用户主动改才写新格式
    data = json.loads(p.read_text())
    assert data["skus"]["cat"] == {"scale": 1.0, "dir": None}
