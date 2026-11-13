#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 2D 桌宠动作层（不依赖 Qt）。

提供三件事：
1. Motion：呼吸 / 摇摆 / 弯曲的通用参数与逐帧取值，任何透明图都能用；
2. detect_eyes()：在上半身找成对的暗色椭圆块，定位双眼；
3. make_blink_frames()：用“局部填肤色 + 眼睑弧线”生成半闭/全闭帧。

眨眼是唯一依赖图像内容的动作：检测不到眼睛时返回空帧，窗口侧自动跳过，
呼吸与摇摆不受影响。

单独预览眨眼效果：
    python3 desktop_pet/anim.py --asset "desktop_pet/assets/bgy/bgy_001_white.png" \
        --preview desktop_pet/assets/blink_preview.png
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

GOLDEN_ANGLE = 2.399963        # 黄金角：让相邻发束的相位尽量错开


@dataclass
class Motion:
    """通用动作参数；默认值适用于 512px 左右的站立手办。"""

    breath: float = 0.02          # 纵向缩放幅度（2%）
    breath_period: float = 4.2    # 呼吸周期（秒）
    sway_deg: float = 1.2         # 摇摆角度（度）
    sway_period: float = 5.6      # 摇摆周期（秒）
    bend: float = 0.0             # 预留：运行时网格弯曲幅度（0 关闭）
    blink: bool = True
    blink_interval: tuple[float, float] = (3.0, 6.0)

    def at(self, t: float) -> dict:
        """返回 t 时刻的变换参数；角度单位是度，缩放是相对 1.0 的倍数。"""
        return {
            "scale_y": 1.0 + self.breath * math.sin(2 * math.pi * t / self.breath_period),
            "angle": self.sway_deg * math.sin(2 * math.pi * t / self.sway_period),
        }


@dataclass
class HairWisp:
    """一缕手工多边形圈出的头发。

    mask 决定哪些像素参与摆动；pivot 是发根（旋转中心，画多边形时点的第一个顶点）；
    tip 是静止时的发梢，用来算发缕长度和摆动的力学方向。
    """

    mask: np.ndarray
    pivot: tuple[float, float]
    tip: tuple[float, float]


def hair_from_dicts(items: list[dict] | None,
                    base_dir: Path | None = None) -> list[HairWisp]:
    """manifest 里的发缕（mask 文件名 + 素材像素坐标的发根/发梢）-> HairWisp。"""
    wisps: list[HairWisp] = []
    if not isinstance(items, list):
        return wisps
    for it in items:
        try:
            name = str(it["mask"])
            p = Path(name) if base_dir is None else Path(base_dir) / name
            m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if m is None or not (m > 127).any():
                continue
            pivot = [float(v) for v in it["pivot"]]
            tip = [float(v) for v in it["tip"]]
            wisps.append(HairWisp(m > 127, (pivot[0], pivot[1]), (tip[0], tip[1])))
        except (KeyError, TypeError, ValueError, IndexError, OSError):
            continue
    return wisps


def group_number(group) -> int:
    """分组 id → 数字：'g3' / 3 / '3' → 3，非法值回 1。

    `read_manifest()` 已把 group 归一成 `g{n}`；这里只是把 g 前缀还原成
    窗口层的整数键，**不做类型判定分流**（下游只见归一化后的字符串）。
    """
    if isinstance(group, bool):
        return 1
    if isinstance(group, int):
        return max(1, group)
    s = str(group).strip().lstrip("gG")
    try:
        return max(1, int(s))
    except (TypeError, ValueError):
        return 1


@dataclass
class Blink:
    """眨眼时间轴上的一个眨眼对象（对应一组眼睛）。"""

    t: float            # 循环内触发时刻（秒）
    hold: float = 0.2   # 闭眼持续时长（秒）
    group: str = "g1"   # 眼组 id（g1 = 眼1+眼2，g2 = 眼3+眼4 …）
    count: int = 2      # 这组包含几只眼睛（按眼组编号顺序分配）
    half: bool = False  # 前半段是否走遮挡式半闭（默认 false = 整段全闭）

    def __post_init__(self):
        # 旧调用方仍可能传 int；统一成 g{n}，与 v2 manifest 保持同一套写法
        if not isinstance(self.group, str):
            self.group = f"g{group_number(self.group)}"

    @property
    def group_no(self) -> int:
        return group_number(self.group)


def blinks_from_plan(plan: dict | None) -> tuple[list[Blink], float]:
    """manifest 里的眨眼时间轴 -> (眨眼列表, 循环周期秒)。

    enabled=false、没有有效对象或对象不在周期内时返回空列表，
    窗口侧自动回退到随机眨眼。
    """
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return [], 0.0
    blinks = []
    for it in plan.get("items") or []:
        try:
            t = max(0.0, float(it["t"]))
            hold = min(3.0, max(0.03, float(it.get("hold", 0.2))))
            group = f"g{group_number(it.get('group', 1))}"
            count = min(8, max(1, int(it.get("count") or 2)))
        except (KeyError, TypeError, ValueError):
            continue
        blinks.append(Blink(t=t, hold=hold, group=group, count=count,
                            half=bool(it.get("half", False))))
    blinks.sort(key=lambda b: (b.t, b.group_no))
    if not blinks:
        return [], 0.0
    try:
        cycle = float(plan.get("cycle", 0.0))
    except (TypeError, ValueError):
        cycle = 0.0
    if cycle <= 0:
        cycle = blinks[-1].t + 2.0
    blinks = [b for b in blinks if b.t < cycle]
    if not blinks:
        return [], 0.0
    return blinks, cycle


@dataclass
class Eye:
    cx: float
    cy: float
    rx: float
    ry: float
    score: float = 0.0
    dark: tuple = (30, 30, 30)


def eyes_from_dicts(items: list[dict]) -> list[Eye]:
    """manifest 里的手工标注（像素坐标）-> Eye 列表。"""
    eyes = []
    for it in items:
        try:
            dark = it.get("dark") or (30, 30, 30)
            eyes.append(Eye(cx=float(it["cx"]), cy=float(it["cy"]),
                            rx=max(3.0, float(it["rx"])),
                            ry=max(3.0, float(it["ry"])),
                            score=float(it.get("score", 0.0)),
                            dark=tuple(int(v) for v in dark)))
        except (KeyError, TypeError, ValueError):
            continue
    return eyes


def group_eyes(eyes: list[Eye], counts: list[int] | None = None) -> list[list[Eye]]:
    """按框选顺序分组：默认每两只眼一组（眼1+眼2 = 组1 …）。

    给了 counts 就按每组的眼睛数量依次分配（时间轴里「眼睛数量」列）；
    分配不完的剩余眼睛并入最后一组，避免漏掉不眨的眼。
    """
    if not counts:
        return [eyes[i:i + 2] for i in range(0, len(eyes), 2)]
    out, i = [], 0
    for n in counts:
        if i >= len(eyes):
            break
        out.append(eyes[i:i + max(1, int(n))])
        i += max(1, int(n))
    if i < len(eyes):
        if out:
            out[-1] = out[-1] + eyes[i:]
        else:
            out = [eyes[i:]]
    return out


def blink_group_counts(blinks: list[Blink]) -> list[int]:
    """按眼组编号取每组的眼睛数量；同组多个眨眼事件取第一个，缺号按 2 补。"""
    counts: dict[int, int] = {}
    for b in blinks:
        counts.setdefault(b.group_no, int(b.count))
    if not counts:
        return []
    return [counts.get(g, 2) for g in range(1, max(counts) + 1)]


def blink_phases(blinks: list[Blink], cycle: float, t: float) -> dict[int, int]:
    """当前时刻各眼组的相位：{组号: 0=hold 前段 / 1=全闭}。

    前段长什么样由 `Blink.half` 决定（见 `compose_group_blink` 的 front 参数）：
    `half:true` → ExprBank 遮挡式半眼；`half:false` → make_blink_frames 的
    0.55 遮挡过渡帧（默认观感保持原样）。

    前后段的切分交给 `arbiter.timeline_phase()`，接线层不自己写 `dt < hold/2`；
    `dt == hold/2` 归全闭（严格小于，两段不重叠）。
    """
    if not blinks or cycle <= 0:
        return {}
    from arbiter import timeline_phase       # 纯 Python，无循环依赖
    u = float(t) % float(cycle)
    out: dict[int, int] = {}
    for b in blinks:
        # 这里只借它做「hold 对半切」：把 half 置真，phase=="half" 即前段。
        phase, _ = timeline_phase(
            u * 1000.0, float(cycle) * 1000.0,
            [{"t": float(b.t) * 1000.0, "hold": float(b.hold) * 1000.0,
              "half": True}],
            half_enabled=True)
        if phase is not None:
            out[b.group_no] = 0 if phase == "half" else 1
    return out


def compose_group_blink(base: np.ndarray, active: dict[int, int],
                        group_frames: dict[int, list[np.ndarray]],
                        group_rects: dict[int, tuple[int, int, int, int]],
                        front: dict[int, np.ndarray] | None = None) -> np.ndarray:
    """把各组正在眨眼的眼皮贴回基础帧；**每组只改自己的包围盒**。

    active: {组号: 0 前段 / 1 全闭}
    front:  {组号: 前段帧}（`half:true` 时传 ExprBank 遮挡式半眼）；
            缺省用 `group_frames[组号][0]`（0.55 遮挡过渡帧）。

    这是纯 numpy 版，`PetWindow` 只负责转 QPixmap；多组裁剪逻辑只有这一份。
    """
    out = base.copy()
    for gi, ph in active.items():
        rect = group_rects.get(gi)
        if not rect:
            continue
        x0, y0, x1, y1 = rect
        if x1 <= x0 or y1 <= y0:
            continue
        frames = group_frames.get(gi) or []
        if ph == 0 and front and gi in front:
            frame = front[gi]
        elif ph == 0 and frames:
            frame = frames[0]               # 默认观感：0.55 遮挡过渡帧
        elif frames:
            frame = frames[-1]              # 全闭 = cover 1.0
        else:
            continue
        out[y0:y1, x0:x1] = frame[y0:y1, x0:x1]
    return out



def eye_group_bbox(eyes: list[Eye], pad: float = 2.6) -> tuple[float, float, float, float]:
    """一组眼睛的包围盒，范围与闭眼绘制（约 2.2 倍半径）匹配。"""
    x0 = min(e.cx - e.rx * pad for e in eyes)
    y0 = min(e.cy - e.ry * pad for e in eyes)
    x1 = max(e.cx + e.rx * pad for e in eyes)
    y1 = max(e.cy + e.ry * pad for e in eyes)
    return x0, y0, x1, y1


def _mask_file_index(p) -> int:
    """<stem>_eyes.png → 0；<stem>_eyes_2.png → 1；<stem>_eyes_3.png → 2 …

    序号必须从文件名取，不能按列表下标：`_eyes.png` 缺失而 `_eyes_2.png` 存在时，
    后者仍要给 eye_index 2/3 供位，不能塌回 0/1。
    """
    m = re.search(r"_eyes_(\d+)\.png$", Path(p).name)
    return int(m.group(1)) - 1 if m else 0


def load_eye_slots(paths, shape) -> tuple[dict[int, np.ndarray], np.ndarray | None]:
    """按**眼睛下标**返回 socket：key = 文件序号*2 + 通道（0=R, 1=G）。

    空通道不占位（key 缺失 = 这只眼没有 socket）。用字典而不是扁平列表，
    是为了让下游按 `eye_index` 精确对位，而不是靠 `zip` 静默截断。
    """
    slots: dict[int, np.ndarray] = {}
    hl = None
    for p in paths or []:
        fi = _mask_file_index(p)
        try:
            img = np.array(Image.open(p).convert("RGB"))
        except (OSError, ValueError):
            continue
        if img.shape[:2] != tuple(shape[:2]):
            continue
        for ch in (0, 1):
            m = img[..., ch] > 127
            if m.any():
                slots[fi * 2 + ch] = m
        b = img[..., 2] > 127
        if b.any():
            hl = b if hl is None else (hl | b)
    return slots, hl


def load_eye_masks(paths, shape):
    """读标注工具落盘的 <stem>_eyes.png：R/G = 眼眶 mask，B = 高光。

    返回按 eye_index **补空占位**的扁列表（第 i 项就是第 i 只眼，空通道用
    all-False 占位），编辑器与运行时看到的是同一套下标，不会左移。
    需要区分「没有 socket」时用 `load_eye_slots()`（缺 key = 无）。
    """
    slots, hl = load_eye_slots(paths, shape)
    if not slots:
        return [], hl
    empty = np.zeros(tuple(shape[:2]), bool)
    return [slots.get(k, empty) for k in range(max(slots) + 1)], hl


