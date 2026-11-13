"""C9–C11：仲裁器（单一状态源）的仓库级断言。

对应《收敛补丁清单》第 9 节 + 《裁定回执》：
- C9  timeline 半闭期间 idle 的 expr 被拒、gaze 可并行
- C10 同优先级不抢占、低不打断高、高可抢占低
- C11 touch() 后 3000ms 内 idle 全拒，guard 同时抑制 mood 切换
- Q2  半闭为「可选、默认关」：默认整段全闭
- Q4  blink_zero_gaze 默认 False（与仓库现状一致），可显式开启

时钟全部用注入的 now_ms，不依赖真实时间。
"""

from arbiter import Arbiter, DEFAULT, timeline_phase

T = 10_000.0


# ---------------- C9 ----------------
def test_timeline_blocks_idle_expr_but_allows_gaze():
    a = Arbiter()
    a.set_timeline_blink("half", now_ms=T)
    assert a.request("idle", "expr", "surprise", now_ms=T) is False
    assert a.request("idle", "gaze", (-0.7, 0.0), now_ms=T) is True
    s = a.resolve(now_ms=T)
    assert s["expr"] == "half" and s["blink"] == "half"
    assert s["gaze"] == (-0.7, 0.0)          # 默认不归零，可与半闭并行


def test_timeline_release_falls_back_then_idle_can_write():
    a = Arbiter()
    a.set_timeline_blink("full", now_ms=T)
    assert a.resolve(now_ms=T)["expr"] == "blink"
    a.set_timeline_blink(None, now_ms=T + 100)
    assert a.resolve(now_ms=T + 100)["expr"] == "normal"
    assert a.request("idle", "expr", "half", now_ms=T + 100) is True


# ---------------- C10 ----------------
def test_same_priority_no_preempt():
    a = Arbiter()
    assert a.request("idle", "expr", "wink_r", now_ms=T) is True
    assert a.request("idle", "expr", "glance", now_ms=T + 10) is False
    assert a.resolve(now_ms=T + 10)["expr"] == "wink_r"


def test_higher_preempts_lower_and_lower_cannot_interrupt():
    a = Arbiter()
    a.request("idle", "expr", "glance", now_ms=T)
    assert a.request("interaction", "expr", "surprise", now_ms=T + 10) is True
    assert a.resolve(now_ms=T + 10)["expr"] == "surprise"
    assert a.request("idle", "expr", "glance", now_ms=T + 20) is False


def test_blink_is_timeline_only():
    """矩阵：blink 通道 interaction / idle 都写不了。"""
    a = Arbiter()
    assert a.request("idle", "blink", "blink", now_ms=T) is False
    assert a.request("interaction", "blink", "half", now_ms=T) is False
    assert a.resolve(now_ms=T)["blink"] is None


def test_hold_expiry_falls_back_to_default():
    a = Arbiter(hold_ms=500)
    a.request("idle", "expr", "half", now_ms=T)
    assert a.resolve(now_ms=T + 499)["expr"] == "half"
    assert a.resolve(now_ms=T + 501)["expr"] == DEFAULT["expr"]


# ---------------- C11 ----------------
def test_touch_guard_blocks_idle_and_mood():
    a = Arbiter(interaction_guard_ms=3000)
    a.touch(now_ms=T)
    assert a.request("idle", "expr", "blink", now_ms=T + 2999) is False
    assert a.mood_allowed(now_ms=T + 2999) is False
    assert a.request("idle", "expr", "blink", now_ms=T + 3001) is True
    assert a.mood_allowed(now_ms=T + 3001) is True


def test_mood_cooldown():
    a = Arbiter()
    a.mark_mood_switch(now_ms=T)
    assert a.mood_allowed(now_ms=T + 19_999) is False
    assert a.mood_allowed(now_ms=T + 20_001) is True


# ---------------- Q4 ----------------
def test_blink_zero_gaze_off_by_default_and_opt_in():
    a = Arbiter()
    assert a.blink_zero_gaze is False
    a.set_timeline_blink("full", now_ms=T)
    a.request("idle", "gaze", (0.6, -0.2), now_ms=T)
    assert a.resolve(now_ms=T)["gaze"] == (0.6, -0.2)

    b = Arbiter(blink_zero_gaze=True)
    b.set_timeline_blink("full", now_ms=T)
    b.request("idle", "gaze", (0.6, -0.2), now_ms=T)
    assert b.resolve(now_ms=T)["gaze"] == (0.0, 0.0)


