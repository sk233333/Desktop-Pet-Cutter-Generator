# 嘴型 overlay 运行时渲染 · 边界文档

> 状态：**T-M1 ~ T-M5 已落，T-M6 已验收**。运行时按素材分辨率源帧合成。
> 关联基线：`origin/main = 74b7ba0`（A1 已结案）；`pytest -q` 133 passed；
> `tag v0.1` = tag 对象 `fe5d76f` → commit `b03cdb7`（未动）。
> 定位：**新功能**，不是「接线」，不是收尾。`mouth.open` 已实现的是**产数据**；
> 本文件讨论的是**运行时消费这份数据**。

## 0. 一句话

把 `mouth.open` 已落盘的独立 RGBA 覆盖层，在 `PetWindow` 取到素材分辨率帧之后、
缩放到显示尺寸之前，按 alpha bbox 合成一次；不新增表情状态、不进 `ExprBank`、
不改 `arbiter`、不改 manifest v2。

## 1. 现状事实（读码复核）

| # | 事实 | 位置 |
|---|---|---|
| F1 | `mouth.open` 落盘 `items[i]/mouth_dir = "<stem>_mouth_<level>.png"` 与 `items[i]/mouth_level` | `completers/mouth_open.py:127-137` |
| F2 | 覆盖层是**素材原尺寸** RGBA，背景全透明 | `mouth_open.py:125-126` |
| F3 | `ExprBank.build(mouth_dir=...)` 只把值存到实例上，`apply()` 完全不读 | `expr_state.py:221-224, 339-356` |
| F4 | `ExprBank.save()` 只把 `mouth_dir` 写进图集 meta，不做合成 | `expr_state.py:321-336` |
| F5 | `anim.build_expr_bank()` 调 `ExprBank.build()` 时**没有传** `mouth_dir` | `anim.py:2048+` |
| F6 | `PetAsset` 只保留素材像素与路径，不保留 manifest item，因此运行时拿不到 `mouth_dir` | `anim.py` PetAsset；`pet_window.py:184` |
| F7 | `PetWindow._pick_frame()` 先选素材分辨率源帧（blink / group-blink / expr / gaze / base），再叠加头发摆动 | `pet_window.py:562-593` |
| F8 | `_over()` 已存在，支持 RGB/RGBA 的 source-over | `expr_state.py:20-30` |
| F9 | manifest schema **没有** `mouth_dir` 字段，`validate_manifest` 不校验它；`read_manifest` 原样保留 | `manifest.schema.json`、`manifest_io.py` |
| F10 | `PetWindow` 已有三处构造点（`launch.py` / `pet_window.__main__` / `ui_app.py`），不走统一工厂 | grep `PetWindow(` |

结论：数据在，消费者不在；而且**运行时根本读不到 `mouth_dir`**（F6）。
所以这不是「补一行参数」，至少要新增「取字段 + 加载 + 合成」三件事。

## 2. 范围

### 做

- 每条 manifest item 的 `mouth_dir` 指向的**一张静态覆盖层**；
- 在桌宠运行时合成，覆盖 base / blink / group-blink / expr / gaze 五条取帧路径；
- 覆盖层缺失、损坏、尺寸不符、路径越界时**安全跳过**，基础帧逐像素不变；
- 新增 Qt-free 的 `mouth_overlay.py` + 测试；
- 不改 `mouth.open` 的产物格式；它已经写对了。

### 不做

- 口パク / 五元音 / 音频驱动 / 嘴型随时间开合；
- 按表情状态切不同嘴型（`smile` 只在闭眼时闭嘴等）；
- 自动对齐、透视、形变、光照匹配；
- 新增 `mouth` 表情状态、改 `STATES` / 别名表 / `apply()` 签名；
- 改 `arbiter` 通道矩阵、新增仲裁通道；
- 写 manifest、升 schema、改 `mouth.open`；
- 配置期 AI 候选、权重、网络。

## 3. 推荐方案（Q1 选 B）

### 3.1 数据访问：Q1 = B，必须走 `read_manifest()`

- 读 manifest **只允许** `read_manifest(path)`，禁止裸 `json.load`；否则 v1 降级、
  `_derive_legacy()` 派生视图、`_normalize_groups()` 归一化全被绕过，等于第二份状态源。