@dataclass
class PetAsset:
    """一张透明素材 + 锚点 + 动作参数。"""

    rgba: np.ndarray
    anchor: tuple[float, float] = (0.5, 0.99)
    motion: Motion = field(default_factory=Motion)
    eyes: list[Eye] = field(default_factory=list)
    hair: list[HairWisp] = field(default_factory=list)
    cloth: list[HairWisp] = field(default_factory=list)   # 衣服 / 小物件的飘动区域
    blinks: list[Blink] = field(default_factory=list)   # 空列表 = 随机眨眼
    blink_cycle: float = 0.0                            # 眨眼时间轴的循环周期（秒）
    blink_on: bool = True                               # 「眨眼」总开关（界面勾选）
    gaze_on: bool = True                                # 「鼠标跟踪」总开关
    anim: dict = field(default_factory=dict)            # 动作参数（编辑器的「动作参数」）
    eyes_trusted: bool = False                          # 手工框选的眼睛全部可信
    eye_masks: list = field(default_factory=list, repr=False)  # 每只眼的眼眶 mask（扁平，兼容旧路径）
    eye_sockets: dict = field(default_factory=dict, repr=False)  # eye 下标 -> socket
    eye_level: list = field(default_factory=list)  # 每只眼的处理档位 L2/L1/L0/auto
    eye_highlight: np.ndarray | None = field(default=None, repr=False)
    # 参与眨眼/视线/表情的原始 eyes 下标；None = 不过滤（v1 / 无 parts）
    active_eyes: tuple[int, ...] | None = None
    source: Path | None = None
    _group_frames: dict = field(default_factory=dict, repr=False)
    _gaze_geos: list = field(default_factory=list, repr=False)
    _gaze_geos_ready: bool = field(default=False, repr=False)
    _expr_bank: object = field(default=None, repr=False)   # 表情差分贴片库（懒加载）
    _half_blink: object = field(default=None, repr=False)  # 半闭相位整帧（ExprBank 派生）

    @property
    def height(self) -> int:
        return self.rgba.shape[0]

    @property
    def width(self) -> int:
        return self.rgba.shape[1]

    def group_blink_frames(self) -> dict[int, list[np.ndarray]]:
        """按眼组生成眨眼帧：组 1 只动眼1+眼2，组 2 只动眼3+眼4 …（惰性缓存）。

        时间轴里给了「眼睛数量」就按数量分组，与预览保持一致。
        `_active_indices` 非空时，只有 active 的眼会参与眨眼：
        过滤只改变「哪些索引参与」，几何一律取 `self.eyes[idx]`，不重编号。
        """
        if not self._group_frames and self.eyes:
            for gi, idxs in enumerate(self._eye_groups(), 1):
                if not idxs:
                    continue                      # 整组都被过滤：该组不眨眼
                self._group_frames[gi] = make_blink_frames(
                    self.rgba, [self.eyes[i] for i in idxs])
        return self._group_frames

    def _eye_groups(self) -> list[list[int]]:
        """眼组分组的**原始下标**版：先按时间轴分组，再在组内滤掉 inactive。

        组号始终按原始顺序编号（组 2 就是眼3+眼4），过滤只让组内成员变少；
        绝不把过滤后的结果重新切块，否则组的含义会整体左移。
        `_active_indices` 为 None（v1/无 parts）时等于全量。
        """
        all_idx = list(range(len(self.eyes)))
        act = set(all_idx) if self._active_indices is None \
            else set(self._active_indices)
        counts = blink_group_counts(self.blinks) or [
            2] * ((len(all_idx) + 1) // 2)
        chunks, i = [], 0
        for n in counts:
            if i >= len(all_idx):
                break
            chunks.append(all_idx[i:i + max(1, int(n))])
            i += max(1, int(n))
        if i < len(all_idx):
            # 与 group_eyes 的「剩余眼睛并入最后一组」语义保持一致。
            # 这里必须自己分块：group_eyes 会按 Eye.cx 排序，喂下标会 AttributeError；
            # 也不要因为改了实现就改掉这条语义，否则组号与 timeline item 会对不上。
            if chunks:
                chunks[-1] = chunks[-1] + all_idx[i:]
            else:
                chunks = [all_idx]
        return [[k for k in chunk if k in act] for chunk in chunks]

    @classmethod
    def load(cls, path: Path, motion: Motion | None = None,
             eyes: list[dict] | None = None,
             hair: list[dict] | None = None,
             cloth: list[dict] | None = None,
             blink: dict | None = None,
             eye_masks: list | None = None,
             eye_level: list | None = None,
             active_eyes: list[int] | tuple[int, ...] | None = None,
             anim: dict | None = None) -> "PetAsset":
        img = Image.open(path).convert("RGBA")
        rgba = np.array(img)
        asset = cls(rgba=rgba, motion=motion or Motion(), source=path)
        # active_eyes：v2 parts 过滤后的原始下标；None = 不过滤（v1）
        asset.active_eyes = (tuple(active_eyes) if active_eyes is not None
                             else None)
        # eyes=None：没有人工标注，自动检测；eyes=[]：明确不框眼，不眨眼
        asset.eyes_trusted = eyes is not None
        asset.eyes = eyes_from_dicts(eyes) if eyes is not None else detect_eyes(rgba)
        asset.hair = hair_from_dicts(hair, base_dir=path.parent)
        asset.hair = [w for w in asset.hair if w.mask.shape == rgba.shape[:2]]
        asset.cloth = hair_from_dicts(cloth, base_dir=path.parent)
        asset.cloth = [w for w in asset.cloth if w.mask.shape == rgba.shape[:2]]
        # 兼容 {"blink": {...}} 包装与直接给内层 plan 两种写法
        plan = blink if isinstance(blink, dict) else {}
        if isinstance(plan.get("blink"), dict):
            plan = plan["blink"]
        asset.blinks, asset.blink_cycle = blinks_from_plan(plan)
        asset.blink_on = bool(plan.get("blink", plan.get("on", True)))
        asset.gaze_on = bool(plan.get("gaze", True))
        asset.eye_sockets, asset.eye_highlight = load_eye_slots(
            eye_masks, rgba.shape)
        asset.eye_masks = [asset.eye_sockets[k]
                           for k in sorted(asset.eye_sockets)]
        # 逐只眼档位：manifest 里可手改，缺省/auto 时运行时按内部结构自动判
        asset.eye_level = list(eye_level) if isinstance(eye_level, list) else []
        if isinstance(anim, dict):
            asset.anim = dict(anim)
            # 摇摆参数落到 Motion 上，桌宠本体和编辑器预览一致
            try:
                if "sway_deg" in anim:
                    asset.motion.sway_deg = max(0.0, float(anim["sway_deg"]))
                if "sway_speed" in anim:
                    speed = max(0.0, float(anim["sway_speed"]))
                    # 0 = 停住不摇：周期放到极大，角度几乎恒为 0
                    asset.motion.sway_period = (1e6 if speed <= 0
                                                else max(0.6, 5.6 / speed))
            except (TypeError, ValueError):
                pass
        return asset

    def hair_params(self) -> dict:
        """HairField 的可调参数（头发飘动速度 / 摆幅 / 回弹刚度 / 阻尼）。"""
        def val(key, default=1.0):
            try:
                return max(0.0, float(self.anim.get(key, default)))
            except (TypeError, ValueError):
                return default
        return {"wind_speed": val("hair_speed"), "amp": val("hair_amp"),
                "stiffness": val("hair_stiff"), "damping": val("hair_damp")}

    @property
    def _active_indices(self) -> list[int] | None:
        """active 眼的原始下标；None 表示不过滤。"""
        if self.active_eyes is None:
            return None
        n = len(self.eyes)
        return [i for i in self.active_eyes if 0 <= i < n]

    def blink_frames(self) -> list[np.ndarray]:
        idx = self._active_indices
        eyes = self.eyes if idx is None else [self.eyes[i] for i in idx]
        return make_blink_frames(self.rgba, eyes) if eyes else []

    def half_blink_frame(self) -> np.ndarray | None:
        """时间轴 `half:true` 前半段用的整帧：`ExprBank.apply(base, 'half')`。

        半闭只走遮挡式贴片（眼球零形变）。`make_blink_frames` 的 cover=0.55
        帧同样是遮挡式（填肤色 + 弧线），但它按椭圆近似眼眶、睑缘是手画抛物
        线；`half:true` 用 ExprBank 是因为它按真实 socket mask 遮挡、睑缘直接
        复用原 lash 层。两者都不压缩眼球。
        没有可用眼眶 mask 时返回 None（调用方退回 0.55 过渡帧）。
        """
        if self._half_blink is None:
            bank = self.expr_bank()
            self._half_blink = (bank.apply(self.rgba, "half")
                                if bank is not None else False)
        return None if self._half_blink is False else self._half_blink

    def gaze_geos(self) -> list:
        """视线的眼睛几何：在素材分辨率下算一次，之后所有显示尺寸/偏移复用。

        同一只眼在不同显示尺寸下重新检测虹膜，结果会因缩放抗锯齿而漂移，
        偶尔还会检测失败退回兜底路径，看起来就是“有时候糊、有时候正常”。
        素材分辨率是固定的，几何也固定下来最稳。
        """
        if not self._gaze_geos_ready:
            # active_eye_indices 是唯一真源：这里不再留「有 socket 就不过滤」的特例。
            idx = self._active_indices
            if idx is None:
                idx = list(range(len(self.eyes)))
            eyes = [self.eyes[i] for i in idx]
            levels = [self.eye_level[i] if i < len(self.eye_level) else None
                      for i in idx]
            # 按 eye_index 逐项对位；缺 socket 的眼下传 None（该眼不参与视线），
            # 不做 zip 截断。数量对不上时显式提示，而不是静默错位。
            sockets = [self.eye_sockets.get(i) for i in idx]
            got = sum(1 for s in sockets if s is not None)
            if self.eye_sockets and got != len(idx):
                print(f"[warn] socket 数 {got} ≠ 参与眼数 {len(idx)}"
                      f"（{self.source.name if self.source else '?'}）："
                      f"缺 socket 的眼不会参与视线，请补 mask 或重标",
                      file=sys.stderr)
            self._gaze_geos = build_gaze_geos(
                self.rgba, eyes, trusted=self.eyes_trusted,
                sockets=sockets, highlight=self.eye_highlight, levels=levels,
                require_socket=self._active_indices is not None)
            self._gaze_geos_ready = True
        return self._gaze_geos

    def expr_bank(self):
        """表情差分贴片库（半眼/惊讶/单侧眨眼）。

        独立于视线与眨眼：旧路径（眨眼帧 + gaze_frame）完全不动，这里只是
        额外生成一组「整块盖住眼睛」的贴片，状态非 normal 时叠在帧上。
        没有可用的眼眶 mask（自动检测不到眼睛）时返回 None。
        """
        if self._expr_bank is None:
            self._expr_bank = build_expr_bank(self.rgba, self.gaze_geos()) or False
        return self._expr_bank or None


def detect_eyes(rgba: np.ndarray, upper: float = 0.62) -> list[Eye]:
    """自动找一对眼睛；找不到返回空列表（调用方应跳过眨眼）。

    判据：上半身、暗色块、面积适中、两个块尺寸相近且水平并排。
    阈值从暗到亮依次尝试：灰头发/浅色皮肤在 <120 时会和眼线连成一片，
    所以先用 70 这类“只保留近黑”的阈值，失败再放宽。
    """
    w = rgba.shape[1]
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > 0.05)
    if len(xs) == 0:
        return []
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    roi_y1 = int(y0 + (y1 - y0) * upper)
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    area_all = float((x1 - x0) * (y1 - y0))

    for thr in (70, 85, 100, 115):
        dark = ((gray < thr) & (alpha > 80)).astype(np.uint8)
        dark[:y0, :] = 0
        dark[roi_y1:, :] = 0
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        n, labels, stats, cents = cv2.connectedComponentsWithStats(dark, 8)
        cands = []
        for i in range(1, n):
            _, _, cw, ch, area = stats[i]
            if area < area_all * 0.002 or area > area_all * 0.09:
                continue
            if ch < 4 or cw < 4:
                continue
            aspect = cw / float(ch)
            if not 0.35 <= aspect <= 3.2:
                continue
            if (cents[i][1] - y0) / float(y1 - y0) > upper:
                continue
            cands.append((i, cents[i][0], cents[i][1], cw, ch, area))

        best, best_score = [], 0.0
        for a in range(len(cands)):
            for b in range(a + 1, len(cands)):
                _, ax, ay, aw, _, aa = cands[a]
                _, bx, by, bw, _, ba = cands[b]
                if min(aa, ba) / max(aa, ba) < 0.45:
                    continue
                if abs(ay - by) > (y1 - y0) * 0.14:
                    continue
                sep = abs(ax - bx)
                if sep < w * 0.06 or sep > w * 0.62:
                    continue
                score = (aa + ba) * (1.0 - abs(aw - bw) / max(aw, bw))
                if score > best_score:
                    best_score = score
                    best = [cands[a], cands[b]]
        if not best:
            continue

        eyes = []
        for idx, cx, cy, cw, ch, _ in best:
            mask = (labels == idx) & (gray < 140)
            vals = rgba[..., :3][mask]
            if len(vals):
                gl = gray[mask]
                sel = vals[gl <= np.percentile(gl, 25)]
                dark_col = tuple(int(v) for v in np.median(sel, axis=0))
            else:
                dark_col = (30, 30, 30)
            eyes.append(Eye(cx=float(cx), cy=float(cy),
                            rx=max(3.0, cw / 2.0 * 1.05), ry=max(3.0, ch / 2.0 * 1.05),
                            score=float(best_score), dark=dark_col))
        return eyes
    return []