# ---------------- Q2：相位辅助 ----------------
def test_timeline_phase_default_is_full_blink():
    """half 默认关：整个 hold 都是全闭（不改变现有观感）。"""
    items = [{"t": 2000.0, "hold": 200.0}]
    assert timeline_phase(2000.0, 8000.0, items) == ("full", items[0])
    assert timeline_phase(2100.0, 8000.0, items) == ("full", items[0])
    assert timeline_phase(2201.0, 8000.0, items) == (None, None)


def test_timeline_phase_half_opt_in_and_wraparound():
    items = [{"t": 2000.0, "hold": 200.0, "half": True}]
    assert timeline_phase(2050.0, 8000.0, items, half_enabled=True)[0] == "half"
    assert timeline_phase(2150.0, 8000.0, items, half_enabled=True)[0] == "full"
    # 跨周期：t=2000、cycle=8000，now=2000 的下一个周期 10000 仍应命中
    assert timeline_phase(10_050.0, 8000.0, items, half_enabled=True)[0] == "half"


# ---------------- T2：时间轴半闭 ----------------
def test_t2_blink_group_is_normalized_to_gstr():
    """v2 的 g{n} 与旧 int 组号在 Blink 里统一成同一套写法。"""
    from anim import Blink
    assert Blink(t=0.0, group=1).group == "g1"
    assert Blink(t=0.0, group="g2").group == "g2"
    assert Blink(t=0.0, group="g2").group_no == 2


def test_t2_half_false_keeps_legacy_front_frame():
    """C4 反面：half:false 时前段仍是 0.55 遮挡过渡帧，且不产出 ExprBank 半眼。"""
    from anim import Blink, blink_phases
    blinks = [Blink(t=1.0, hold=0.4, group="g1", half=False)]
    for t in (1.0, 1.1, 1.19):
        assert blink_phases(blinks, 8.0, t) == {1: 0}, t
    assert blink_phases(blinks, 8.0, 1.2) == {1: 1}      # dt == hold/2 归全闭
    assert blink_phases(blinks, 8.0, 1.4) == {}


def test_t2_half_true_boundary_belongs_to_full():
    """C4：half:true 前半段 = ExprBank 半眼；dt == hold/2 归全闭，两段不重叠。"""
    from anim import Blink, blink_phases
    blinks = [Blink(t=1.0, hold=0.4, group="g1", half=True)]
    assert blink_phases(blinks, 8.0, 1.0) == {1: 0}
    assert blink_phases(blinks, 8.0, 1.199) == {1: 0}
    assert blink_phases(blinks, 8.0, 1.2) == {1: 1}
    assert blink_phases(blinks, 8.0, 1.399) == {1: 1}
    assert blink_phases(blinks, 8.0, 1.4) == {}


def test_t2_half_frame_is_exprbank_apply(eye_scene):
    """C4 正面：ExprBank 半眼帧逐像素等于 apply(base, 'half')。"""
    from anim import PetAsset
    from expr_state import ExprBank
    bank = ExprBank.build(eye_scene["L"], eye_scene["socket"])
    a = PetAsset(rgba=eye_scene["frame"])
    a._expr_bank = bank                  # 跳过依赖真实素材的几何检测
    import numpy as np
    assert np.array_equal(a.half_blink_frame(),
                          bank.apply(eye_scene["frame"], "half"))


def test_t2_front_frame_choice_depends_on_half(eye_scene):
    """前段帧：half:false → frames[0]；half:true → ExprBank 半眼。"""
    import numpy as np
    from anim import compose_group_blink
    from expr_state import ExprBank
    base = eye_scene["frame"]
    frames = {1: [base + 1.0, base + 2.0]}          # 两帧可区分
    rect = {1: (0, 0, base.shape[1], base.shape[0])}
    bank = ExprBank.build(eye_scene["L"], eye_scene["socket"])
    half = bank.apply(base, "half")

    legacy = compose_group_blink(base, {1: 0}, frames, rect)
    assert np.array_equal(legacy, frames[1][0])
    assert not np.array_equal(legacy, half)         # 不是 ExprBank 半眼

    with_half = compose_group_blink(base, {1: 0}, frames, rect, {1: half})
    assert np.array_equal(with_half, half)