- 只读、不写、不反写；同一真源读两次不构成分叉。
- `resolve_asset()` 已返回 `(path, item)`；若调用点已拿到 item，**优先复用**，连重读都省。
  首期 `PetWindow` 增加可选 `item: dict | None = None`：传了直接用；没传才用
  `read_manifest(asset.source.parent / "manifest.json")` 重读。
- 可选参数默认 `None`，所有既有 `PetWindow` 构造点零改动。
- 没传而走重读时：重读失败 / 无匹配 item → **静默跳过**，输出与 `74b7ba0` 逐比特一致；
  不给旧调用点凭空增加一次 manifest IO 的失败面。
- `asset.source` 为空（直接加载单图）→ 无 item，跳过。
- 按 `stem` 精确匹配；命中多条 → 跳过并 warn，不猜第一条（Q3）。

### 3.2 新模块 `desktop_pet/mouth_overlay.py`（Qt-free）

```python
class MouthOverlay:
    @classmethod
    def load(cls, sku_dir, name, size) -> "MouthOverlay | None":
        """只读；拒绝绝对路径 / `..`；尺寸必须等于 size；空 alpha 返回 None。"""

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """返回新数组；只在 alpha bbox 内做 source-over，bbox 外逐比特不变。"""
```

- 只用 `numpy` + `PIL`（CI 五包已有；避开 `cv2.imread` 的 BGR/RGB 顺序坑）；
- 加载时预计算 `alpha > 0` 的 bbox 与 RGBA 子图；
- `apply()` 先 `frame.copy()`，再只改 bbox；不原地改调用方缓存；
- 只处理 RGBA（4 通道）；RGB（3 通道）帧不做合成、原样返回同一数组；
- 合成公式 = 按 src alpha 混 RGB、**dst alpha 原样保留**（非标准 Porter-Duff）；
- 不做全图浮点混合（30fps 成本可控）。

### 3.3 `PetWindow` 接线（唯一改动的现有文件）

1. `__init__`：解析 `mouth_dir` → `MouthOverlay.load(...)`，失败只打一行 warning；
2. **素材分辨率源帧**：`self._render_rgba = self._mouth.apply(asset.rgba)`，
   全局只此一处 `apply`；原 `asset.rgba` 不改；
3. `base / base_rgba / raw_blink / expr / gaze` 全部从 `_render_rgba` 派生；
   `_pick_frame()` 零改动；
4. 不新增 UI 开关；`mouth_level` 只用于日志/报告，v1 不参与切换；
5. 覆盖层在素材分辨率合成一次，随后照旧 `resize_rgba` 到显示高度。

### 3.4 为什么在素材分辨率源帧合成，而不是 `_pick_frame()`

> **T-M3 实现裁定：采用源帧统一叠加（A 方案）。**
> 读码事实：四路取帧到汇合点时已是显示尺寸，字面在 `_pick_frame()` 插入
> 会因 `MouthOverlay.load` 的 size 不符而静默返回原数组，不崩不报。
> 实现：`__init__` 中一次 `_render_rgba = _mouth.apply(asset.rgba)`，
> `base / base_rgba / raw_blink / expr / gaze` 四路均从该源帧派生；
> `_pick_frame()` 零改动；Q2 静态常驻，一次合成即够。
> **原「`_pick_frame()` 唯一合成点」表述作废，以本节为准。**

- `_pick_frame()` 的 base / blink / expr / gaze 取到时都已是**显示尺寸**
  （`resize_rgba` 已在各自缓存里发生），汇合点之后没有缩放；在那里插 `apply`
  会因尺寸不符静默失效（也就是 T-M2 契约警告的那条）。
- 所以在 `__init__` 对 `asset.rgba` 合成一次，四路从 `_render_rgba` 派生；
  既保住「素材分辨率合成后再缩放」，也只有一个合成点。
- `ExprBank` 仍是表情贴片库，嘴型是独立图层；不新增状态、不改 `apply()` 签名。

## 4. 补丁顺序（T-M1 ~ T-M6）

