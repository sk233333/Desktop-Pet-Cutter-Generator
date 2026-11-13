"""表情状态机 + 差分贴片生成。

设计原则（前面讨论过的结论在这里落地）：
  * 表情是【离散状态】→ 用差分贴片，不用形变；视线是【连续量】→ 用 dir_amp 插值
  * 只换眼睛那一小块贴片，不整图 ×N；normal 状态完全不叠（零重影、零累积）
  * 贴片内部是实心眼白盘 → 覆盖式替换，不会出现"画了两遍"
  * 状态机只有三条触发源：idle 随机池 / 事件 / 对话 emotion

嘴差分：可选外部目录 mouth/<state>.png，缺省不处理（只做眼）。

本文件是交接包 modules/expr_state.py 的原样搬入，仅加了一个 bgr 参数：
本仓库 anim.py 全程用 RGB，而交接包按 BGR 写。除取肤色中位色那一步的
颜色空间换算外，算法一行未改。
"""
import cv2, numpy as np, json, os, time, random

STATES = ('normal', 'blink', 'half', 'surprise', 'wink_l', 'wink_r')


def _over(dst, src):
    """source-over。dst 可为 RGB(3) 或 RGBA(4)，返回同通道数。"""
    a = np.clip(src[..., 3:4] / 255.0, 0.0, 1.0)
    rgb = (np.clip(src[..., :3], 0, 255) * a +
           np.clip(dst[..., :3], 0, 255) * (1.0 - a))
    if dst.ndim == 3 and dst.shape[2] == 4:
        out = np.empty_like(dst)
        out[..., :3] = rgb
        out[..., 3:4] = np.clip(src[..., 3:4] +
                                dst[..., 3:4] * (1.0 - a), 0, 255)
        return out
    return rgb


def _eye_rgba(L, socket):
    """从分层合成一张『完整眼部』RGBA（外部透明，socket 内实心）。"""
    # 中间合成保持 3 通道，alpha 最后统一由 socket 软边给出
    rgb = np.zeros((*socket.shape, 3), np.float32)
    for k in ('disk', 'iris', 'spec', 'lash'):
        rgb = _over(rgb, L[k])
    out = np.zeros((*socket.shape, 4), np.float32)
    out[..., :3] = rgb
    a = cv2.GaussianBlur(socket.astype(np.float32), (0, 0), 1.0)
    out[..., 3] = np.clip(a / max(a.max(), 1e-6), 0, 1) * 255.0
    return out


def _halves(socket, report=None):
    """按连通域分左右眼，只保留【面积最大的两个】。

    不能再用 len//2 切：手工标注里常混进配饰上的「装饰眼」（帽子花纹、猫眼扣子），
    3 个域时 cut=1，wink_r 会同时闭掉真右眼和装饰眼。
    按面积取前两名 + 按 x 排序，剩下的域不参与任何贴片，也就不会被 cover 抹掉。
    两者 y 中心差 > 0.5 眼高时判为离群，写进 report 而不是静默吞。
    """
    sm = (socket > 0).astype(np.uint8)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(sm, 8)
    H, W = socket.shape
    empty = np.zeros((H, W), bool)
    if n <= 1:
        return [empty, empty]

    ids = list(range(1, n))
    if len(ids) > 2:
        ids.sort(key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True)
        dropped = ids[2:]
        ids = ids[:2]
        if report is not None:
            report.append(f"眼眶有 {n - 1} 个连通域，忽略 {len(dropped)} 个（疑似配饰）")
    else:
        ids.sort(key=lambda i: cents[i][0])

    keep = sorted(ids, key=lambda i: cents[i][0])
    if len(keep) == 2:
        h = max(stats[keep[0], cv2.CC_STAT_HEIGHT], stats[keep[1], cv2.CC_STAT_HEIGHT])
        dy = abs(cents[keep[0]][1] - cents[keep[1]][1])
        dx = abs(cents[keep[0]][0] - cents[keep[1]][0])
        # 真眼是一左一右：允许轻微倾斜（dy 小于水平距离），但不应差出一只眼的高度。
        # 用「dy > 0.8*dx + 2」而不是固定的 0.5*眼高：歪头素材两眼可以有 7~8px 高差。
        if dy > max(0.8 * dx + 2.0, 0.5 * max(h, 1)):
            keep = [max(keep, key=lambda i: stats[i, cv2.CC_STAT_AREA])]
            if report is not None:
                report.append("两只眼眶 y 中心差过大，已忽略疑似配饰的连通域")

    left = np.isin(lab, keep[:1])
    right = np.isin(lab, keep[1:]) if len(keep) > 1 else empty
    return [left, right]