def test_c12a_other_group_untouched_when_one_group_half_blinks(eye_scene):
    """C12a：g1 前段贴 ExprBank 半眼时，g2 包围盒逐像素等于 base。"""
    import numpy as np
    from anim import compose_group_blink
    h, w = eye_scene["frame"].shape[:2]
    base = eye_scene["frame"]
    g1 = (0, 0, w // 2, h)
    g2 = (w // 2, 0, w, h)
    fill = np.full_like(base, 9.0)                  # 模拟「整帧半眼」
    out = compose_group_blink(base, {1: 0},
                              {1: [fill, fill * 2], 2: [fill * 3, fill * 4]},
                              {1: g1, 2: g2}, {1: fill})
    x0, y0, x1, y1 = g2
    assert np.array_equal(out[y0:y1, x0:x1], base[y0:y1, x0:x1])
    assert np.array_equal(out[g1[1]:g1[3], g1[0]:g1[2]], fill[g1[1]:g1[3], g1[0]:g1[2]])


def test_t2_make_blink_frames_full_frame_untouched():
    """不改 make_blink_frames 的全闭帧生成：默认仍是 (0.55, 1.0) 两帧。"""
    import inspect
    from anim import make_blink_frames
    assert inspect.signature(make_blink_frames).parameters["covers"].default \
        == (0.55, 1.0)


# ---------------- C12：接线层不再直写状态 ----------------
def test_c12_no_direct_expression_writes():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "desktop_pet"
           / "pet_window.py").read_text("utf-8")
    for pat in ("expr_state.set(", "sm.set(", "self.expr_state"):
        assert pat not in src, pat
    assert "arbiter.resolve(" in src
    assert "set_timeline_blink(phase" in src
    # 全闭眼只留 make_blink_frames 这一份实现（时间轴与 idle 共用）
    assert 'state == "blink"' in src and "blink_frames()[-1]" in src


def test_c12_preview_uses_shared_phase_function():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "desktop_pet"
           / "ui_app.py").read_text("utf-8")
    assert "blink_phases(self.blinks" in src
    assert "local >= hold * 0.5" not in src


# ---------------- T6：眨眼分组只认 active，且不重编号 ----------------
def test_t6_blink_groups_keep_original_indices():
    from anim import Blink, PetAsset
    a = PetAsset(rgba=__import__("numpy").zeros((4, 4, 4), "float32"),
                 eyes=[object()] * 4,
                 blinks=[Blink(t=1.0, group="g1", count=2),
                         Blink(t=1.0, group="g2", count=2)],
                 active_eyes=(0, 2))
    # 组号按原始顺序：组1=眼1,2 → 只剩眼1；组2=眼3,4 → 只剩眼3
    assert a._eye_groups() == [[0], [2]]


def test_c8e_b_active_zero_means_no_blink_and_pixels_unchanged(eye_scene):
    """C8e-b：active=0 时时间轴眨眼不产生帧，整张图逐像素等于 base。"""
    import numpy as np
    from anim import Blink, PetAsset, compose_group_blink
    base = eye_scene["frame"]
    a = PetAsset(rgba=base, eyes=[object()] * 2,
                 blinks=[Blink(t=1.0, group="g1", count=2)],
                 active_eyes=())
    assert a.blink_frames() == []
    assert a.group_blink_frames() == {}
    h, w = base.shape[:2]
    out = compose_group_blink(base, {1: 1}, {}, {1: (0, 0, w, h)})
    assert np.array_equal(out, base)


def test_c8c_c_socket_slots_keyed_by_eye_index(tmp_path):
    """C8c-c：R 为空、只有 G 有内容时，socket 必须落在 eye_index=1，不能塌成 0。"""
    import cv2
    import numpy as np
    from anim import load_eye_slots
    h = w = 20
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 1] = 255                      # G 通道 = 第 2 只眼
    p = tmp_path / "a_eyes.png"
    cv2.imwrite(str(p), img)
    slots, _hl = load_eye_slots([p], (h, w, 4))
    assert sorted(slots) == [1]
    assert slots[1].any()


def test_c8c_c_missing_first_mask_file_keeps_index(tmp_path):
    """③ 中间文件缺失：只有 _eyes_2.png 时 key 仍是 2/3，不能塌成 0/1。"""
    import cv2
    import numpy as np
    from anim import load_eye_slots
    h = w = 20
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 2] = 255                      # R = 第 3 只眼（cv2 是 BGR 写盘）
    p = tmp_path / "a_eyes_2.png"
    cv2.imwrite(str(p), img)
    slots, _hl = load_eye_slots([p], (h, w, 4))
    assert sorted(slots) == [2]


def test_c8c_d_flat_layer_pads_empty_slots(tmp_path):
    """③ 扁平层按 eye_index 补空占位，不把空通道挤掉（否则编辑器左移）。"""
    import cv2
    import numpy as np
    from anim import load_eye_masks
    h = w = 20
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 1] = 255                      # 只有 G（第 2 只眼）有内容
    p = tmp_path / "a_eyes.png"
    cv2.imwrite(str(p), img)
    masks, _hl = load_eye_masks([p], (h, w, 4))
    assert len(masks) == 2                 # 占位，不左移
    assert not masks[0].any() and masks[1].any()