| 序 | 补丁 | 文件 | 不做什么 |
|---|---|---|---|
| T-M1 | `MouthOverlay.load/apply` + 纯逻辑测试 | 新增 `mouth_overlay.py` / `tests/test_mouth_overlay.py` | 不接 PetWindow |
| T-M2 | 取 item：可选参数（默认 `None`，旧构造点零改动）+ `read_manifest()` 回退 + 路径守卫 | `pet_window.py` | 不合成、不改签名语义；重读失败静默跳过 |
| T-M3 | 素材分辨率源帧合成（`_render_rgba`），四路派生 | `pet_window.py` | 不改 `_pick_frame` / anim / ExprBank |
| T-M4 | 四类失败 + 四路 golden + 文件哈希 + 双 SKU 下沉到 Qt-free `tests/test_mouth_runtime_ci.py`；仅 200 帧真实 `_pick_frame` 留在 GUI 文件 | 测试 | 不合并参数化；不在 CI 里 skip 集成断言 |
| T-M5 | CHANGELOG / README / 本文件同步（含 PetPreview 不同步） | 文档 | 不写「已统一」 |
| T-M6 | 干净 venv + 离屏 200 帧 + 干净树 | — | 不加依赖 |

每一步单独可回滚；T-M3 之后无 `mouth_dir` 的 SKU 仍与旧行为逐比特一致。

### T-M1 补钉（已并入 T-M1 commit）

- `_WARNED` key = `(sku_dir, name, kind)`；不同 SKU 同名缺失各打一行，补对应断言。
- RGB（3 通道）帧不做合成、原样返回同一数组；补断言。
- 参考合成公式 = 按 src alpha 混 RGB、dst alpha 原样保留（非标准 Porter-Duff）；
  docstring 写明，C-M2 参考实现按此对齐。
- 断言数变化后 commit message 按实际值写，不照抄 147。

### T-M2 接线契约（下一轮，不改 T-M1）

- `load(size)` 的 `size` 必须是**原始素材分辨率**；合成为素材分辨率源帧（`__init__`
  一次），`_pick_frame()` 不调用 `apply()`。传显示帧会命中「尺寸不符」静默返回
  原数组——不崩，但看起来像没生效。
- `mouth_dir` + `mouth_level` 拼出的 name 必须整体过 `load()` 的路径守卫，
  不在拼接处绕过（例如 `mouth_level` 带 `..`）。
- 可选 `item` 默认 `None`、旧构造点零改动；`resolve_asset()` 已返回 item 时优先复用；
  重读失败 / 无匹配 → 静默跳过且逐比特一致。

## 5. 断言（可执行）

| 编号 | 断言 |
|---|---|
| C-M1 | **仅 PetWindow 侧**：无 `mouth_dir` 时，base / blink / expr / gaze 四路输出与 `74b7ba0` 逐字节一致；**不含** `ui_app.PetPreview`（首期不同步，见 Q4）。CI 版在 `tests/test_mouth_runtime_ci.py::test_cm1_*`。**比较层级**：逐比特成立在**缩放前的素材分辨率源帧**；显示尺寸会被 `resize_rgba` 重采样整帧，只用于确认识别得到覆盖层颜色，不做逐比特 |
| C-M2 | 有覆盖层时，alpha bbox 外逐比特不变；bbox 内按「src alpha 混 RGB、dst alpha 原样保留」与参考一致 |
| C-M3a | 尺寸不符 → 不崩溃、warn、输出等于 C-M1（独立用例，不与 b/c 合并；CI 版 `test_cm3_bad_overlay_falls_back[size]`） |
| C-M3b | 文件损坏 / 解码失败 → 不崩溃、warn、输出等于 C-M1（独立用例；CI 版 `[corrupt]`） |
| C-M3c | 文件缺失 → 不崩溃、warn、输出等于 C-M1（独立用例；CI 版 `[missing]`） |
| C-M3d | **全透明 PNG（alpha bbox 为空）** → 不崩溃、warn、输出等于 C-M1（独立用例；CI 版 `[transparent]`） |
| C-M4 | `mouth_dir` 含 `..` 或绝对路径 → 拒绝加载，不读 SKU 目录外文件 |
| C-M5 | 运行时零 manifest 写入；`git status` 无 manifest / mask PNG 变化。CI 版 `test_cm5_apply_200_frames_zero_writes` 比对字节 + mtime |
| C-M6 | `anim.py` / `expr_state.py` / `arbiter.py` / manifest v2 / 摆动线零 diff；CI 版额外断言这些模块不引用 `mouth_overlay` |
| C-M7 | `STATES` 六态、`smile → half` 别名、`apply()` 签名不变；不新增 `mouth` 状态（CI 版 `test_cm7_*`） |
| C-M8 | 两条 SKU：一条有 overlay、一条无；互不串图（CI 版 `test_cm8_*`） |
| C-M9 | 200 帧离屏（base / blink / expr / gaze 各 50）无异常、无尺寸漂移；真实帧循环留在 GUI 文件 |
| C-M10 | 干净 venv 五包（无 PyQt6）跑 `pytest -q`，不低于当前基线；Qt-free 用例全过，**仅 `test_pet_window_mouth.py` 一个文件整体 skip（skip ≤ 1）** |
| C-M11 | `mouth_dir` 为空字符串 / `None` / 非字符串 → 按无 overlay 处理，不抛异常 |
| C-M12 | 覆盖层 alpha 全 0 → 按无 overlay 处理（见 C-M3d），不 warn 刷屏 |
| C-M13 | 有 / 无 `mouth_dir` 时 `derive_allowed()` 输出逐项相同；**不得**因补嘴型把 `smile` 加进词表 |