def _eye_skin_color(rgb: np.ndarray, alpha: np.ndarray, eye: Eye, gray: np.ndarray) -> np.ndarray:
    """从眼睛外圈取肤色：排除暗像素，避免把眼线当成皮肤。

    只在眼睛周围 2.4 倍半径的小块里统计，不再对整幅图建 mgrid——
    多眼素材里每只眼每换一档偏移都建一次全图坐标，是最容易掉帧的地方。
    """
    h, w = alpha.shape
    x0 = max(0, int(eye.cx - eye.rx * 2.4))
    x1 = min(w, int(eye.cx + eye.rx * 2.4) + 1)
    y0 = max(0, int(eye.cy - eye.ry * 2.4))
    y1 = min(h, int(eye.cy + eye.ry * 2.4) + 1)
    if x1 <= x0 or y1 <= y0:
        return np.array([210, 210, 210], np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dist = ((xx - eye.cx) / max(eye.rx, 1e-3)) ** 2 + ((yy - eye.cy) / max(eye.ry, 1e-3)) ** 2
    a = alpha[y0:y1, x0:x1]
    g = gray[y0:y1, x0:x1]
    ring = (dist >= 1.15 ** 2) & (dist <= 1.9 ** 2) & (a > 128)
    if ring.sum() < 12:
        ring = (dist <= 2.4 ** 2) & (a > 128)
    if ring.sum() == 0:
        return np.array([210, 210, 210], np.float32)
    # 深色眼（黑猫、深色兽瞳）的眼眶周围也是深色：闭眼遮罩必须用同一层次的深色，
    # 否则会在黑毛上糊出一块浅灰饼。浅色眼才用外圈“够亮”的肤色，避免黑发把肤色带偏
    if float(np.median(g[dist <= 1.0])) < 70.0:
        return np.median(rgb[y0:y1, x0:x1][ring].astype(np.float32), axis=0)
    bright = ring & (g > 100)
    if bright.sum() >= 12:
        ring = bright
    return np.median(rgb[y0:y1, x0:x1][ring].astype(np.float32), axis=0)


def _draw_closed_eye(out_rgb: np.ndarray, alpha: np.ndarray, eye: Eye,
                     cover: float, skin: np.ndarray, closed: bool):
    """用椭圆遮罩把眼睛逐步“盖上”，closed 时再加一条下弯的眼睑线。"""
    h, w = alpha.shape
    x0 = max(0, int(eye.cx - eye.rx * 2.2))
    x1 = min(w, int(eye.cx + eye.rx * 2.2) + 1)
    y0 = max(0, int(eye.cy - eye.ry * 2.2))
    y1 = min(h, int(eye.cy + eye.ry * 2.2) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    # 遮盖椭圆比眼眶略大：用户框的框常常卡着虹膜边缘，等尺寸遮罩会漏出一圈彩色边
    crx, cry = eye.rx * 1.05, eye.ry * 1.08
    ell = ((xx - eye.cx) / crx) ** 2 + ((yy - eye.cy) / cry) ** 2 <= 1.0
    if cover < 1.0:
        # 上眼睑是从上方压下来的弧线：中间压得更低、两端留出眼角，
        # 直接按水平线截断会留下一条笔直的边，半闭帧看起来像眼睛被切了一半
        lid = (eye.cy - cry + 2.0 * cry * cover
               + cry * 0.45 * (1.0 - np.clip((xx - eye.cx) / max(1.0, eye.rx), -1.0, 1.0) ** 2))
        ell &= yy <= lid

    soft = cv2.GaussianBlur(ell.astype(np.float32), (0, 0), 1.2)[..., None]
    roi = out_rgb[y0:y1, x0:x1]
    roi[:] = roi * (1.0 - soft) + skin[None, None, :] * soft

    if closed:
        # 深色眼眶（黑猫）上，纯黑眼睑线完全看不见，按肤色提亮成同色系的浅线
        if float(skin.mean()) < 90.0:
            lid_col = tuple(int(min(255, v + (255.0 - v) * 0.35)) for v in skin)
        else:
            lid_col = eye.dark
        canvas = np.ascontiguousarray(out_rgb)
        cv2.ellipse(canvas,
                    (int(round(eye.cx)), int(round(eye.cy + eye.ry * 0.15))),
                    (max(2, int(eye.rx * 0.9)), max(2, int(eye.ry * 0.55))),
                    0.0, 200.0, 340.0, lid_col,
                    max(1, int(eye.ry * 0.18)), lineType=cv2.LINE_AA)
        out_rgb[:] = canvas


def make_blink_frames(rgba: np.ndarray, eyes: list[Eye],
                      covers: tuple[float, ...] = (0.55, 1.0)) -> list[np.ndarray]:
    """返回半闭、全闭两帧（RGBA）。没有眼睛时返回空列表。"""
    if not eyes:
        return []
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    frames = []
    for cover in covers:
        out = rgba.copy()
        for eye in eyes:
            skin = _eye_skin_color(rgba[..., :3], rgba[..., 3], eye, gray)
            _draw_closed_eye(out[..., :3], rgba[..., 3], eye, cover, skin,
                             closed=(cover >= 1.0))
        frames.append(out)
    return frames


def _ellipse(h: int, w: int, cx: float, cy: float,
             rx: float, ry: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - cx) / max(0.5, rx)) ** 2 + ((yy - cy) / max(0.5, ry)) ** 2 <= 1.0


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """填掉被前景包住的洞（虹膜内部的亮色区会被误判成洞）。"""
    m = mask.astype(np.uint8)
    h, w = m.shape
    inv = (1 - m).astype(np.uint8)
    tmp = inv.copy()
    ff = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(tmp, ff, (0, 0), 2)
    holes = (inv == 1) & (tmp == 1)
    return mask | holes


def _nearest_fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """用最近的未遮挡像素一圈圈往洞里填。

    瞳孔底下的虹膜常是「上深下浅」的分色块，cv2.inpaint 会在瞳孔原位留下
    一团偏亮的斑；按最近的已知像素填，分色界线会沿原方向延续下去，
    瞳孔滑走后原位看起来就是本来的眼珠。
    """
    out = rgb.astype(np.float32).copy()
    hole = hole.astype(bool)
    known = ~hole
    h, w = hole.shape
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1))
    for _ in range(max(h, w)):
        if known.all():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros((h, w), np.float32)
        for dy, dx in dirs:
            src = (slice(max(0, dy), h + min(0, dy)),
                   slice(max(0, dx), w + min(0, dx)))
            dst = (slice(max(0, -dy), h + min(0, -dy)),
                   slice(max(0, -dx), w + min(0, -dx)))
            s = np.zeros_like(out)
            k = np.zeros((h, w), bool)
            s[dst] = out[src]
            k[dst] = known[src]
            acc += s * k[..., None]
            cnt += k
        new = (~known) & (cnt > 0)
        out[new] = acc[new] / cnt[new][..., None]
        known |= new
    return np.clip(out, 0, 255).astype(np.uint8)


class _EyeGeo:
    """单只眼的图层拆解：像素级虹膜 mask / 位移权重 / 纹理 / 高光 / 眼眶。"""

    __slots__ = ("box", "weight", "whole_weight", "open_soft", "socket",
                 "texture", "hl", "split_hl", "pupil", "pupil_base",
                 "pupil_rx", "pupil_ry", "limit_x", "limit_y", "enabled",
                 "level", "mode", "amp", "feather", "spec_follow", "converge",
                 "layers", "room")

    def __init__(self, **kw):
        self.pupil = None
        self.pupil_base = None
        self.pupil_rx = 0.0
        self.pupil_ry = 0.0
        self.hl = None
        self.socket = None
        self.split_hl = False
        self.limit_x = 1.5
        self.limit_y = 1.5
        self.enabled = True
        self.whole_weight = None
        self.level = "L0"
        self.mode = "whole"
        self.amp = 1.2
        self.feather = 6.0
        self.spec_follow = 0.0
        self.converge = 0.0
        self.layers = None
        self.room = None            # L2 贴片四向可挪余量 (左,右,上,下)；None=不按方向限
        for k, v in kw.items():
            setattr(self, k, v)


# 按「眼睛内部有几层」而不是物种来选处理方式：
# L2 = 眼白 + 虹膜 + 瞳孔 + 高光，抠出任意形状的虹膜贴片平移，
#      实心眼白盘托底所以不会露底；amp 由几何（虹膜半径 + 眼白余量）自动决定，
#      这里的 5.0 只是分层构建失败时的兜底幅度；
# L1 = 虹膜 + 高光、几乎没有眼白，整块微移、高光钉死；
# L0 = 单一色块 + 一个高光点，没有「眼球」语义，幅度压到极小，主力交给眨眼。
EYE_PARAM = {
    "L2": dict(mode="iris", amp=5.0, feather=2.5,
               spec_follow=1.0, converge=0.15),
    "L1": dict(mode="whole", amp=2.5, feather=4.0,
               spec_follow=0.0, converge=0.0),
    "L0": dict(mode="whole", amp=1.2, feather=6.0,
               spec_follow=0.0, converge=0.0),
}


def eye_structure_level(rgb: np.ndarray, eye_mask: np.ndarray) -> str:
    """按眼睛内部结构自动判 L0/L1/L2（与物种无关）。

    眼白 = 低彩度 + 高亮；暗核 = 暗且靠近中心；虹膜 = 有彩度的主色块。
    图 9 那种低清放大 + 锯齿的图自动判容易摇摆，manifest 里允许手工覆盖。
    """
    ys, xs = np.nonzero(eye_mask)
    if len(xs) < 50:
        return "L0"
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    patch = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    m = eye_mask[y0:y1, x0:x1] > 0
    if patch.size == 0 or m.sum() < 30:
        return "L0"

    hsv = cv2.cvtColor(patch.astype(np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32)
    V = hsv[..., 2].astype(np.float32)
    Sm, Vm = S[m], V[m]
    area = float(m.sum())

    sclera_ratio = float(((Sm < 55) & (Vm > 190)).sum() / area)

    dark = (V < 0.42 * np.percentile(Vm, 95)) & m
    has_core = float(dark.sum()) / area > 0.03
    if dark.sum() > 10:
        dys, dxs = np.nonzero(dark)
        cy = dys.mean() - (y1 - y0) / 2.0
        cx = dxs.mean() - (x1 - x0) / 2.0
        r = float(np.hypot(y1 - y0, x1 - x0)) / 2.0 + 1e-6
        has_core = has_core and float(np.hypot(cy, cx)) < 0.45 * r

    chroma_ratio = float((Sm > 70).sum() / area)

    if sclera_ratio > 0.12 and has_core and chroma_ratio > 0.15:
        return "L2"
    if chroma_ratio > 0.15:
        return "L1"
    return "L0"


def _pick_eye_component(mask: np.ndarray, socket: np.ndarray, lx: float,
                        ly: float, rx: float, ry: float,
                        min_frac: float) -> np.ndarray | None:
    """在眼眶内挑一块「像眼珠」的连通域：够大、质心靠中心、形状不细长。"""
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                         np.ones((2, 2), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m, 8)
    need = max(6, int(socket.sum() * min_frac))
    best = None
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < need:
            continue
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        # 睫毛线 / 上眼睑阴影是贴着外圈的细长弧：质心离中心远、圆度低，
        # 都会被压下去，不会再被当成虹膜
        ox = (cents[i][0] - lx) / max(1.0, rx)
        oy = (cents[i][1] - ly) / max(1.0, ry)
        if ox * ox + oy * oy > 0.75 * 0.75:
            continue
        roundness = min(cw, ch) / float(max(cw, ch))
        score = area * (0.4 + 0.6 * roundness) / (1.0 + 2.0 * (ox * ox + oy * oy))
        if best is None or score > best[0]:
            best = (score, i)
    return None if best is None else labels == best[1]


def _iris_mask(rgb: np.ndarray, socket: np.ndarray, lx: float, ly: float,
               rx: float, ry: float, strict: bool = False) -> np.ndarray | None:
    """眼眶内的虹膜 mask：彩度判据 + 连通域挑选，直接保留像素形状。

    不用 fitEllipse：动漫虹膜是「圆盘被上眼皮切掉一块」的 D 形，椭圆拟合
    只能得到圆心偏下、短半径偏小的有偏结果（虹膜看着贴在睫毛上往上飘）。
    mask 没有形状假设，被睫毛遮住的部分本来就不在 mask 里。

    strict=True 是「用户手画过眼眶」的路径：粗结果再按颜色种子吸附一次边界
    并做四条断言，不合格就返回 None 让上层退回整块微移。自动椭圆框不启用，
    保持原来的宽松策略（小尺寸/单色虹膜的装饰眼不会被误停）。
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    sat, val = hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    r = rgb[..., 0].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    # 冷色（青/蓝/灰蓝）允许低饱和：动漫淡色虹膜 sat 只有 0.1 左右；
    # 暖色（红/金/琥珀）要求饱和度高，避免把肤色（R>B、sat≈0.09）当成虹膜
    colored = (((b >= r) & (sat >= 0.05))
               | ((b < r) & (sat >= 0.22))) & (val > 0.12)
    for cand, min_frac in ((colored & socket, 0.05),
                           (socket & (val < 0.40), 0.12)):
        comp = _pick_eye_component(cand, socket, lx, ly, rx, ry, min_frac)
        if comp is None:
            continue
        rough = _fill_holes(comp) & socket
        if not strict:
            return rough
        # 粗结果可能把眼白/上眼睑吃进来：按颜色种子吸附回真实边界，再校验一次。
        # 校验不过就试下一个候选，都不行才返回 None——上层退回「整块微移」，
        # 不会拖着眼白跑，也不会把这只眼整个停掉。
        fixed = _refine_iris(rgb, socket, rough, keep_spec=True)
        if fixed is None:
            fixed = rough
        ok, _why = _validate_iris(rgb, socket, fixed)
        if ok:
            return fixed
    return None


def _build_weight_field(socket: np.ndarray, iris: np.ndarray | None = None):
    """距离变换权重场：眼眶内位移，眶外恒为 0。

    眼眶是像素级 mask 时，框外皮肤/眉毛/头发的权重直接是 0，不会再跟着眼球
    一起挪（这正是矩形框最大的问题）。斜坡有两种取法：
    - 用法 A（没有虹膜）：ramp = 0.55 × max(d)，中央自动形成权重≈1 的平台，
      正面立绘的虹膜本来就在眼眶中央；
    - 用法 B（找到虹膜）：ramp 取虹膜内距离的 90 分位，跟着虹膜尺度走，
      再把虹膜本体抬到 1（羽化 1.2px 接上外圈衰减），虹膜是真正的刚体。
    """
    d = cv2.distanceTransform(socket.astype(np.uint8), cv2.DIST_L2, 5)
    has_iris = iris is not None and int(iris.sum()) >= 12
    if has_iris:
        ramp = max(float(np.percentile(d[iris], 90)), 1.0)
    else:
        ramp = max(0.55 * float(d.max()), 1.0)
    w = np.clip(d / ramp, 0.0, 1.0)
    w = cv2.GaussianBlur(w, (0, 0), ramp * 0.25)
    w[socket == 0] = 0.0
    if has_iris:
        soft = cv2.GaussianBlur(iris.astype(np.float32), (0, 0), 1.2)
        w = np.maximum(w, soft)
        w[socket == 0] = 0.0
    return w, ramp, d


def _edge_gaps(socket: np.ndarray, iris: np.ndarray, pct: float | None = None):
    """虹膜四侧到眼眶边界的余量（像素）：哪边还有眼白，哪边就还能挪。

    pct=None 取每行/列的最小间隙（最保守：虹膜任何一行都不会越过眶沿）；
    pct 给定时取该分位数——虹膜上下收圆的边缘行/列间隙天然偏小，按最小值
    会把「其实还有眼白」的眼判成没余量而整只停用，中位数更接近实际空档。
    """
    h, w = socket.shape
    gl: list[float] = []
    gr: list[float] = []
    gu: list[float] = []
    gd: list[float] = []
    for y in range(h):
        s = np.flatnonzero(socket[y])
        i = np.flatnonzero(iris[y])
        if len(s) and len(i):
            gl.append(float(i[0] - s[0]))
            gr.append(float(s[-1] - i[-1]))
    for x in range(w):
        s = np.flatnonzero(socket[:, x])
        i = np.flatnonzero(iris[:, x])
        if len(s) and len(i):
            gu.append(float(i[0] - s[0]))
            gd.append(float(s[-1] - i[-1]))

    def agg(v: list[float]) -> float:
        if not v:
            return 0.0
        val = float(np.min(v)) if pct is None else float(np.percentile(v, pct))
        return max(0.0, val)

    return agg(gl), agg(gr), agg(gu), agg(gd)


_K3 = np.ones((3, 3), np.uint8)


def _largest_blob(mask: np.ndarray) -> np.ndarray:
    """只保留面积最大的连通域。"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return mask.astype(bool)
    return lab == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))