def _warp(rgba, region, sx=1.0, sy=1.0, dx=0.0, dy=0.0):
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return np.zeros_like(rgba)
    cx = (xs.min() + xs.max()) / 2.0
    cy = (ys.min() + ys.max()) / 2.0
    M = np.float32([[sx, 0, cx * (1 - sx) + dx],
                    [0, sy, cy * (1 - sy) + dy]])
    out = cv2.warpAffine(rgba, M, (region.shape[1], region.shape[0]),
                         flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_TRANSPARENT)
    # 变换后仍限制在该半区内，避免越界盖到另一只眼
    out[..., 3] *= region.astype(np.float32)
    return out


def _closed_line(L, socket, region):
    """闭眼：直接画一条上眼睑弧线。

    不要用 lash 层压缩 —— 它包含整块深色上眼睑，压到 8% 会糊成一片脏渐变，
    且 INTER_LINEAR 缩放锐边会产生横向条纹。
    """
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return np.zeros((*socket.shape, 4), np.float32)

    m = L['lash'][..., 3] > 128
    col = (np.median(L['lash'][..., :3][m], axis=0)
           if m.sum() > 20 else np.array([40., 35., 35.], np.float32))

    x0, x1 = int(xs.min()), int(xs.max())
    cy = (int(ys.min()) + int(ys.max())) / 2.0
    cx, rx = (x0 + x1) / 2.0, (x1 - x0) / 2.0 * 0.88

    # 线宽按原 lash 的实际厚度取：这只角色黑色上眼睑弧很粗，
    # 手画 2px 细线在大虹膜上会立刻穿帮。
    lash_m = (L['lash'][..., 3] > 128) & region & (socket > 0)
    lw = 2
    if lash_m.sum() > 30:
        mid = int(round(cx))
        band = lash_m[:, max(0, mid - 2):mid + 3]
        thick = int(band.any(axis=1).sum())
        lw = int(np.clip(round(thick * 0.8), 2, 12))

    out = np.zeros((*socket.shape, 4), np.float32)
    # 下半椭圆弧：两端略高、中间略低，符合闭眼的弯度
    cv2.ellipse(out, (int(round(cx)), int(round(cy))),
                (max(int(rx), 2), max(int((ys.max() - ys.min()) * 0.09), 2)),
                0, 0, 180, (*[float(c) for c in col], 255.0), lw, cv2.LINE_AA)
    out[..., 3] *= region.astype(np.float32)
    return out