def test_c8c_d_missing_socket_builds_no_geometry(eye_scene):
    """C8c-d：require_socket=True 且 socket=None → 不建几何、不抛异常、不回退椭圆。"""
    from anim import Eye, _eye_geometry, build_gaze_geos
    eyes = [Eye(cx=56, cy=60, rx=20, ry=20)]
    geos = build_gaze_geos(eye_scene["frame"], eyes, sockets=[None],
                           require_socket=True)
    assert geos == [None]                  # 留空槽，但不建几何
    # require_socket=True 在选椭圆眼眶之前就返回 None，所以不存在「回退椭圆」
    assert _eye_geometry(eye_scene["frame"], eyes[0],
                         require_socket=True) is None


def test_c12a_c_timeline_group_number_stays_stable(eye_scene):
    """C12a-c：6 眼 3 组、active=[0,1] → [[0,1],[],[]]；引用 g2 的 item 不落到组1。"""
    from anim import Blink, PetAsset, blink_phases
    blinks = [Blink(t=1.0, group="g1", count=2),
              Blink(t=2.0, group="g2", count=2),
              Blink(t=3.0, group="g3", count=2)]
    a = PetAsset(rgba=eye_scene["frame"], eyes=[object()] * 6,
                 blinks=blinks, active_eyes=(0, 1))
    assert a._eye_groups() == [[0, 1], [], []]
    # 组 2 的 item 只在 t≈2.0 出相位，且它对应空组 → 不会贴到组 1
    assert blink_phases(blinks, 8.0, 2.0) == {2: 0}
    # 组 2 是空组：group_blink_frames 会跳过它，不会把 g2 的眨眼贴到组 1
    assert a._eye_groups()[1] == []


def test_c8e_active_zero_expression_leaves_pixels_unchanged(eye_scene):
    """C8e：active=0 时不建视线/表情几何，非 normal 表情也改不动画面。

    （C8e-b 只覆盖 blink 通道；这条覆盖 expr/gaze 通道，别混。）
    """
    import numpy as np
    from anim import Eye, PetAsset, gaze_frame
    base = eye_scene["frame"]
    a = PetAsset(rgba=base, eyes=[Eye(cx=56, cy=60, rx=20, ry=20)],
                 eye_sockets={0: eye_scene["socket"]}, active_eyes=())
    assert a.gaze_geos() == []
    assert a.expr_bank() is None                # 没有几何就没有表情贴片
    out = gaze_frame(base, a.eyes, 6, 6, geos=a.gaze_geos())
    assert np.array_equal(out, base)            # 眼睛区域逐像素等于 base


def test_v1_pending_warning_only_once(capsys):
    """v1 无 mask 的素材：任何读取路径只点名一次，不刷屏。"""
    from manifest_io import active_eye_indices
    item = {"stem": "warn_once_demo", "eyes": [{"cx": 1, "cy": 1, "rx": 2, "ry": 2}],
            "eyes_mask": []}
    for _ in range(3):
        assert active_eye_indices(item) is None
    assert capsys.readouterr().err.count("warn_once_demo") == 1


def test_c12a_b_middle_state_other_eyes_untouched(eye_scene):
    """C12a-b：6 只眼 active=[0,1] 时眨眼，index 2..5 包围盒逐像素等于 base。

    正是 3b3d / d37e 补完部分 mask 后的中间态。
    """
    import numpy as np
    from anim import Blink, PetAsset, compose_group_blink
    base = eye_scene["frame"]
    h, w = base.shape[:2]
    a = PetAsset(rgba=base, eyes=[object()] * 6,
                 blinks=[Blink(t=1.0, group="g1", count=2)],
                 active_eyes=(0, 1))
    assert a._eye_groups() == [[0, 1]]            # index 2..5 不参与、不重编号
    frames = {1: [base + 1.0, base + 2.0]}
    rects = {1: (0, 0, w // 2, h)}                # 组1只覆盖眼1/2 的条带
    out = compose_group_blink(base, {1: 1}, frames, rects)
    assert np.array_equal(out[:, :w // 2], frames[1][-1][:, :w // 2])
    # 下半张（模拟 index 2..5 所在的区域）逐像素等于 base
    assert np.array_equal(out[:, w // 2:], base[:, w // 2:])