def _iris_spec_mask(rgb: np.ndarray, socket: np.ndarray) -> np.ndarray:
    """眶内高光：极亮、无彩度、且**不贴眶沿**的小块。

    贴眶沿的亮块是眼白或皮肤（那是被粗框多框进来的），必须留在外面；
    被虹膜整圈包住的那一小撮才是高光。
    """
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)
    m = socket.astype(bool)
    bright = (V >= 225) & (S < 55) & m
    bright = cv2.morphologyEx(bright.astype(np.uint8), cv2.MORPH_OPEN, _K3) > 0
    if not bright.any():
        return np.zeros_like(m)
    rim = m & ~(cv2.erode(m.astype(np.uint8), _K3, iterations=2) > 0)
    n, lab, st, _ = cv2.connectedComponentsWithStats(bright.astype(np.uint8), 8)
    spec = np.zeros_like(m)
    for i in range(1, n):
        blob = lab == i
        if (blob & rim).any():
            continue                      # 连着眶沿：眼白/皮肤，不算高光
        if st[i, cv2.CC_STAT_AREA] > 0.35 * int(m.sum()):
            continue
        spec |= blob
    return spec


def _refine_iris(rgb: np.ndarray, socket: np.ndarray, rough: np.ndarray,
                 keep_spec: bool = True) -> np.ndarray | None:
    """粗虹膜 -> 颜色种子 GrabCut 吸附：把边界拉回真实的颜色跳变处。

    种子只用 H/S 判断「是不是同一块虹膜」：动漫虹膜常见上深下亮，那是同一块
    的明暗渐变，按 V 分会把深蓝上半判成虹膜、青蓝下半判成眼白。V 只在两处
    出现——排掉高光（极亮低彩度）和贴上眼眶的深色眼睑/睫毛（V<55）。
    粗框本身允许多框进眼白或上眼睑，GrabCut 会按颜色把边界咬回去。

    返回 None 表示这次吸附不可信（种子太少/结果跑出眼眶），调用方沿用粗结果。
    """
    socket = socket.astype(bool)
    rough = rough.astype(bool) & socket
    if int(rough.sum()) < 20 or int(socket.sum()) < 50:
        return None

    spec = _iris_spec_mask(rgb, socket)
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)
    r = rgb[..., 0].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    sat, val = S / 255.0, V / 255.0
    # 和 _iris_mask 同一套彩度语义：冷色（青/蓝/灰蓝）允许低饱和，暖色要求够艳
    colored = (((b >= r) & (sat >= 0.05))
               | ((b < r) & (sat >= 0.22))) & (val > 0.12)

    iris_seed = rough & colored & ~spec & (V >= 30)          # 确定前景：虹膜两半都在这
    bg_seed = socket & (((S < 45) & (V > 165)) | (V < 55)) & ~spec   # 眼白/皮肤 + 眼睑
    if int(iris_seed.sum()) < 8:
        return None

    roi = cv2.dilate(socket.astype(np.uint8), _K3, iterations=3) > 0
    gc = np.full(socket.shape, cv2.GC_BGD, np.uint8)
    gc[roi] = cv2.GC_PR_BGD
    # 「可能前景」给整个眼眶：粗虹膜可能被眼睑压掉一块、也可能多咬进眼白，
    # 让 GrabCut 自己按颜色咬回去，比把一个已经错了的形状当硬边界更稳
    gc[socket] = cv2.GC_PR_FGD
    gc[iris_seed] = cv2.GC_FGD
    gc[bg_seed] = cv2.GC_BGD
    gc[spec] = cv2.GC_BGD
    try:
        cv2.grabCut(np.ascontiguousarray(rgb, np.uint8), gc, None,
                    np.zeros((1, 65), np.float64),
                    np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None

    out = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)) & socket
    out = cv2.morphologyEx(out.astype(np.uint8), cv2.MORPH_CLOSE, _K3,
                           iterations=2) > 0
    out = _largest_blob(out)
    # 瞳孔/深色瞳纹被「暗=眼睑」的种子挖掉了，这里补回被虹膜包住的洞；
    # 但只补暗洞——亮洞是眼白/皮肤，补进去就是刚才想排掉的错。
    light = (S < 45) & (V > 165)
    out = out | (_fill_holes(out) & ~light)
    if keep_spec:
        out |= rough & spec
    else:
        out &= ~spec        # L2 里高光是单独一层，虹膜贴片不重复占
    cover = float(out.sum()) / max(1.0, float(socket.sum()))
    if not out.any() or cover < 0.05 or cover > 0.97:
        return None
    return out


def _validate_iris(rgb: np.ndarray, socket: np.ndarray,
                   iris: np.ndarray) -> tuple[bool, str]:
    """四条断言，任一不过就静默降级：宁可不启用视线，也不要拖着眼白跑。"""
    s = socket.astype(bool)
    m = iris.astype(bool) & s
    if int(m.sum()) < 20 or int(s.sum()) < 40:
        return False, "面积过小"
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32)
    V = hsv[..., 2].astype(np.float32)

    ratio = float(m.sum()) / float(s.sum())
    if not (0.12 < ratio < 0.92):
        return False, f"虹膜/眼眶占比 {ratio:.2f}"

    # 混入眼白会把框内彩度均值拉低
    s_in = float(S[m].mean())
    ring = (cv2.dilate(m.astype(np.uint8), _K3, iterations=2) > 0) & ~m & s
    s_out = float(S[ring].mean()) if int(ring.sum()) > 20 else s_in
    if s_in < s_out + 12:
        return False, f"彩度对比不足 in={s_in:.0f} out={s_out:.0f}"

    # 高光已经单独成层，这里只抓「成片的低彩度高亮」= 眼白/皮肤漏进来了
    spec = _iris_spec_mask(rgb, s)
    white_in = float((((V > 195) & (S < 55)) & m & ~spec).sum()) / float(m.sum())
    if white_in > 0.10:
        return False, f"混入眼白 {white_in:.2f}"

    # 虹膜不该贴到眶沿：中心到眶边太近说明框偏了
    d = cv2.distanceTransform(s.astype(np.uint8), cv2.DIST_L2, 5)
    M = cv2.moments(m.astype(np.uint8))
    cx = M["m10"] / max(M["m00"], 1.0)
    cy = M["m01"] / max(M["m00"], 1.0)
    if d[int(cy), int(cx)] < 0.25 * float(d.max()):
        return False, "虹膜中心贴边"
    return True, "ok"


def _soft_mask(mask: np.ndarray, px: float = 1.2) -> np.ndarray:
    """mask -> 归一化后的软 alpha（内部 1、边界 px 像素内衰减）。"""
    a = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), max(float(px), 1e-3))
    return np.clip(a / max(float(a.max()), 1e-6), 0.0, 1.0)


def _grow_iris_rim(rgb: np.ndarray, iris: np.ndarray, socket: np.ndarray,
                   core: np.ndarray, sclera: np.ndarray,
                   spec: np.ndarray) -> np.ndarray:
    """把虹膜贴片扩到整块虹膜，判据只看「相对眼白」的彩度与明度差。

    区域生长的几何/彩度下限会把贴着眼眶的虹膜上下缘排除在外——那段过渡色
    只是更浅、彩度更低（淡蓝、浅棕、深灰都有），并不是眼白。漏掉的这部分
    留在眼白盘上原地不动，表现就是下缘一小块不跟着视线走。

    判据不能只比「暗几个亮度」：眼白本身有阴影和渐变，差几个亮度就把整片
    眼白当成虹膜吞进来，贴片盖满眼眶后眼白盘没地方托底，平移时原位会露出
    填平的肤色。也不能只看最近邻色：浅蓝虹膜在 RGB 上离白眼球比离深蓝
    虹膜更近，会被判成眼白，贴片只带走深色上半，平移时深浅交界处裂开。
    这里用彩度为主：彩度明显高于眼白（浅蓝/浅黄/红/绿都算），或明度明显
    低于眼白（深灰/黑）才算虹膜；眼白无论偏蓝还是偏暖，彩度都低、亮度都高。
    """
    if not iris.any():
        return iris
    m = socket.astype(bool) & ~spec
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32)
    V = hsv[..., 2].astype(np.float32)
    if int(core.sum()) >= 8 and int(sclera.sum()) >= 8:
        s_ref = float(np.median(S[sclera]))
        v_white = float(np.median(V[sclera]))
        colored = S >= max(s_ref + 25.0, 45.0)
        darker = V <= 0.70 * v_white
        ok = m & (colored | darker)
    else:
        # 没有可信眼白（整颗都是虹膜的玩具眼）：只吞明显比眶内亮部更暗的像素
        v_ref = float(np.percentile(V[m], 90)) if m.any() else 235.0
        ok = m & (V <= 0.62 * v_ref)
    out = iris.copy()
    # 最多扩到眼眶内半径那么多层，极端形状下也不会无休止外溢
    cap = max(1, int(cv2.distanceTransform(socket.astype(np.uint8),
                                           cv2.DIST_L2, 5).max()) + 1)
    for _ in range(cap):
        add = (cv2.dilate(out.astype(np.uint8), _K3) > 0) & ok & ~out
        if not add.any():
            break
        out |= add
        if int(out.sum()) > 0.90 * int(m.sum()):
            break
    # 只加不减：原有虹膜像素（高光/低彩度/被判成眼白的部分）不能被滤掉，
    # 否则贴片会被打洞，平移后原处留下碎片状残影
    return out | iris


def _accept_iris(rgb: np.ndarray, socket: np.ndarray, iris: np.ndarray,
                 core: np.ndarray, sclera: np.ndarray,
                 spec: np.ndarray, strict: bool = False) -> dict | None:
    """虹膜生长完再做一次颜色吸附 + 校验（仅手画眼眶的 strict 路径）。

    生长时的「比眼白暗」判据会把压在上缘的深色眼睑一并吞进来（同一块明暗
    渐变的虹膜上半也被算成「暗」），吸附按颜色把边界咬回去；校验不过就
    返回 None，外层把 L2 降级成整块微移，绝不让眼白/眼睑跟着虹膜平移。
    """
    if strict:
        fixed = _refine_iris(rgb, socket, iris, keep_spec=False)
        if fixed is not None:
            iris = fixed
        ok, _why = _validate_iris(rgb, socket, iris | spec)
        if not ok:
            return None
    return dict(sclera=sclera, iris=iris, iris_core=core, spec=spec)


def _segment_eye(rgb: np.ndarray, socket: np.ndarray,
                 strict: bool = False) -> dict | None:
    """在眼眶内分出 眼白 / 虹膜 / 高光，不假设虹膜是圆或径向对称。

    不用全局 Otsu：深色虹膜的 V 会正好压在阈值上被判成眼白。这里只用
    「高置信眼白」的极端值定义候选区，再从眶中央取种子做区域生长，
    ds 阈值由松到紧自适应，吞掉大半个眼眶就收紧重试。
    """
    m = socket.astype(bool)
    if int(m.sum()) < 50:
        return None
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)

    # 高光：眶内极亮的一小撮
    spec = (V >= max(float(np.percentile(V[m], 96)), 225.0)) & (S < 70) & m
    spec = cv2.morphologyEx(spec.astype(np.uint8), cv2.MORPH_OPEN, _K3) > 0
    # 纯白的眼白（V≥225 且低彩度）会整片撞上高光判据：高光不可能占半个眼眶，
    # 超过就按眼白算，否则眼白会被当成高光层导致虹膜 mask 长满整个眼眶
    if int(spec.sum()) > 0.35 * int(m.sum()):
        spec = np.zeros_like(spec)

    # 高置信眼白：只取最亮且几乎无彩度的像素
    rest = m & ~spec
    if not rest.any():
        return None
    v_hi = float(np.percentile(V[rest], 75))
    sclera = (V >= v_hi) & (S <= 40) & rest

    ds = cv2.distanceTransform(socket.astype(np.uint8), cv2.DIST_L2, 5)
    R = float(ds.max())
    if R < 2.0 or int((rest & ~sclera).sum()) < 20:
        return None

    # 种子：眼眶中央区域里最「不像眼白」的点（够暗 或 彩度高）
    inner = ds >= 0.45 * R
    cand = rest & ~sclera & inner
    if int(cand.sum()) < 10:
        cand = rest & ~sclera
    if not cand.any():
        return None
    sc = np.maximum(S / 255.0, (255.0 - V) / 255.0).astype(np.float32)
    sc = cv2.GaussianBlur(np.where(cand, sc, 0.0).astype(np.float32), (0, 0), 2.0)
    sy, sx = np.unravel_index(int(np.argmax(np.where(cand, sc, -1.0))), sc.shape)

    # 区域生长：ds 阈值由松到紧自适应，避免漏进睫毛/眼睑阴影
    for k in (0.18, 0.30, 0.42, 0.55):
        limit = rest & ~sclera & (ds >= k * R)
        if not bool(limit[sy, sx]):
            continue
        iris = np.zeros(m.shape, bool)
        iris[sy, sx] = True
        for _ in range(int(3 * R) + 8):
            nxt = cv2.dilate(iris.astype(np.uint8), _K3) > 0
            nxt &= limit
            if np.array_equal(nxt, iris):
                break
            iris = nxt
        if int(iris.sum()) < 20:
            continue
        # 吞掉大半个眼眶说明漏到眼睑/睫毛了 → 收紧重试
        if int(iris.sum()) > 0.80 * int(m.sum()) and k < 0.55:
            continue
        core = iris
        iris = _grow_iris_rim(rgb, iris.copy(), socket, core, sclera, spec)
        return _accept_iris(rgb, socket, iris, core, sclera, spec, strict)

    core = _largest_blob(rest & ~sclera)
    iris = _grow_iris_rim(rgb, core.copy(), socket, core, sclera, spec)
    return _accept_iris(rgb, socket, iris, core, sclera, spec, strict)