def _lid_shutter(L, socket, region, skin, drop=0.30, sag=0.10):
    """遮挡式半眼：眼球【零形变】，只把上眼睑压下来盖住上半部。

    压缩式（把 ball 压到 sy=0.55）会同时毁掉三样东西：
      眼环变扁椭圆 / 白高光被拉成椭圆 / 虹膜的水平双色分界歪掉，
    而且 lash 留在原位置会和压扁的眼球脱节，中间露一条缝。
    真实半眼是「增加遮挡」，不是「改变形状」。

    眼睑边缘直接复用【原 lash 层向下平移】，粗细/颜色/弯度 100% 跟随角色画风。
    """
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return np.zeros((*socket.shape, 4), np.float32)

    H, W = socket.shape
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    h, w = y1 - y0 + 1, max(x1 - x0 + 1, 2)
    mid_x = (x0 + x1) / 2.0
    base_cut = y0 + drop * h

    def cut_at(x):
        t = np.clip((x - mid_x) / (w / 2.0), -1.0, 1.0)
        return int(base_cut + sag * h * np.sqrt(max(0.0, 1.0 - t * t)))

    sm = (socket > 0).astype(np.uint8)

    shutter = np.zeros((H, W), np.uint8)
    for x in range(x0, x1 + 1):
        shutter[max(0, y0 - 2):cut_at(x), x] = 255
    shutter &= sm * 255
    soft = cv2.GaussianBlur(shutter.astype(np.float32), (0, 0), 1.2) / 255.0

    out = np.zeros((H, W, 4), np.float32)
    out[..., :3] = skin
    out[..., 3] = np.clip(soft, 0, 1) * 255.0

    # 眼睑边缘 = 原 lash 下移。drop 别超过 0.35，否则眼环/高光会被埋掉。
    shift = drop * h
    lid_down = _warp(L['lash'], region, dy=shift)
    if lid_down[..., 3].sum() < 50:                 # lash 缺失时才退回手画线
        m = (L['lash'][..., 3] > 128) & (socket > 0)
        col2 = (np.median(L['lash'][..., :3][m], axis=0)
                if m.sum() > 20 else np.array([35., 30., 30.], np.float32))
        line = np.zeros((H, W), np.uint8)
        pts = np.array([[x, cut_at(x)] for x in range(x0, x1 + 1, 2)], np.int32)
        cv2.polylines(line, [pts], False, 255, 4, cv2.LINE_AA)
        line &= sm * 255
        la = cv2.GaussianBlur(line.astype(np.float32), (0, 0), 0.8) / 255.0
        lid_down = np.zeros((H, W, 4), np.float32)
        lid_down[..., :3] = col2
        lid_down[..., 3] = np.clip(la, 0, 1) * 255.0
    return _over(out, lid_down)


def _iris_zoom(L, region, s=1.10):
    """surprise：只把虹膜放大一点，眼白盘 / 高光 / lash 原地不动。

    整只眼球缩放会让眼环变成椭圆、高光失去镜面反射的圆形轮廓，
    和压缩式半眼是同一类错误（见 _lid_shutter 的说明）。
    """
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return np.zeros((*region.shape, 4), np.float32)
    H, W = region.shape
    cx = (xs.min() + xs.max()) / 2.0
    cy = (ys.min() + ys.max()) / 2.0
    m = np.float32([[s, 0, cx * (1 - s)], [0, s, cy * (1 - s)]])
    iris = cv2.warpAffine(L['iris'], m, (W, H),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_TRANSPARENT)
    iris[..., 3] *= region.astype(np.float32)      # 不跑出本眼
    out = _over(L['disk'].copy(), iris)
    return _over(out, L['spec'])                   # 高光原样，保持正圆


