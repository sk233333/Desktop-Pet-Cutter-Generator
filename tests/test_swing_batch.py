"""合批摆动（swing_all）：多个场只重采样一次，区外像素逐比特保留。

头发与衣服以前各自 swing 一遍，衣服段数一多就成倍变慢；
这里钉住合并后的两条不变量：不动的场是恒等、区外像素不变。
"""
import numpy as np
import cv2

from anim import HairField, HairWisp, swing_all

S = 64


def _scene():
    rgba = np.zeros((S, S, 4), np.uint8)
    cv2.rectangle(rgba, (8, 8), (56, 56), (200, 180, 170, 255), -1)
    return rgba


def _field(y0, x0, angle=0.0):
    m = np.zeros((S, S), np.uint8)
    cv2.ellipse(m, (x0, y0), (5, 14), 0, 0, 360, 255, -1)
    f = HairField([HairWisp(mask=m > 0, pivot=(x0, y0 - 14),
                            tip=(x0, y0 + 14))], _scene(), S, S)
    for s in f.items:
        s.angle, s.omega = angle, 0.0
    return f


def test_idle_fields_are_identity():
    rgba = _scene()
    f = _field(30, 20, angle=0.0)
    assert swing_all((f,), rgba) is rgba


def test_merged_pass_keeps_outside_pixels_bitwise():
    rgba = _scene()
    a = _field(24, 16, angle=6.0)
    b = _field(40, 48, angle=-6.0)
    out = swing_all((a, b), rgba)
    assert out.shape == rgba.shape and out.dtype == rgba.dtype
    box = (0, 0, S, S)
    inside = np.zeros((S, S), bool)
    for f in (a, b):
        for s in f.items:
            x0, y0, x1, y1 = s.bbox
            inside[y0:y1, x0:x1] = True
    assert np.array_equal(out[~inside], rgba[~inside]), "包围盒外必须逐字节不动"
    assert out[inside].tobytes() != rgba[inside].tobytes(), "盒内应真的动了"
    assert set(box) == {0, S}


def test_single_field_swing_matches_batch():
    rgba = _scene()
    f = _field(30, 30, angle=5.0)
    assert np.array_equal(f.swing(rgba), swing_all((f,), rgba))