def _extend_ring(rgb: np.ndarray, mask: np.ndarray, px: int = 6):
    """把贴片向外扩 px 层，用自身边缘颜色逐层延展。

    不重建、不套形状假设：只把边缘颜色往外涂，任意瞳纹都保留。
    """
    cur = mask.astype(np.uint8).copy()
    out = rgb.astype(np.float32)
    kf = np.ones((3, 3), np.float32)
    for _ in range(max(0, int(px))):
        nxt = cv2.dilate(cur, _K3)
        ring = (nxt > 0) & (cur == 0)
        if not ring.any():
            break
        cnt = cv2.filter2D(cur.astype(np.float32), -1, kf)
        for c in range(3):
            src = np.where(cur > 0, out[..., c], 0.0).astype(np.float32)
            out[..., c] = np.where(ring,
                                   cv2.filter2D(src, -1, kf) / np.maximum(cnt, 1.0),
                                   out[..., c])
        cur = nxt
    return out, cur > 0


def _build_sclera_disk(rgb: np.ndarray, socket: np.ndarray,
                       fill_mask: np.ndarray, white: np.ndarray | None = None) -> np.ndarray:
    """眼白盘：眶内未遮挡处保留原图（睫毛/眼睑阴影/眼白渐变都不丢），
    只把虹膜贴片的整块足迹填平。

    贴片平移后原位露出的就是这层干净眼白；因为填平范围 ≥ 贴片足迹，
    任何被贴片盖过的像素都不会在盘上留下残影。填平按「逐行取这一行眼白
    的中位色」：眼白上下常带眼睑阴影、越往下越暗，全图一个中位色平铺后
    贴片一挪开，露出的那块比周围亮一截，边缘就成了生硬的补丁。整行都没
    眼白（被虹膜盖满）的行用上下已知行插值，避免纵向断带。
    没有可信眼白（整颗彩色玩具眼）时才退回最近邻填充。
    """
    hole = fill_mask.astype(bool)
    if white is not None and int(white.sum()) >= 12:
        filled = rgb.astype(np.float32).copy()
        rows = np.flatnonzero(hole.any(axis=1))
        colmap = np.zeros((rgb.shape[0], 3), np.float32)
        if rows.size:
            # 整行没有任何可用眼白的行，取上下已知行的中位色插值兜底
            known = np.zeros(rows.size, bool)
            cols = np.zeros((rows.size, 3), np.float32)
            for k, y in enumerate(rows):
                src = white[y] & ~hole[y]
                if int(src.sum()) >= 3:
                    cols[k] = np.median(rgb[y][src], axis=0)
                    known[k] = True
            if known.any():
                gi = np.flatnonzero(known)
                if gi.size == 1:
                    cols[:] = cols[gi[0]]
                else:
                    at = np.arange(rows.size)
                    for c in range(3):
                        cols[:, c] = np.interp(at, gi, cols[gi, c])
            else:
                cols[:] = np.median(rgb[white], axis=0)
            colmap[rows] = cols
            # 有眼白的行按「洞左右最近眼白像素」横向线性过渡：只取整行中位色
            # 平铺时，贴片挪开后露出的填平区与洞边眼白亮度对不上，会看到一条
            # 生硬的白色弧带；插值能保证填平区在左右边界处与真实眼白连续。
            for y in rows:
                src = white[y] & ~hole[y]
                xs = np.flatnonzero(hole[y])
                if not xs.size:
                    continue
                cuts = np.flatnonzero(np.diff(xs) > 1) + 1
                for seg in np.split(xs, cuts):
                    x0, x1 = int(seg[0]), int(seg[-1])
                    left = np.flatnonzero(src[:x0])
                    right = np.flatnonzero(src[x1 + 1:]) + x1 + 1
                    if left.size and right.size:
                        c0 = rgb[y, left[-1]].astype(np.float32)
                        c1 = rgb[y, right[0]].astype(np.float32)
                        w = (np.arange(x1 - x0 + 1) + 1.0) / (x1 - x0 + 2.0)
                        filled[y, x0:x1 + 1] = (c0[None, :] * (1.0 - w)[:, None]
                                                + c1[None, :] * w[:, None])
                    elif left.size:
                        filled[y, x0:x1 + 1] = rgb[y, left[-1]]
                    elif right.size:
                        filled[y, x0:x1 + 1] = rgb[y, right[0]]
                    else:
                        filled[y, x0:x1 + 1] = colmap[y]
    else:
        filled = _nearest_fill(np.ascontiguousarray(rgb, np.uint8),
                               hole).astype(np.float32)
    disk = np.zeros((*socket.shape, 4), np.float32)
    disk[..., :3] = filled
    disk[..., 3] = _soft_mask(socket.astype(bool), 1.0) * 255.0
    return disk


def _build_eyelash(rgb: np.ndarray, socket: np.ndarray, sclera: np.ndarray,
                   iris: np.ndarray, spec: np.ndarray, px: int = 6) -> np.ndarray:
    """眼眶外扩一圈内的暗部（除眼白/虹膜/高光）= 睫毛 + 眼睑阴影。

    这部分原图像素要盖回最上层，否则实心眼白盘会把上眼睑的阴影抹平。
    这里必须把「彩色像素」也排除掉：虹膜贴片只覆盖生长出的虹膜 mask，
    贴着眼白的一圈过渡色可能落在 mask 外，但它们仍是虹膜颜色。若被当成
    睫毛写进静态层，虹膜一平移，原位就留下一条蓝色/红色残影（深浅分色
    的眼尤其像一道裂缝）。眼睑阴影是灰黑色的，比眼白彩度还低。
    """
    px = max(0, int(px))
    reg = cv2.dilate(socket.astype(np.uint8), _K3, iterations=px) > 0
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32)
    V = hsv[..., 2].astype(np.float32)
    thr = float(np.percentile(V[reg], 45)) if reg.any() else 0.0
    s_ref = float(np.median(S[sclera])) if sclera.any() else 0.0
    iris_ext = cv2.dilate(iris.astype(np.uint8), _K3, iterations=1) > 0
    # 不取最大连通域：下眼睑线的颜色可能偏浅，若被眼白盘盖掉会露出浅色带
    lash = (reg & (V < thr) & (S <= s_ref + 20.0)
            & ~sclera & ~iris_ext & ~spec)
    out = np.zeros((*socket.shape, 4), np.float32)
    out[..., :3] = rgb.astype(np.float32)
    out[..., 3] = lash.astype(np.float32) * 255.0
    return out


L2_EXTEND_PX = 1      # 虹膜贴片外扩层数：只需盖住 1px 抗锯齿边，再大就会糊出黑晕


def build_eye_layers(rgb: np.ndarray, socket: np.ndarray,
                     extend_px: int | None = None,
                     strict: bool = False) -> dict | None:
    """离线构建一只眼的分层贴片包（只在 build_gaze_geos 时跑一次）。

    层次自下而上：原图底 → 实心眼白盘 → 任意形状虹膜贴片 → 高光 → 睫毛/眼睑。
    只有虹膜贴片参与平移，眼白盘是全实心的，所以永远不会露底。
    strict 为真时（手画眼眶）虹膜边界再做颜色吸附与校验。
    """
    extend_px = L2_EXTEND_PX if extend_px is None else int(extend_px)
    seg = _segment_eye(rgb, socket, strict=strict)
    if seg is None:
        return None
    sclera, iris, spec = seg["sclera"], seg["iris"], seg["spec"]
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2HSV)
    S = hsv[..., 1].astype(np.float32)
    V = hsv[..., 2].astype(np.float32)

    # 虹膜贴片：外扩补全（保住星形/十字/心形瞳纹）+ 羽化 alpha。
    # 贴片必须先「实心化」：瞳孔上的浅色高光、浅灰瞳孔本身常被判成眼白，
    # 在虹膜 mask 里就是一个个洞；带着洞贴片一平移，洞挪开原位就露出填充色，
    # 看起来就是「眼睛里有一小块不跟着动」。填洞只补被虹膜整圈包住的区域，
    # 贴着眼眶的眼白月牙仍留在贴片外，不会被一起搬走。
    # 填洞前先闭运算缝上 1~2px：瞳孔/亮斑边缘那圈抗锯齿过渡色比虹膜边界亮、
    # 彩度低，常被判成眼白，瞳孔就经它和眶外连通，_fill_holes 认不出这是「洞」。
    # 贴片于是少了瞳孔那一块，平移时瞳孔下半截原地不动，原位留一道月牙。
    solid = _fill_holes(cv2.morphologyEx(iris.astype(np.uint8), cv2.MORPH_CLOSE,
                                        _K3, iterations=2) > 0)
    # 虹膜内部的亮色瞳孔/高光若与眶外连通（眶缘裁掉了虹膜下缘就会这样），
    # 实心化补不到它，贴片会带着一个缺口：平移时瞳孔不动、缺口露出填白色。
    # 这类亮斑单独并进「高光层」，按 spec_follow 与虹膜刚性同步平移，老位置
    # 由眼白盘填平，既不会留在原地，也不会把眼白月牙一起搬走。
    v_white = float(np.percentile(V[sclera], 75)) if sclera.any() else 200.0
    if iris.any():
        iy, ix = np.nonzero(iris)
        cx, cy = float(ix.mean()), float(iy.mean())
        r = math.sqrt(max(1.0, float(iris.sum())) / math.pi)
        yy, xx = np.mgrid[0:socket.shape[0], 0:socket.shape[1]]
        core = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.62 * r) ** 2
        hull = np.zeros(socket.shape, np.uint8)
        cv2.fillPoly(hull, [cv2.convexHull(np.column_stack((ix, iy)).astype(np.int32))], 255)
        inner = ((hull > 0) & core & socket & ~solid
                 & (V >= max(150.0, v_white - 22.0)) & (S <= 70))
        spec = spec | inner
    # 虹膜最外圈还有 1px 的描边/过渡色，按颜色判据常被漏掉。漏掉的话贴片
    # 平移后这圈原地不动，会和移走的虹膜之间留下一条「鬼影」轮廓；这里把
    # 实心虹膜再往外带 1px（保留原像素，不改成平均色）作为贴片的硬边。
    # 眼眶最外一圈是眼睑压住眼球的那条深色描边，既不能当成虹膜搬走，也不能
    # 被眼白盘的填充色刷白——那会在静止时沿眶沿出现一条亮弧。所以贴片足迹
    # 与填平区都从眶沿往里收 1px，眶沿原像素逐比特保留。
    rim = cv2.erode(socket.astype(np.uint8), _K3, iterations=1) > 0
    carry = (cv2.dilate(solid.astype(np.uint8), _K3) > 0) & rim
    filled, ext = _extend_ring(rgb, carry, extend_px)
    patch = np.zeros((*socket.shape, 4), np.float32)
    patch[..., :3] = filled
    # alpha 必须在虹膜本体（含 1px 描边）里就是 1：以前对「外扩后的 ext」整体
    # 做高斯羽化，虹膜内侧 2px 也跟着掉到 0.9，底下的眼白盘（填平色）透上来，
    # 静止时深色虹膜就被提亮了一圈。改成「carry 恒 1 + 只在外扩圈向外衰减」。
    alpha = cv2.GaussianBlur(carry.astype(np.float32), (0, 0), 1.0)
    alpha *= ext                 # 只保留外扩的一圈，环外不参与合成
    alpha[carry] = 1.0
    patch[..., 3] = np.clip(alpha, 0.0, 1.0) * 255.0

    # 高光单独一层：默认钉死，按 spec_follow 决定跟随比例
    hl = np.zeros((*socket.shape, 4), np.float32)
    hl[..., :3] = rgb.astype(np.float32)
    hl[..., 3] = spec.astype(np.float32) * 255.0

    # 填平范围只铺在「虹膜足迹 + 高光 + 眼白」上：无脑外扩 2px 会溢到眶沿
    # 外的眼睑/皮肤上，静止时那条浅色弧线就是这么来的（盘是实心不透明的，
    # 一铺就把肤色刷白）。外扩的 2px 只允许落在眼白上。
    fill = carry | spec
    fill = (cv2.dilate(fill.astype(np.uint8), _K3, iterations=2) > 0) & rim & (fill | sclera)
    disk = _build_sclera_disk(rgb, socket, fill, white=sclera)
    lash = _build_eyelash(rgb, socket, sclera, iris, spec)

    # 虹膜半径仍按「生长边缘前」的核心算：边缘生长只是把贴边的虹膜外圈一起
    # 带进贴片，半径若按生长后的算，0.6*r 会把本该小步挪的眼撑成大位移。
    core = seg.get("iris_core", iris)
    area = float(core.sum())
    r_iris = float(np.sqrt(area / math.pi)) if area > 0 else 0.0
    # 眼白余量按「贴片实际足迹」量，不能按虹膜核心：核心比外圈小一圈，量出来
    # 常有 7~10px，贴片照着这个值挪出去就被「眶外逐比特还原」硬裁掉，虹膜外缘
    # 出现一条被切平的直边——眼白弧带/贴纸感就是这么来的。足迹的余量才是真能挪。
    gl, gr, gu, gd = _edge_gaps(socket, solid, pct=50)
    room = float(max(gl, gr, gu, gd))
    # 四个余量并不对称（虹膜常贴着某一侧眶沿），单一 amp 只能按最宽那侧给，
    # 朝窄侧看时照样裁边。四个方向一起导出，渲染时按实际朝向取上限。
    room_dir = (float(gl), float(gr), float(gu), float(gd))
    # 横向、纵向分别按各自方向的最大余量取值：眼眶常常是扁的，向下只有
    # 1~2px 眼白却和横向 8px 共用同一个 amp，向下看时虹膜会整块移出眼白，
    # 原位露出填充色（大白眼上就是一块突兀的补丁）。
    limx = float(min(0.6 * r_iris, max(0.8 * max(gl, gr), 1.0), 6.0))
    limy = float(min(0.6 * r_iris, max(0.8 * max(gu, gd), 1.0), 6.0))
    amp = max(limx, limy)
    # 除了四向余量，还得有足够「非虹膜」像素当眼白托底：玩具眼/整颗彩色眼
    # 虹膜占眶 90% 以上，滴平后没有干净眼白可露，平移会在原位露出填充色。
    # 这种眼退回整块微移（L1），别硬做分层贴片。
    outside = float((socket & ~iris).sum()) / max(1.0, float(socket.sum()))
    return dict(base=rgb.astype(np.float32), disk=disk, iris=patch, spec=hl,
                lash=lash, socket=socket.astype(bool), r_iris=r_iris,
                room=room, amp=amp, limit_x=limx, limit_y=limy,
                room_dir=room_dir, ok=(room >= 1.5 and outside >= 0.10))


