# 嘴型 overlay 运行时渲染 · 边界文档

> 状态：**边界已定、Q1–Q4 已裁定，仓库零代码改动**。本轮只落文档，不产补丁。
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

- 只用 `numpy` + `cv2`（CI 五包已有）；
- 加载时预计算 `alpha > 0` 的 bbox 与 RGBA 子图；
- `apply()` 先 `frame.copy()`，再只改 bbox；不原地改调用方缓存；
- 不做全图浮点混合（30fps 成本可控）。

### 3.3 `PetWindow` 接线（唯一改动的现有文件）

1. `__init__`：解析 `mouth_dir` → `MouthOverlay.load(...)`，失败只打一行 warning；
2. `_pick_frame()`：在 `src` 选定后、`_hair_active()` 之前：
   `if self._mouth is not None: src = self._mouth.apply(src)`；
3. 不新增 UI 开关；`mouth_level` 只用于日志/报告，v1 不参与切换；
4. 覆盖层只在素材分辨率合成，随后照旧 `resize_rgba` 到显示高度。

### 3.4 为什么放在 `_pick_frame` 而不是 `ExprBank`

- `ExprBank` 是**表情贴片库**，六个状态 + 一个共用 socket；嘴型是**独立图层**，
  塞进去会逼着新增状态或改 `apply()`，正好踩中前几轮的 MUST NOT；
- `_pick_frame` 是五条取帧路径的唯一汇合点，一处合成即可覆盖 blink / gaze / expr；
- 以后真要按状态切嘴型，也只需在这一层加映射，不动 `STATES`。

## 4. 补丁顺序（T-M1 ~ T-M6）

| 序 | 补丁 | 文件 | 不做什么 |
|---|---|---|---|
| T-M1 | `MouthOverlay.load/apply` + 纯逻辑测试 | 新增 `mouth_overlay.py` / `tests/test_mouth_overlay.py` | 不接 PetWindow |
| T-M2 | 取 item：可选参数（默认 `None`，旧构造点零改动）+ `read_manifest()` 回退 + 路径守卫 | `pet_window.py` | 不合成、不改签名语义；重读失败静默跳过 |
| T-M3 | `_pick_frame()` 合成（素材分辨率、alpha bbox、hair 之前） | `pet_window.py` | 不动 anim / ExprBank |
| T-M4 | 尺寸不符 / 损坏 / 缺失三类独立用例 + 五路取帧断言 | 测试 | 不合并参数化 |
| T-M5 | CHANGELOG / README 差异登记（含 PetPreview 不同步） | 文档 | 不写「已统一」 |
| T-M6 | 干净 venv + 离屏 200 帧冒烟 | — | 不加依赖 |

每一步单独可回滚；T-M3 之前 `mouth_dir` 仍不消费，旧行为零变化。

## 5. 断言（可执行）

| 编号 | 断言 |
|---|---|
| C-M1 | **仅 PetWindow 侧**：无 `mouth_dir` 时，`_pick_frame` 的 base / blink / expr / gaze 四路输出与 `74b7ba0` 逐字节一致；**不含** `ui_app.PetPreview`（首期不同步，见 Q4） |
| C-M2 | 有覆盖层时，alpha bbox 外逐比特不变；bbox 内等于参考 `_over(base, overlay)` |
| C-M3a | 尺寸不符 → 不崩溃、warn、输出等于 C-M1（独立用例，不与 b/c 合并） |
| C-M3b | 文件损坏 / 解码失败 → 不崩溃、warn、输出等于 C-M1（独立用例） |
| C-M3c | 文件缺失 → 不崩溃、warn、输出等于 C-M1（独立用例） |
| C-M3d | **全透明 PNG（alpha bbox 为空）** → 不崩溃、warn、输出等于 C-M1（独立用例，不并进参数化） |
| C-M4 | `mouth_dir` 含 `..` 或绝对路径 → 拒绝加载，不读 SKU 目录外文件 |
| C-M5 | 运行时零 manifest 写入；`git status` 无 manifest / mask PNG 变化 |
| C-M6 | `anim.py` / `expr_state.py` / `arbiter.py` / manifest v2 / 摆动线零 diff |
| C-M7 | `STATES` 六态、`smile → half` 别名、`apply()` 签名不变；不新增 `mouth` 状态 |
| C-M8 | 两条 SKU：一条有 overlay、一条无；互不串图 |
| C-M9 | 200 帧离屏（base / blink / expr / gaze 各 50）无异常、无尺寸漂移 |
| C-M10 | 干净 venv 五包（无 PyQt6）跑 `pytest -q`，≥133 passed，新增测试全部 Qt-free |
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
| docs 小提交 | ✅ 本次提交（仅两份文档） |
| 可运行代码 / 测试 | ❌ 未产，仓库零代码改动 |
| `mouth_dir` 数据槽 | 🔒 仍未被运行时消费；下一轮从 T-M1 起 |
| A1 / 向导 / 摆动线 / tag | 🔒 不动 |