## 6. MUST NOT

1. 不新增 `ExprBank` 状态、不改 `STATES` / 别名表 / `apply()` 签名；
2. 不动 `arbiter.py` 通道矩阵，不新增 `mouth` 通道；
3. 不写 manifest、不升 schema、不改 `mouth.open` 产物格式；
4. 不做全图逐帧浮点合成；只在 bbox 内做；
5. 不自动形变/对齐；覆盖层像素即最终像素；
6. 不把嘴型和聊天/表情状态绑定（v1 静态）；
7. 不碰 `render_frame`、摆动线已结案文件、`tag v0.1`；
8. 不为看效果去装依赖；测试必须能在 CI 五包跑。

## 7. 已裁定（Q1–Q4）

- **Q1 = B**：`PetWindow` 侧取 item；**必须走 `read_manifest()`**，禁止裸 `json.load`；
  只读不写；`resolve_asset()` 已返回 item 时优先复用，否则按 `stem` 精确重读。
  可选 `item` 默认 `None`，旧构造点零改动；重读失败 / 无匹配 → 静默跳过且输出零回归。
- **Q2 = 静态常驻**：`mouth_dir` 指向哪一档，就常驻显示哪一档；无 `mouth_dir` 时完全不合成。
  「说话才张嘴」要接 A1 的 chunk 信号切档，是另一条功能，排 v0.1.1，**不估成小改**；
  STATES / 别名表 / `apply()` 签名 / A1 worker 全不碰。
- **Q3 = 单 item 精确匹配**：`stem` 命中多条 → 跳过 + warn，不猜第一条；
  与「禁止静默 zip 截断」同一原则。
- **Q4 = 首期不同步编辑器预览**：已读码确认 `PetPreview` 是 `ui_app.py` 里独立取帧管线，
  **不走** `PetWindow._pick_frame()`；首期只做桌面运行时，CHANGELOG 显式记录该差异，
  按「显式接受」处理，不写「已统一」。

## 8. 状态

| 项 | 状态 |
|---|---|
| 本文件（边界 + 契约 + 断言 + Q1–Q4 裁定） | ✅ 已落盘 |
| T-M1 `MouthOverlay` | ✅ `4e10451` |
| T-M2 取 item / 加载 | ✅ `18c8937` |
| T-M3 素材分辨率源帧合成 | ✅ T-M3 提交（`pet_window.py` + 测试） |
| T-M4 四类失败 + 200 帧零写盘 | ✅ 原始提交 `0bf4a66`；CI 下沉 `6c63658`（Qt-free `tests/test_mouth_runtime_ci.py`，skip 2 → 1） |
| T-M5 文档同步 | ✅ T-M5 提交 `de70718` |
| T-M6 干净 venv / 干净树 | ✅ 干净 venv `171 passed, 1 skipped`（仅 PyQt6 GUI 文件）；本地 `175 passed` |
| CI 集成断言下沉（P0） | ✅ `6c63658`：C-M1 / C-M3a~d / C-M5~C-M8 在 CI 五包真跑；GUI 文件只留 200 帧 |
| `mouth_dir` 数据槽 | ✅ 已由桌面运行时静态消费；编辑器 `PetPreview` 仍不同步 |
| A1 / 向导 / 摆动线 / tag | 🔒 不动 |