def _layer_over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """标准 source-over；两侧先钳到合法范围，避免累积出 NaN。"""
    a = np.clip(src[..., 3:4] / 255.0, 0.0, 1.0)
    return (np.clip(src[..., :3], 0.0, 255.0) * a
            + np.clip(dst, 0.0, 255.0) * (1.0 - a))


def _warp_transparent(layer: np.ndarray, dx: float, dy: float,
                      w: int, h: int) -> np.ndarray:
    """RGBA 平移；BORDER_TRANSPARENT 不写目标区外的像素，必须先清零 dst。

    不预填 dst 时 warpAffine 留出的那些像素是未初始化内存（float 下会出现垃圾/NaN）。
    """
    dst = np.zeros_like(layer)
    cv2.warpAffine(layer,
                   np.float32([[1.0, 0.0, float(dx)], [0.0, 1.0, float(dy)]]),
                   (w, h), dst=dst, flags=cv2.INTER_LINEAR,
                   borderMode=cv2.BORDER_TRANSPARENT)
    return dst


def _render_eye_layers(layers: dict, dx: float, dy: float, spec_follow: float,
                       base: np.ndarray) -> np.ndarray:
    """分层合成：3 次 warpAffine + 3 次 alpha 混合，连续无跳变。"""
    h, w = layers["socket"].shape
    out = base.astype(np.float32)
    out = _layer_over(out, layers["disk"])
    out = _layer_over(out, _warp_transparent(layers["iris"], dx, dy, w, h))
    fs = float(spec_follow)
    out = _layer_over(out, _warp_transparent(layers["spec"], dx * fs,
                                             dy * fs, w, h))
    out = _layer_over(out, layers["lash"])
    return out


def _eye_geometry(rgba: np.ndarray, eye: Eye, trusted: bool = True,
                  socket_mask: np.ndarray | None = None,
                  highlight_mask: np.ndarray | None = None,
                  level: str | None = None,
                  require_socket: bool = False) -> _EyeGeo | None:
    """在眼眶内提取像素级虹膜 mask，并生成位移场、纹理与高光掩膜。

    socket_mask 是标注工具给的眼眶 mask（像素级）：有它时眼眶就是它，眶外的
    皮肤/眉毛/头发权重恒为 0，不会再跟着眼球一起挪；没有时退回椭圆眼眶。
    require_socket=True 时没有可用 socket 直接返回 None：既不用椭圆兜底，
    也不抛异常（v2 里 mask=null 的眼只能不参与视线）。
    自动检测的眼（trusted=False）量不出眶内余量，不套用「没有眼白就不动」，
    保持原来的小范围平移；手工框 / SAM 眼眶的余量可信，严格按规则执行。

    level 是逐只眼的处理档位（L2/L1/L0）；None 或 auto 时按眼睛内部结构自动判。
    L2 走分层贴片（实心眼白盘 + 任意形状虹膜平移），L1/L0 整块微移（最外圈按 feather 羽化）。

    找不到可用区域时返回 None：这只眼不参与视线（眨眼仍然保留）。
    """
    h, w = rgba.shape[:2]
    hlm = None
    has_socket = (socket_mask is not None
                  and socket_mask.shape[:2] == (h, w)
                  and bool(socket_mask.any()))
    if require_socket and not has_socket:
        return None
    if (socket_mask is not None and socket_mask.shape[:2] == (h, w)
            and bool(socket_mask.any())):
        ys, xs = np.where(socket_mask)
        pad = 2
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(w, int(xs.max()) + pad + 1)
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(h, int(ys.max()) + pad + 1)
        ww, hh = x1 - x0, y1 - y0
        if ww < 8 or hh < 6:
            return None
        socket = socket_mask[y0:y1, x0:x1].astype(bool)
        # 眼眶 bbox 的中心与半宽：挑虹膜时拿它当「居中」的参照
        lx, ly = 0.5 * float(ww), 0.5 * float(hh)
        rx, ry = lx, ly
        if highlight_mask is not None and highlight_mask.shape[:2] == (h, w):
            hlm = highlight_mask[y0:y1, x0:x1].astype(bool) & socket
    else:
        # 自动框是「整只眼 + 外圈眼睑」，往里收一点当眼眶，眼眶外那一圈眼睑
        # 保持不动，眼球在里面滑；手工框由用户按眼睑画，原样使用
        rx, ry = (eye.rx, eye.ry) if trusted else (eye.rx * 0.84, eye.ry * 0.84)
        pad = int(max(rx, ry) * 0.35) + 2
        x0 = max(0, int(eye.cx - rx - pad))
        x1 = min(w, int(eye.cx + rx + pad) + 1)
        y0 = max(0, int(eye.cy - ry - pad))
        y1 = min(h, int(eye.cy + ry + pad) + 1)
        ww, hh = x1 - x0, y1 - y0
        if ww < 8 or hh < 6:
            return None
        lx, ly = eye.cx - x0, eye.cy - y0
        socket = _ellipse(hh, ww, lx, ly, rx, ry)

    rgb = rgba[y0:y1, x0:x1, :3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    sat, val = hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    # 手画过眼眶的走 strict：虹膜边界按颜色吸附 + 四条断言，错了就整块微移
    strict = socket_mask is not None
    iris = _iris_mask(rgb, socket, lx, ly, rx, ry, strict=strict)
    if iris is None and socket_mask is None:
        return None        # 椭圆路径找不到虹膜：维持原来的跳过策略
    area = int(iris.sum()) if iris is not None else 0
    if area < 12:
        iris = None
        area = 0

    open_soft = cv2.GaussianBlur(socket.astype(np.float32), (0, 0), 0.8)
    weight, _ramp, dmap = _build_weight_field(socket, iris)

    # 档位：显式 L2/L1/L0 优先，否则按眼睛内部结构自动判一次
    lv = level if level in EYE_PARAM else None
    if lv is None:
        lv = eye_structure_level(rgb, socket)
    p = EYE_PARAM[lv]

    # L2：任意形状虹膜贴片 + 分层合成。眼白盘是全实心的，虹膜怎么平移都不露底，
    # 也不再假设虹膜径向对称（星形/十字/心形瞳纹原样保留）。
    if lv == "L2":
        layers = build_eye_layers(rgb, socket, strict=strict)
        if layers is not None and layers["ok"]:
            amp = max(0.0, float(layers["amp"]))
            return _EyeGeo(box=(x0, y0, ww, hh), weight=weight,
                           open_soft=open_soft, socket=socket,
                           level=lv, mode=p["mode"], amp=amp,
                           limit_x=float(layers["limit_x"]),
                           limit_y=float(layers["limit_y"]),
                           enabled=True,
                           feather=float(p["feather"]),
                           spec_follow=float(p["spec_follow"]),
                           converge=float(p["converge"]),
                           room=layers["room_dir"],
                           layers=layers)
        # 眼眶几乎就是虹膜本身（眼白只剩 1px）：分层贴片没有眼白托底，
        # 硬平移会在原位露出填平的肤色，于是整只眼被停用、完全不动。
        # 退回整块微移（L1 档）：眶内整体小幅滑动，幅度小但至少能跟。
        lv = "L1"
        p = EYE_PARAM[lv]

    whole_weight = None
    if p["mode"] == "whole":
        # 整块微移：眶内大部分权重 1，只让最外圈按 feather 平滑归零
        whole_weight = np.clip(dmap / max(0.5, float(p["feather"])),
                               0.0, 1.0).astype(np.float32)
        whole_weight = cv2.GaussianBlur(whole_weight, (0, 0), 0.8)
        whole_weight[socket == 0] = 0.0

    # 用法 A：没有虹膜时，权重≈1 的距离场平台就是「眼球」；
    # 用法 B（找到虹膜）用虹膜本体当刚体核心
    core = iris if iris is not None else (weight >= 0.95) & socket
    if not core.any():
        core = (weight >= 0.6) & socket
    if not core.any():
        return None
    cys, cxs = np.where(core)
    irx = 0.5 * float(cxs.max() - cxs.min() + 1)
    iry = 0.5 * float(cys.max() - cys.min() + 1)
    icx, icy = float(cxs.mean()), float(cys.mean())
    r_eq = math.sqrt(float(core.sum()) / math.pi)
    gl, gr, gu, gd = _edge_gaps(socket, core)
    # 虹膜最贴眶边处的余量：Q 版大眼 / 星形瞳这里≈0，自动判为「没有眼白」
    room = float(dmap[core].min())

    # 高光 / 瞳孔：低饱和 + 够亮的小块。占了大半只虹膜的亮区是虹膜本身的配色
    # （淡色虹膜、金色角饰、背景泡泡），不算高光；居中且够大的亮块是「瞳孔」
    cand = core & (val > 0.45) & (sat < 0.45)
    hl = np.zeros_like(socket)
    hl_area = 0
    if hlm is not None and hlm.any():
        # 标注时算好的高光（<stem>_eyes.png 的 B 通道）：直接钉住，不再重复检测
        hl = hlm & core
        hl_area = int(hl.sum())
    else:
        n2, lab2, st2, _ = cv2.connectedComponentsWithStats(
            cand.astype(np.uint8), 8)
        if trusted and n2 > 1:
            j = 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))
            hl_area = int(st2[j, cv2.CC_STAT_AREA])
            if 3 <= hl_area <= 0.55 * area:
                hl = lab2 == j
    # 自动框圈的是整只眼，虹膜 mask 只能算个大概，「哪块是瞳孔、哪块是虹膜
    # 配色」分不开，硬套高光/瞳孔会把眼睑外圈当成瞳孔一起搬走，所以跳过
    split_hl = trusted and bool(hl.any()) and iris is not None
    pupil = pupil_base = None
    pupil_rx = pupil_ry = 0.0
    if split_hl:
        hys, hxs = np.where(hl)
        hoff = math.hypot((float(hxs.mean()) - icx) / max(1.0, irx),
                          (float(hys.mean()) - icy) / max(1.0, iry))
        frac = hl_area / float(max(1, area))
        # 瞳孔拆分只用于 L2 的「虹膜在眼白里滚动」；
        # L1/L0 走整块微移，高光要留在原位（spec_follow=0），不能把 hl 清掉
        if (p["mode"] != "whole" and frac >= 0.05
                and (hoff < 0.35 or frac >= 0.08)):
            # 虹膜几乎填满眼眶时，整块平移会在虹膜边缘拉出一条发白的缝：
            # 只让瞳孔在虹膜内部滑动，并把瞳孔外那圈暗色描边一起带上，
            # 否则瞳孔一移开就露出一圈暗环
            hm = cv2.dilate((hl & iris).astype(np.uint8),
                            np.ones((3, 3), np.uint8),
                            iterations=3) & iris.astype(np.uint8)
            pi = cv2.distanceTransform(hm, cv2.DIST_L2, 3)
            po = cv2.distanceTransform((hm == 0).astype(np.uint8),
                                       cv2.DIST_L2, 3)
            pupil = np.clip(0.5 + (pi - po) / 2.0, 0.0, 1.0).astype(np.float32)
            pys, pxs = np.where(hm.astype(bool))
            pupil_rx = 0.5 * float(pxs.max() - pxs.min() + 1)
            pupil_ry = 0.5 * float(pys.max() - pys.min() + 1)
            # 静态底色：把瞳孔整块抹掉再补成平滑虹膜，瞳孔滑走后原位是干净眼珠
            pupil_base = _nearest_fill(rgb, hm.astype(bool))
            hl = np.zeros_like(hl)
            split_hl = False
    if split_hl:
        hm = cv2.dilate(hl.astype(np.uint8), np.ones((2, 2), np.uint8))
        texture = cv2.inpaint(rgb, (hm.astype(bool) & iris).astype(np.uint8), 2,
                              cv2.INPAINT_TELEA)
    else:
        texture = rgb.copy()

    # 位移上限：眼眶 mask / 手工框交给「虹膜边缘到眼眶边缘」的实际余量，
    # 没有眼白的 Q 版眼自然收敛到几乎不动；自动框量不出余量，沿用框尺寸取值
    gaps_room = max(gl, gr, gu, gd)
    amp = float(p["amp"])
    if p["mode"] == "whole":
        # 整块微移：不需要眼白余量，眼眶里有一块眼睛就能动；
        # 幅度直接由档位的 amp 封顶（L1 2.5px / L0 1.2px）
        limx = limy = amp
        enabled = True
    elif pupil is not None:
        # 瞳孔在虹膜内部滑动：只要别滑出虹膜边缘，整只眼可以一直跟随
        limx = min(amp, max(1.5, min(0.6 * irx, 0.85 * (irx - pupil_rx) + 0.5)))
        limy = min(amp, max(1.5, min(0.6 * iry, 0.85 * (iry - pupil_ry) + 0.5)))
        enabled = True
    elif not trusted and socket_mask is None:
        limx = min(amp, max(1.5, 0.6 * rx))
        limy = min(amp, max(1.5, 0.6 * ry))
        enabled = True
    else:
        # 整块虹膜平移：只能挪「有眼白的那一侧」的余量。min(d[core]) 太小时
        # （虹膜顶满眼眶、星形瞳）根本没有形变空间，直接不参与视线——
        # 宁可不动，也不要把整圈眼白拉成一条变形带
        limx = min(amp, max(1.5, min(0.6 * r_eq, 0.7 * max(gl, gr) + 1.5)))
        limy = min(amp, max(1.5, min(0.6 * r_eq, 0.7 * max(gu, gd) + 1.5)))
        if socket_mask is not None:
            enabled = room >= 1.5
        else:
            # 旧的手工椭圆框沿线画在虹膜外缘上，min(d[iris])≈1 会误伤所有
            # 老素材，这里仍按四向最大余量判断，保持向后兼容
            enabled = gaps_room >= 1.5

    return _EyeGeo(box=(x0, y0, ww, hh), weight=weight,
                   whole_weight=whole_weight,
                   open_soft=open_soft, socket=socket,
                   texture=texture, hl=hl, split_hl=split_hl, pupil=pupil,
                   pupil_base=pupil_base, pupil_rx=pupil_rx,
                   pupil_ry=pupil_ry, limit_x=limx, limit_y=limy,
                   enabled=enabled, level=lv, mode=p["mode"], amp=amp,
                   feather=float(p["feather"]),
                   spec_follow=float(p["spec_follow"]),
                   converge=float(p["converge"]))