class ExprBank:
    """差分贴片库。离线 build 一次，运行时 apply 只做一次 source-over。"""

    def __init__(self, patches=None, socket=None, mouth_dir=None, bbox=None):
        self.patches = patches or {}       # state -> RGBA float32
        self.socket = socket
        self.mouth_dir = mouth_dir
        # 所有 socket 的外接框（含 2px 余量）：运行时只在这一小块做合成
        self.bbox = bbox
        self.report: list[str] = []      # 眼眶结构异常（多出来的装饰眼等）

    # ---------------- 离线生成 ----------------
    @classmethod
    def build(cls, L, socket, mouth_dir=None,
              half_drop=0.30, half_sag=0.10, report=None):
        # 本仓库的眼眶是 bool 掩膜，cv2 形态学只吃 uint8
        socket = np.asarray(socket)
        if socket.dtype == bool:
            socket = socket.astype(np.uint8)
        eye = _eye_rgba(L, socket)
        halves = _halves(socket, report)
        # 只覆盖「真眼」：socket 里若混进配饰上的装饰眼，任何贴片都不能碰它
        eye_cover = (halves[0] | halves[1])
        p = {}

        # 关键：非 normal 的贴片必须【不透明覆盖】，而不是半透明叠加。
        # cover = 挖掉眼睛后的皮肤底，socket 内 alpha=255。先铺它再叠变形眼，
        # frame 里原来的虹膜/眼白才会被真正盖住 —— 否则就是「画了两遍」= 重影。
        #
        # 不能用 L['base']（inpaint 结果）：上眼睑深色会渗进 socket 上部，
        # 平时被眼球盖住看不见，闭眼时铺满就露成一片黑渐变。
        # 改从 socket 外环取「亮皮肤」中位色填满。
        ring = (cv2.dilate(socket, np.ones((5, 5), np.uint8), iterations=2) > 0) & (socket == 0)
        # 只取 V/S 两个通道做皮肤过滤，它们与 RGB/BGR 顺序无关，
        # 所以这里不引入 bgr 参数：调用方不必关心通道序。
        hsv = cv2.cvtColor(np.clip(L['base'][..., :3], 0, 255).astype(np.uint8),
                           cv2.COLOR_RGB2HSV)
        # 低饱和过滤很重要：眼周是奶油色，但环外还有橙棕头发，
        # 只按 V 取中位会把头发色混进来，遮挡片会铺成一块脏橙色。
        skin_px = L['base'][..., :3][ring & (hsv[..., 2] > 170) &
                                    (hsv[..., 1] < 65)]
        skin = (np.median(skin_px, axis=0) if len(skin_px) > 30
                else np.array([235., 235., 240.], np.float32))
        cover = np.zeros((*socket.shape, 4), np.float32)
        cover[..., :3] = skin
        # 只铺在【真眼】上，不是整个 socket：socket 若混进配饰上的装饰眼，
        # 按整个 socket 铺 cover 会把那块图案抹成一块浅色圆斑（比 wink 污染更严重）。
        eye_cover = (halves[0] | halves[1]).astype(np.uint8)
        a = cv2.GaussianBlur(eye_cover.astype(np.float32), (0, 0), 1.0)
        cover[..., 3] = np.clip(a / max(a.max(), 1e-6), 0, 1) * 255.0

        # 眼球层（不含眼睑）：half / surprise 只该压缩这一层，
        # 把 lash 一起压会让整块深色上眼睑被挤成横向条纹。
        ball = np.zeros((*socket.shape, 3), np.float32)
        for k in ('disk', 'iris', 'spec'):
            ball = _over(ball, L[k])
        ball_rgba = np.zeros((*socket.shape, 4), np.float32)
        ball_rgba[..., :3] = ball
        ball_rgba[..., 3] = cover[..., 3]

        def opaque(parts):
            """铺皮肤底 → 依次叠各部件，保证 socket 内不透明。"""
            out = cover.copy()
            for q in parts:
                out = _over(out, q)
            return out

        # normal 不存贴片：apply 时直接返回原帧，保证零重影
        p['normal'] = None

        # blink：两半都闭
        p['blink'] = opaque([_closed_line(L, socket, r) for r in halves])

        # wink：单侧闭，另一侧原样
        for name, close_idx in (('wink_l', 0), ('wink_r', 1)):
            r = halves[close_idx]
            keep = eye.copy()
            keep[..., 3] *= ((~r) & eye_cover).astype(np.float32)
            p[name] = opaque([keep, _closed_line(L, socket, r)])

        # half / ジト目：眼球完全不动，只用上眼睑遮挡片盖住上半部。
        # 压缩式（sy=0.55）会把眼环压扁、高光拉成椭圆、虹膜分界线挤歪，
        # 且 lash 留在原位与压扁的眼球脱节 —— 这是「太丑」的根因。
        p['half'] = opaque(
            [ball_rgba] +
            [_lid_shutter(L, socket, r, skin,
                          drop=half_drop, sag=half_sag) for r in halves])

        # surprise：只放大虹膜贴片，眼白盘与 lash 原地不动。
        # 整只眼球缩放会让眼环/高光一起变形，和半眼是同一类错误。
        p['surprise'] = opaque([_iris_zoom(L, r) for r in halves] + [L['lash']])

        ys, xs = np.nonzero(socket)
        pad = 2
        bbox = None
        if len(xs):
            h, w = socket.shape
            bbox = (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
                    min(w, int(xs.max()) + 1 + pad), min(h, int(ys.max()) + 1 + pad))
        bank = cls(p, socket, mouth_dir, bbox=bbox)
        bank.report = list(report or [])
        return bank

    def save(self, path):
        """拼成一张图集落盘，附一份同名 .json。"""
        h, w = self.socket.shape
        cols = 3
        rows = (len(STATES) + cols - 1) // cols
        atlas = np.zeros((rows * h, cols * w, 4), np.float32)
        for i, name in enumerate(STATES):
            y, x = divmod(i, cols)
            if self.patches.get(name) is not None:
                atlas[y*h:(y+1)*h, x*w:(x+1)*w] = self.patches[name]
        cv2.imwrite(path, np.clip(atlas, 0, 255).astype(np.uint8))
        meta = dict(states=list(STATES), grid=[rows, cols], tile=[h, w],
                    mouth_dir=self.mouth_dir)
        with open(os.path.splitext(path)[0] + '.json', 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    # ---------------- 运行时 ----------------
    def apply(self, frame, state):
        """frame 可以是 RGB 或 RGBA。normal / 未知状态 → 原样返回。

        只在 socket 外接框里合成：整图 30fps 做浮点混合太贵，而贴片在
        socket 外完全是透明的，框外一个像素都不会变。
        """
        if state not in self.patches or self.patches[state] is None:
            return frame
        patch = self.patches[state]
        if self.bbox is None:
            return np.clip(_over(frame.astype(np.float32), patch),
                           0, 255).astype(np.uint8)
        x0, y0, x1, y1 = self.bbox
        out = frame.copy()
        sub = frame[y0:y1, x0:x1].astype(np.float32)
        out[y0:y1, x0:x1] = np.clip(_over(sub, patch[y0:y1, x0:x1]),
                                    0, 255).astype(np.uint8)
        return out


# ------------------------------------------------------------------ 状态机
class ExprState:
    """
    三条触发源共用一个入口 set()：
      idle 随机池 / 事件（落地回弹、点击）/ 对话 emotion
    """

    def __init__(self, idle_pool=('blink', 'half', 'wink_l', 'wink_r'),
                 idle_ms=(2500, 6500), blink_ms=160, hold_ms=1800):
        self.idle_pool = list(idle_pool)
        self.idle_ms, self.blink_ms, self.hold_ms = idle_ms, blink_ms, hold_ms
        self.state, self.until = 'normal', 0.0
        self.next_idle = time.time() * 1000 + random.uniform(*idle_ms)
        self.listeners = []

    def on_change(self, fn):
        self.listeners.append(fn)

    def _emit(self, old):
        if old != self.state:
            for fn in self.listeners:
                fn(self.state, old)

    def set(self, name, hold_ms=None):
        """对话 emotion / 事件 都调这个。未知状态安全退回 normal。"""
        if name not in STATES:
            name = 'normal'
        old = self.state
        ms = self.blink_ms if name == 'blink' else (hold_ms or self.hold_ms)
        self.state, self.until = name, time.time() * 1000 + ms
        if name != 'normal':
            self.next_idle = self.until + random.uniform(*self.idle_ms)
        self._emit(old)
        return self.state

    def tick(self):
        """每帧调一次（或在 30fps 定时器里）。到期自动回 normal。"""
        now = time.time() * 1000
        if self.state != 'normal' and now >= self.until:
            self.set('normal', hold_ms=1)
        elif self.state == 'normal' and now >= self.next_idle:
            self.set(random.choice(self.idle_pool))
        return self.state

    def from_agent(self, emotion, action=None):
        """对接对话模块的 {text, emotion, action} —— 只认 emotion，action 交给物理层。"""
        alias = {'smile': 'half', 'jito': 'half', 'wink_left': 'wink_l',
                 'wink_right': 'wink_r', 'shy': 'half', 'angry': 'half',
                 'happy': 'surprise', 'sad': 'half'}
        return self.set(alias.get(emotion, emotion))