def _compose_gaze(out: np.ndarray, rgba: np.ndarray, geo: _EyeGeo,
                  sx: float, sy: float):
    """用位移场把一只眼整体形变：虹膜刚体平移，眼眶轮廓不动。

    不挖洞、不填补：位移权重在虹膜内部=1（虹膜+瞳孔整体平移），
    到眼眶边界衰减到 0（眼睑/眼线轮廓不动），中间的「眼白」靠拉伸吸收位移。
    这样眼球挪开的位置不会露出任何被挖掉的底色——之前挖洞再填最近邻，
    会把这颗眼外围那圈紫灰色描边抹进来，看起来就是「移开眼睛产生黑影」。
    """
    x0, y0, ww, hh = geo.box
    rgb = rgba[y0:y0 + hh, x0:x0 + ww, :3].astype(np.float32)
    dst = rgb.copy()

    if geo.layers is not None:
        # L2 分层贴片：实心眼白盘托底，任意形状虹膜整体平移，睫毛/眼睑盖顶。
        # 不重建眼球，不做位移场形变，原图瞳纹（星形/十字/心形）原样搬走。
        dst = _render_eye_layers(geo.layers, sx, sy, geo.spec_follow, rgb)
    else:
        if sx or sy:
            # 权重来自距离变换（_build_weight_field）：虹膜本体=1，出虹膜后按
            # 「到眼眶边界的距离」衰减，不会再像以前那样叠高斯把过渡带拉宽到 6px
            yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
            if geo.mode != "whole" and geo.pupil is not None:
                # 独立瞳孔（大白眼珠）：虹膜本体是静态底色，把原始瞳孔块（含描边）
                # 整体平移到新位置再叠回去。只用位移场挪亮块会把描边留在原地，
                # 瞳孔一走就露出一圈暗环
                w = cv2.warpAffine(geo.pupil * geo.open_soft,
                                   np.float32([[1.0, 0.0, float(sx)],
                                               [0.0, 1.0, float(sy)]]),
                                   (ww, hh), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REFLECT101)
                mp = cv2.warpAffine(geo.texture.astype(np.float32),
                                    np.float32([[1.0, 0.0, float(sx)],
                                                [0.0, 1.0, float(sy)]]),
                                    (ww, hh), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REFLECT101)
                a = w[..., None]
                dst = geo.pupil_base.astype(np.float32) * (1.0 - a) + mp * a
            else:
                # iris 模式：虹膜本体=1、到眼眶边界衰减 0；
                # whole 模式：眶内大部分=1、最外圈按 feather 羽化
                w = (geo.whole_weight
                     if geo.mode == "whole" and geo.whole_weight is not None
                     else geo.weight)
                # remap 的 map 是「目标像素去源图哪里取」：写 x - sx 才是把内容往 +sx 挪
                map_x = xx - sx * w
                map_y = yy - sy * w
                dst = cv2.remap(geo.texture, map_x, map_y, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT101)
                dst = dst.astype(np.float32)

        # 高光跟的是光源不是视线：spec_follow=0 时钉在原位；
        # L2 的 0.25 表示只跟随 1/4 的位移，避免高光跟着虹膜整块滑走
        if geo.split_hl and geo.hl.any():
            fs = float(geo.spec_follow)
            if fs > 0 and (sx or sy):
                # 虹膜里的高光已经跟着整块虹膜挪了 sx；先把它从旧位置擦掉，
                # 再按 fs 倍位移贴回原图高光，净位移就只剩 fs·s
                ref = cv2.warpAffine(
                    rgb, np.float32([[1.0, 0.0, float(sx) * fs],
                                     [0.0, 1.0, float(sy) * fs]]),
                    (ww, hh), flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
                old = cv2.warpAffine(
                    geo.hl.astype(np.float32),
                    np.float32([[1.0, 0.0, float(sx)],
                                [0.0, 1.0, float(sy)]]),
                    (ww, hh), flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
                new = cv2.warpAffine(
                    geo.hl.astype(np.float32),
                    np.float32([[1.0, 0.0, float(sx) * fs],
                                [0.0, 1.0, float(sy) * fs]]),
                    (ww, hh), flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
                old = cv2.GaussianBlur(old, (0, 0), 0.6)[..., None]
                new = cv2.GaussianBlur(new, (0, 0), 0.6)[..., None]
                dst = dst * (1.0 - old) + ref * old
                dst = dst * (1.0 - new) + ref * new
            else:
                hm = cv2.GaussianBlur(geo.hl.astype(np.float32), (0, 0), 0.6)[..., None]
                dst = dst * (1.0 - hm) + rgb * hm

    # 眼眶 mask 之外的像素逐比特保留原样：瞳孔模式的 open_soft 羽化会往外晕
    # 1~2px，不裁掉的话眉骨/眼睑边缘会被轻微改写
    if geo.socket is not None:
        keep = ~geo.socket
        dst[keep] = rgb[keep]

    out[y0:y0 + hh, x0:x0 + ww, :3] = np.clip(dst, 0, 255).astype(np.uint8)


def build_gaze_geos(rgba: np.ndarray, eyes: list[Eye],
                    trusted: bool = True, sockets: list | None = None,
                    highlight: np.ndarray | None = None,
                    levels: list | None = None,
                    require_socket: bool = False) -> list:
    """一次性算好每只眼的图层拆解，供 gaze_frame 反复使用。

    拆解里有距离变换、连通域、inpaint，单只眼就要几毫秒；以前每换一档
    位移都重算一遍，鼠标一动就掉帧。几何只跟图像有关，缓存起来即可。
    虹膜 mask 找不到时留 None（这只眼不参与视线，眨眼照旧）。
    trusted=False 是自动检测的眼睛：框没画到眼睑，不套用「没有眼白就不动」。
    sockets / highlight 是标注工具给的眼眶与高光 mask（与 eyes 顺序对应）。
    levels 是逐只眼的处理档位（L2/L1/L0）；缺省或写 auto 时按内部结构自动判。
    require_socket=True（v2 有 parts 时）：缺 socket 的眼不建几何，也不回退椭圆
    ——回退等于给全量 eyes 又造一份几何，与「active 单一真源」冲突。
    """
    sockets = sockets or []
    levels = levels or []

    def _geo(i: int, level=None):
        return _eye_geometry(
            rgba, eyes[i], trusted=trusted,
            socket_mask=(sockets[i] if i < len(sockets) else None),
            highlight_mask=highlight,
            level=(level or (levels[i] if i < len(levels) else None)),
            require_socket=require_socket)

    def _manual(i: int) -> bool:
        return i < len(levels) and levels[i] in EYE_PARAM

    geos = [_geo(i) for i in range(len(eyes))]
    # 同一对眼睛的档位必须一致：自动判定逐只看“彩度/眼白占比”，左右眼在
    # 原图里常有一侧更暗、眼白被高光吃掉，于是同一只玩偶的双眼各判一档
    # （一只 L2 挪 6px、一只 L1 只挪 2.5px，还是整块眼白一起滑），看起来
    # 就是「左边那只眼不跟」。自动档下把 L1 的对侧也按 L2 重建；分层不成立
    # （没有眼白托底）时 _eye_geometry 自己会退回 L1，不会硬套。
    for a in range(0, len(eyes) - 1, 2):
        ga, gb = geos[a], geos[a + 1]
        if ga is None or gb is None or _manual(a) or _manual(a + 1):
            continue
        if {ga.level, gb.level} != {"L1", "L2"}:
            continue
        sa = float(ga.socket.sum()) if ga.socket is not None else 0.0
        sb = float(gb.socket.sum()) if gb.socket is not None else 0.0
        # 尺寸悬殊的两只（装饰性小眼/检测到的误框）不配对，各自保留原档
        if sa <= 0 or sb <= 0 or min(sa, sb) < 0.55 * max(sa, sb):
            continue
        for idx in (a, a + 1):
            if geos[idx].level == "L1":
                geos[idx] = _geo(idx, "L2")
    # 只有一只眼时，L0/L1 的默认位移（1.2 / 2.5px）在画面上几乎看不出来；
    # 单眼素材抬到 2.4px 起步，保证肉眼能看到视线跟随（对侧眼不存在，不会失衡）。
    if len(eyes) == 1 and geos and geos[0] is not None:
        g = geos[0]
        if g.level in ("L0", "L1") and g.amp < 2.4:
            g.amp = 2.4
            g.limit_x = max(g.limit_x, 2.4)
            g.limit_y = max(g.limit_y, 2.4)
    return geos


def gaze_frame(rgba: np.ndarray, eyes: list[Eye], dx: int, dy: int,
               geos: list | None = None) -> np.ndarray:
    """生成「看向 (dx, dy)」的帧，位移单位是像素。

    每只眼按自己的档位走：L2 用分层贴片（实心眼白盘托底，任意形状虹膜整体平移），
    L1/L0 整块微移（最外圈羽化、幅度压到 2.5 / 1.2px）。
    镜面高光按 spec_follow 决定跟随比例；两眼内聚比例也来自档位。
    虹膜贴片盖不住眼白（眶内没有余量）时这只眼直接跳过，保持原样。

    geos：可选，先由 build_gaze_geos() 算好的图层拆解（与 eyes 一一对应）。
    """
    if not eyes or (dx == 0 and dy == 0):
        return rgba
    if geos is None:
        geos = build_gaze_geos(rgba, eyes)
    out = rgba.copy()
    center_x = sum(e.cx for e in eyes) / len(eyes)
    two_eyes = len(eyes) >= 2

    for eye, geo in zip(eyes, geos):
        # 虹膜 mask 找不到，或眼眶里根本没有眼白（Q 版大眼虹膜顶满眶）：
        # 这只眼不参与视线，宁可不动也不要把整块色块拖出轮廓，眨眼照旧
        if geo is None or not geo.enabled:
            continue
        if eye.ry < 0.3 * eye.rx:       # 眯着/快闭上的那只眼不参与视线
            continue
        # 保留小数位移：L1/L0 的 2.5px / 1.2px 上限如果用整数会把 L0 直接砍成 1px
        sx, sy = float(dx), float(dy)
        # 近距离目标：两眼各自朝鼻侧收一点，避免平行平移像在看远处。
        # 内聚比例来自档位（L2 0.15，L1/L0 不做内聚）
        if two_eyes and sx:
            conv = min(2, int(round(geo.converge * abs(sx))))
            if conv:
                sx += conv if eye.cx < center_x else -conv
        # 位移上限在建几何时就算好了：L2 按眼白余量、L1/L0 直接按档位 amp，
        # Q 版眼自动收敛到几乎不动。超限时按同一比例缩小两个分量（而不是
        # 各自夹断），否则斜向大位移会被扭成 45°，看起来像「鼠标一远就不跟了」
        limx = min(geo.limit_x, geo.amp)
        limy = min(geo.limit_y, geo.amp)
        # L2 贴片只要挪出眼眶就会被「眶外逐比特还原」硬裁，虹膜外缘出现一条
        # 切平的直边。左右/上下的余量并不对称（虹膜常贴着某一侧眶沿），所以
        # 按当前朝向那一侧的余量取上限：宽的一侧照常挪，窄的一侧少挪或不挪。
        if geo.room is not None and geo.layers is not None:
            gl, gr, gu, gd = geo.room
            if sx:
                limx = min(limx, gr if sx > 0 else gl)
            if sy:
                limy = min(limy, gd if sy > 0 else gu)
        k = 1.0
        if abs(sx) > 0 and limx <= 0:
            sx = 0.0
        elif abs(sx) > limx:
            k = min(k, limx / abs(sx))
        if abs(sy) > 0 and limy <= 0:
            sy = 0.0
        elif abs(sy) > limy:
            k = min(k, limy / abs(sy))
        if k < 1.0:
            sx *= k
            sy *= k
        if sx == 0 and sy == 0:
            continue

        _compose_gaze(out, rgba, geo, sx, sy)

        # 向下看时虹膜上缘本来就会被眼眶裁掉（等于上眼睑压住），
        # 不再额外铺一层平涂肤色——那会在深色大虹膜上留下突兀的灰块
    return out


def build_expr_bank(rgba: np.ndarray, geos: list):
    """把各只眼的分层合成一张整图 L，生成 6 个表情差分贴片。

    只做「提取 + 合并」，不碰视线/眨眼：L2 直接用 build_eye_layers 的
    disk/iris/spec/lash（与交接包要求的键名一致）；L0/L1 没有分层，就把
    整块眼眶像素当眼球层兜底，闭眼弧线照样能画。
    """
    from expr_state import ExprBank, _over as _rgba_over

    sockets = [g for g in geos if g is not None and g.socket is not None]
    if not sockets:
        return None
    h, w = rgba.shape[:2]
    sock = np.zeros((h, w), bool)
    base = np.asarray(rgba[..., :3], np.float32)
    disk = np.zeros((h, w, 4), np.float32)
    iris = np.zeros((h, w, 4), np.float32)
    spec = np.zeros((h, w, 4), np.float32)
    lash = np.zeros((h, w, 4), np.float32)
    # 每只眼的几何都是裁到眼眶外接框里算的，这里按 geo.box 贴回整图
    for g in sockets:
        x0, y0, ww, hh = [int(v) for v in g.box]
        x1, y1 = min(w, x0 + ww), min(h, y0 + hh)
        x0, y0 = max(0, x0), max(0, y0)
        if x1 <= x0 or y1 <= y0:
            continue
        s = np.asarray(g.socket).astype(bool)
        sock[y0:y1, x0:x1] |= s[:y1 - y0, :x1 - x0]
        if g.layers:
            for key, dst in (('disk', disk), ('iris', iris),
                             ('spec', spec), ('lash', lash)):
                dst[y0:y1, x0:x1] = _rgba_over(dst[y0:y1, x0:x1],
                                               g.layers[key][:y1 - y0, :x1 - x0])
        else:
            ball = np.zeros((y1 - y0, x1 - x0, 4), np.float32)
            ball[..., :3] = base[y0:y1, x0:x1]
            ball[..., 3] = s[:y1 - y0, :x1 - x0].astype(np.float32) * 255.0
            disk[y0:y1, x0:x1] = _rgba_over(disk[y0:y1, x0:x1], ball)
    if sock.sum() < 50:
        return None
    L = dict(base=base, disk=disk, iris=iris, spec=spec, lash=lash,
             socket=sock)
    report: list[str] = []
    bank = ExprBank.build(L, sock, report=report)
    for msg in bank.report:
        # 多出来的连通域（配饰上的装饰眼）不会参与贴片，但要让素材维护者看见
        print(f"[expr] 眼眶结构提醒：{msg}")
    return bank


def resize_rgba(rgba: np.ndarray, height: int) -> np.ndarray:
    """按高度等比缩放 RGBA（与窗口侧的显示尺寸一致）。"""
    h, w = rgba.shape[:2]
    if height <= 0 or h == height:
        return rgba
    nw = max(1, int(round(w * height / float(h))))
    interp = cv2.INTER_AREA if height < h else cv2.INTER_LANCZOS4
    return cv2.resize(rgba, (nw, height), interpolation=interp)


def is_free_hair(alpha: np.ndarray, mask: np.ndarray, iters: int = 2,
                 pivot: tuple[float, float] | None = None) -> bool:
    """发缕外缘是否临着透明区——决定这缕头发能不能放开摆动。

    外缘临透明：甩出去露出来的是透明，不会出现「头发后面没画」的空洞。
    压在脸/身上的发丝：摆动后原位置必然露洞，只给很小的幅度。

    传了 pivot（发根）时只看发梢那一半的外圈：发根本来就贴着头部，
    外圈靠近发根的一侧必然是不透明的，算进去会把所有发缕都判成不能摆。
    """
    ring = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8),
                      iterations=iters) > 0
    ring &= ~mask
    if not ring.any():
        return False
    if pivot is not None:
        ys, xs = np.nonzero(mask)
        if len(xs):
            far = float(np.hypot(xs - pivot[0], ys - pivot[1]).max())
            ry, rx = np.nonzero(ring)
            keep = np.hypot(rx - pivot[0], ry - pivot[1]) >= far * 0.5
            if keep.any():
                sel = np.zeros_like(ring)
                sel[ry[keep], rx[keep]] = True
                ring = sel
    return float(alpha[ring].mean()) * (1.0 / 255.0) < 0.08


@dataclass
class _Wisp:
    pivot: tuple[float, float]       # 发根（旋转中心）
    x_dir: float                     # 发梢在发根左侧(-1)/右侧(+1)
    tip_dir: float                   # 发梢朝下(+1)/朝上(-1)
    free: bool                       # 外缘是否临透明
    angle_max: float                 # 允许的最大摆角（度）
    out_dir: float                   # 非外缘发丝的允许摆动方向（+1 右 / -1 左）
    normal: tuple[float, float]      # 发丝的法线方向 = 摆动方向
    length: float                    # 发根到发梢长度（显示像素）
    amp_max: float                   # 发梢位移硬上限（显示像素）
    weight: np.ndarray               # 发根 0 -> 发梢 1 的渐变权重（0..1）
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # 含最大移动余量的包围盒
    angle: float = 0.0
    omega: float = 0.0
    phase: float = 0.0
    stiff: float = 26.0
    damp: float = 6.0


class HairField:
    """多边形发缕：沿发丝方向的渐变剪切位移场（弯曲），不是刚体旋转。

    刚体旋转会把整缕从原位挪走，短发只有 20~30px 时缝隙是硬楔形，而且
    L·sinθ 太小、几乎看不出动。这里改成 remap 的渐变剪切：发根权重 0、
    发梢权重 1（t**1.4），位移方向垂直于发丝，像素被连续拉伸而不是整块搬走。

    位移场在发缕边界归零、边界外恒为 0，整图只做一次重采样：边界像素原地
    不动（不会空出分界线），也没有「抠出→变换→叠回」的第二份像素（重影）。
    预乘 alpha 空间 + BORDER_REPLICATE，避免半透明边缘掺背景色或补成透明变薄。
    只有位移量非零的像素被写回，其余像素逐比特保留。

    摆角由二阶系统驱动（拖拽速度 / 身体摇摆 / 落地冲击 / 低频微风）；
    外缘临透明的发缕摆幅 14% 发缕长度（上限 8px），压在脸或身上的 10%（上限 5px，
    往里的一半再收 60%）。
    """

    def __init__(self, wisps: list[HairWisp], asset_rgba: np.ndarray,
                 width: int, height: int, wind_speed: float = 1.0,
                 amp: float = 1.0, stiffness: float = 1.0,
                 damping: float = 1.0):
        self.items: list[_Wisp] = []
        self.phase = 0.0
        # 可调参数（编辑器的「动作参数」）：速度/摆幅按倍率，刚度阻尼同理
        # 速度、摆幅允许 0（= 完全停止）；刚度/阻尼留一点下限，避免弹簧数值发散
        self.wind_speed = max(0.0, float(wind_speed))
        self.amp = max(0.0, float(amp))
        self.stiffness = max(0.05, float(stiffness))
        self.damping = max(0.05, float(damping))
        if not wisps or width < 16 or height < 16:
            return
        ah, aw = asset_rgba.shape[:2]
        sx, sy = width / float(max(1, aw)), height / float(max(1, ah))
        alpha = asset_rgba[..., 3]
        for i, w in enumerate(wisps):
            m = cv2.resize(w.mask.astype(np.uint8) * 255, (width, height),
                           interpolation=cv2.INTER_NEAREST) > 127
            if not m.any():
                continue
            pivot = (w.pivot[0] * sx, w.pivot[1] * sy)
            tip = (w.tip[0] * sx, w.tip[1] * sy)
            dx, dy = tip[0] - pivot[0], tip[1] - pivot[1]
            length = max(6.0, float(math.hypot(dx, dy)))
            ux, uy = dx / length, dy / length
            normal = (-uy, ux)
            free = is_free_hair(alpha, w.mask, pivot=w.pivot)
            # 发梢位移取发缕长度的 14%（外缘）/ 10%（压在身上），绝对上限见括号
            amp_max = (min(8.0, 0.14 * length) if free
                       else min(5.0, 0.10 * length))
            angle_max = math.degrees(math.asin(min(1.0, amp_max / length)))
            # 位移场 = t**1.4 × 边界归零门控。门控保证 mask 边界外恒为 0、边界内
            # 连续衰减：边界像素原地不动，所以既不会空出原位，也不会有第二份像素
            # 被叠加出来（重影的根源就是"画了两遍"，这里只采样一次）
            ys, xs = np.nonzero(m)
            pad = amp_max + 4.0
            bx0 = max(0, int(xs.min() - pad))
            by0 = max(0, int(ys.min() - pad))
            bx1 = min(width, int(xs.max() + pad) + 1)
            by1 = min(height, int(ys.max() + pad) + 1)
            md = cv2.dilate(m[by0:by1, bx0:bx1].astype(np.uint8),
                            np.ones((3, 3), np.uint8), iterations=2)
            yy, xx = np.mgrid[by0:by1, bx0:bx1].astype(np.float32)
            t = np.clip(((xx - pivot[0]) * ux + (yy - pivot[1]) * uy) / length,
                        0.0, 1.0)
            dist = cv2.distanceTransform(md, cv2.DIST_L2, 5)
            # 门控只在膨胀出来的 2px 外圈衰减（那圈基本是透明像素，压住它看不出）。
            # 衰减带若按发丝轮廓算（如 6px），会把可见轮廓一起钉死，只剩内部一小块
            # 被剪切，看起来像一团污渍，而不是摆动
            gate = np.clip(dist / 2.0, 0.0, 1.0)
            gate = cv2.GaussianBlur(gate, (0, 0), 0.8)
            weight = np.zeros((height, width), np.float32)
            weight[by0:by1, bx0:bx1] = cv2.GaussianBlur(
                (t ** 1.4) * gate, (0, 0), 1.0)
            bbox = (bx0, by0, bx1, by1)
            cx = float(xs.mean())
            self.items.append(_Wisp(
                pivot=pivot, bbox=bbox, normal=normal,
                length=length, amp_max=amp_max, weight=weight,
                x_dir=1.0 if dx >= 0 else -1.0,
                tip_dir=1.0 if dy >= 0 else -1.0,
                free=free, angle_max=angle_max,
                out_dir=1.0 if cx >= width * 0.5 else -1.0,
                phase=(i * GOLDEN_ANGLE) % (2.0 * math.pi),
                # 每缕给不同的阻尼，否则几缕头发完全同步摆动，一眼假
                stiff=(28.0, 24.0, 32.0)[i % 3],
                damp=(6.2, 7.6, 5.4)[i % 3]))

    @property
    def ready(self) -> bool:
        return bool(self.items)

    def kick(self, strength: float):
        """落地/点击的冲击：发梢瞬间向上弹，再在 0.6s 内衰减完。"""
        for s in self.items:
            s.omega += -s.x_dir * strength

    def step(self, dt: float, vx: float, tilt: float):
        """推进角度：目标 = 微风 + 拖拽滞后 + 身体摇摆滞后，二阶弹簧逼近。"""
        if not self.items:
            return
        self.phase += dt * 2.6 * self.wind_speed   # 微风约 2.4 秒一个周期（≈0.41Hz）
        vnorm = max(-1.0, min(1.0, vx / 620.0))
        for s in self.items:
            lim = s.angle_max * self.amp
            # 微风按各自上限取比例：短发上限本来就小，用固定角速度会几乎看不出动。
            # 剪切位移不会空出原位，外缘发缕可以放心用接近满幅，待机也能看出摆动
            wind = lim * (0.95 if s.free else 0.9) \
                * math.sin(self.phase + s.phase)
            # 发梢朝下时，顺时针旋转 = 发梢向左；朝上时方向相反
            target = s.tip_dir * (wind + lim * 0.85 * vnorm) \
                - 0.35 * tilt
            target = max(-lim, min(lim, target))
            if not s.free:
                # 压在身上的发丝：允许往复摆动，但往里（可能盖住脸）的幅度先收一半
                sign = -s.out_dir * s.tip_dir
                if target * sign < 0:
                    target *= 0.6
            acc = ((target - s.angle) * s.stiff * self.stiffness
                   - s.omega * s.damp * self.damping)
            s.omega += acc * dt
            s.angle += s.omega * dt
            if s.angle > lim:
                s.angle, s.omega = lim, 0.0
            elif s.angle < -lim:
                s.angle, s.omega = -lim, 0.0

    def swing(self, rgba: np.ndarray) -> np.ndarray:
        """按当前摆角做渐变剪切：位移场只改变采样位置，整图只采样一次。

        重影的根源是「画了两遍」——把原位置的发丝抠出来、移到新位置、再叠回去，
        同一根发丝就会同时出现在两个位置（羽化越宽、重影带越宽）。这里不做任何
        alpha 混合：所有发缕的位移量先累加成一个位移场，再对预乘 alpha 的图做
        一次 remap。位移场在发缕边界归零，边界外恒为 0，所以边界像素原地不动，
        既不会空出分界线，也不会留下第二份像素。区外像素逐比特保留。
        """
        if not self.items:
            return rgba
        if all(abs(s.angle) < 0.02 for s in self.items):
            return rgba
        h, w = rgba.shape[:2]
        x0 = max(0, min(s.bbox[0] for s in self.items))
        y0 = max(0, min(s.bbox[1] for s in self.items))
        x1 = min(w, max(s.bbox[2] for s in self.items))
        y1 = min(h, max(s.bbox[3] for s in self.items))
        if x1 <= x0 or y1 <= y0:
            return rgba
        sub = rgba[y0:y1, x0:x1]
        sh, sw = sub.shape[:2]
        alpha = sub[..., 3:4].astype(np.float32) * (1.0 / 255.0)
        premul = np.dstack([sub[..., :3].astype(np.float32) * alpha, alpha])

        # 各发缕的位移量叠加成一个场，全程只做一次重采样
        shift_x = np.zeros((sh, sw), np.float32)
        shift_y = np.zeros((sh, sw), np.float32)
        for s in self.items:
            amp = s.length * math.sin(math.radians(s.angle))
            if abs(amp) < 0.01:
                continue
            field = s.weight[y0:y1, x0:x1]
            shift_x -= field * (s.normal[0] * amp)
            shift_y -= field * (s.normal[1] * amp)
        touched = np.hypot(shift_x, shift_y) > 0.02
        if not touched.any():
            return rgba

        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        moved = cv2.remap(premul, xx + shift_x, yy + shift_y,
                          cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        a_out = np.clip(moved[..., 3], 0.0, 1.0)
        safe = np.maximum(a_out, 1e-4)[..., None]
        rgb_out = np.clip(moved[..., :3] / safe, 0, 255).astype(np.uint8)

        result = rgba.copy()
        out = result[y0:y1, x0:x1]
        out[..., :3][touched] = rgb_out[touched]
        out[..., 3][touched] = np.clip(a_out * 255.0, 0, 255).astype(np.uint8)[touched]
        return result


def checkerboard(rgba: np.ndarray, cell: int = 12) -> np.ndarray:
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 235, 190).astype(np.float32)
    bg = np.dstack([bg] * 3)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return np.clip(rgba[..., :3] * a + bg * (1 - a), 0, 255).astype(np.uint8)


def _montage(frames: list[np.ndarray]) -> np.ndarray:
    cells = [checkerboard(f) for f in frames]
    h = max(c.shape[0] for c in cells)
    cells = [cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
             for c in cells]
    return np.hstack(cells)


def main():
    ap = argparse.ArgumentParser(description="检测双眼并预览眨眼帧")
    ap.add_argument("--asset", type=Path, required=True)
    ap.add_argument("--preview", type=Path, default=None)
    args = ap.parse_args()

    asset = PetAsset.load(args.asset)
    print(f"素材 {asset.width}×{asset.height}  眼睛 {len(asset.eyes)} 个")
    for e in asset.eyes:
        print(f"  center=({e.cx:.1f},{e.cy:.1f}) r=({e.rx:.1f},{e.ry:.1f}) score={e.score:.1f}")
    frames = asset.blink_frames()
    print(f"眨眼帧 {len(frames)} 张")
    if args.preview and frames:
        out = _montage([asset.rgba] + frames)
        cv2.imwrite(str(args.preview), cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
        print(f"预览 -> {args.preview}")


if __name__ == "__main__":
    main()
