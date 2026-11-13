# 桌宠素材生成器 · 项目交接文档

> 面向接手的 AI / 开发者。目标是让你**不看对话历史**也能上手改代码。
> 最后更新：2026-11-12。文中路径都相对仓库根目录 `Desktop-Pet-Cutter-Generator/`。

---

## 1. 项目是什么

一句话：**一张手办/插画照片 → 透明 PNG 桌宠 → 能跑会动、能眨眼、会做表情、会掉到小桌子上、能双击启动本机程序的桌面宠物。**

技术栈：Python 3 + PyQt6 + OpenCV + NumPy + Pillow。全部本地运行，不联网、不依赖任何模型服务。

三层职责：

| 层 | 目录/文件 | 负责 |
|---|---|---|
| 素材生产 | `desktop_pet/ui_app.py`（主界面）、`cutout_to_pet.py`、`launch.py` | 选图、抠图、标眼睛、生成 `_pet.png` 与配套 mask/manifest |
| 运行时素材模型 | `desktop_pet/anim.py` | 加载素材、眼睛分层、眨眼时间轴、视线、头发、表情差分贴片 |
| 桌面外壳 | `desktop_pet/pet_window.py` + `action_bridge.py` | 透明窗口、拖动、物理、右键菜单、动作、待机编排 |

---

## 2. 目录与文件职责

```
desktop_pet/
├── ui_app.py            5479 行  单窗口主界面（LaunchUI）+ 画布 ImageCanvas + 编辑器预览 PetPreview
├── launch.py            1054 行  CLI：抠图/掩膜估计/manifest 读写/眼睛标注；UI 复用其函数
├── cutout_to_pet.py      655 行  核心烘焙：去背 → 描边锐化 → 眼睛 mask 映射 → 写 manifest
├── anim.py              2097 行  运行时素材：PetAsset / 眨眼 / 视线 / 头发 / ExprBank 构建
├── pet_window.py        1213 行  透明桌宠窗口：tick 循环、拖动、物理、菜单、气泡
├── expr_state.py         408 行  表情差分贴片 + 状态机（半眼/惊讶/单侧眨眼）
├── effect_director.py    112 行  待机效果编排（mood × 权重 × 多通道）
├── effect_table.json            mood 与效果权重表（可直接改）
├── action_bridge.py      244 行  本地动作执行：白名单、确认、单实例、跨平台解析
├── action_dialog.py      210 行  「动作设置」图形界面（替代手改 JSON）
├── ui_assets/                   UI 用的小图标（chevron 等）
├── assets/<sku>/                生成物：<stem>_pet.png、_pet_eyes*.png、manifest.json、_pet.json
├── source/                      原始图片
tests/
├── conftest.py                  合成眼 fixture（不依赖真实素材）
└── test_half_and_effects.py     17 个验收用例（B1–B13 超集）
docs/
├── HANDOFF_half_effects_v2.md   上一轮表情/半眼交接的增量修订
└── HANDOFF_PROJECT.md           本文
```

其它：`README.md`（面向用户的功能说明）、`desktop-pet-guide.md`（早期设计稿）、`Punctuation_selection_of_images/`（示例图）。

---

## 3. 怎么跑

```bash
# 依赖
pip install PyQt6 opencv-python numpy pillow

# 主界面（推荐）
python3 desktop_pet/launch.py --ui
# 或
python3 desktop_pet/ui_app.py

# 直接起桌宠（已有素材时）
python3 desktop_pet/pet_window.py            # 默认取 assets 里最新一只
python3 desktop_pet/pet_window.py --no-gaze  # 关视线
```

无显示环境下（CI）用 `QT_QPA_PLATFORM=offscreen`。

### 测试

```bash
python3 -m pytest tests -q        # 17 passed
```

覆盖：半眼零形变、白高光形状、惊讶不缩放眼球/lash 逐像素、眨眼线宽自适应、mask 裁边+缩放换算、待机效果（不连播/不打断/idle 下限/逐 SKU 开关/mood）、装饰眼不被涂改。

---

## 4. 数据流（一张图走完全程）

```
源图 ──[ui_app 选图/框取]──> 前景+alpha
     ──[cutout_to_pet.build_one]──> <stem>_pet.png（RGBA）
                                     <stem>_pet_eyes.png（R/G 通道 = 两只眼）
                                     manifest.json（尺寸、锚点、眼睛、档位、周末效果）
     ──[anim.PetAsset.load]──> 眼睛分层 disk/iris/spec/lash + 眨眼帧 + 视线 geo
     ──[PetWindow]──> 30fps 合成帧 → 透明置顶窗口
```

关键点：**运行时每次合成都不重新抠图**；贴片（眨眼/表情）是整帧上的一小块 source-over。

---

## 5. 核心代码地图

### 5.1 `cutout_to_pet.py`

| 函数 | 作用 |
|---|---|
| `build_one(...)` | 一次完整烘焙：去背、锐化描边、眼睛 mask 映射、写 png/manifest/json |
| `map_eye_masks(sockets, hl, crop, size)` | **把眼眶 mask 从源图坐标搬到素材坐标**：先裁 `crop`，再缩放到素材尺寸。少一步就会整体偏移 |
| `load_eye_masks(png, shape)` | 读 `_pet_eyes*.png`，尺寸不符时忽略并告警 |
| `save_eye_masks(...)` | 每张文件存两只眼（R/G 通道），第三只起另起文件 |
| `boxes_from_eye_masks` / `eye_highlight_mask` | 从 mask 推外接框/高光 |
| `fallback_alpha` / `sharpen_cut_edges` | 无 alpha 时的兜底与描边 |

烘焙校验（本轮新增）：统计每只眼眶的连通域数，`!= 1` 时打印 `[warn] ... 眼眶连通域` 并写进 manifest 的 `eye_domains`。多出来的域通常是配饰上的「装饰眼」，必须重标。

### 5.2 `anim.py`

| 类/函数 | 作用 |
|---|---|
| `PetAsset` | 素材模型：`rgba`、`eyes`、`blink` 帧、`gaze_on`、`eye_level`、`anchor`、`effects_cfg` |
| `PetAsset.load(...)` | 读 png/json/manifest；尺寸不符的 mask 直接忽略 |
| `build_gaze_geos` | 按眼眶把眼睛切成 `disk / iris / spec / lash` 四层 + 每层 bbox |
| `gaze_frame` | 视线的**旧渲染路径**（remap），仍在使用 |
| `blink_group_counts` / `make_blink_frames` | 眨眼时间轴：把上下眼睑合成闭眼帧 |
| `HairField` / `HairWisp` | 头发摆动（二次元发缕的轻量物理） |
| `build_expr_bank(asset)` | **新**：把四层按 `geo.box` 贴回整图，交给 `ExprBank` 生成 6 个差分贴片 |
| `_iris_mask` / `_validate_iris` | 校验眼眶里是否真有虹膜（转身/闭眼素材会失败） |

注意：`build_gaze_geos` 一份数据同时服务「旧视线」「眨眼」「头发」「表情贴片」——**不要整体替换它**。

### 5.3 `expr_state.py`（表情）

```python
ExprBank.build(L, socket, mouth_dir=None, half_drop=0.30, half_sag=0.10, report=None)
ExprBank.apply(frame, "half")   # -> 新帧；"normal" 返回原帧（零操作）
```

| 状态 | 实现 |
|---|---|
| `normal` | `None`，不产生任何改动 |
| `blink` | 闭眼线：线宽按 `lash` 厚度自适应（clip 2~12px），统计区域 `& (socket > 0)` 排除眉毛 |
| `half` | **遮挡式**：眼球零形变，`_lid_shutter` 用皮肤色压住上半，睑缘 = 原 `lash` 下移 `drop*h` |
| `surprise` | `_iris_zoom`：**只放大虹膜 1.1 倍**，眼白盘/高光/lash 原地不动 |
| `wink_l/r` | 一侧压成闭眼线，另一侧保留原眼层 |

不变量（改代码前必读）：

1. `opaque()` 的 cover **只铺在真眼并集**上，不是整个 socket——否则配饰会被涂成浅色圆斑/黑块。
2. `_halves()` 用「面积最大的两个连通域」当左右眼；离群判据 `dy > max(0.8*dx+2, 0.5*眼高)`（歪头允许 7~8px 高差）。
3. 半眼的上限 `half_drop ≤ 0.35`，超过会埋掉眼白。
4. **没有 `bgr` 参数**：肤色过滤只用 HSV 的 S/V，与 RGB/BGR 顺序无关。

### 5.4 `pet_window.py`（桌面外壳）

`PetWindow(QWidget)` + 30fps `QTimer.tick()`：

| 功能 | 实现要点 |
|---|---|
| 透明置顶 | `WA_TranslucentBackground` + `FramelessWindowHint` + 置顶；逐帧 `setMask` 只让角色像素可点 |
| 拖动 / 贴边 | 鼠标拖动窗口；落点吸附屏幕边缘 |
| 缩放 | 右键「放大放缓 / 缩小加快 / 恢复大小」，改窗口尺寸并重建帧缓存 |
| 眨眼 | 时间轴（`blink` 帧组），与表情互斥优先级：blink > expr > gaze |
| 视线跟随 | 光标位置 → 素材像素 → 虹膜偏移，一阶缓动 |
| 头发 | `HairField` 每帧推进 |
| 待机编排 | `EffectDirector.tick()` 驱动表情/视线/气泡三通道 |
| 气泡 | `_bubble(text, ms)`：桌宠头顶的小气泡，用于动作反馈与表情符号 |
| 右键菜单 | 动作列表 → 表情（随机开关 + 试一个）→ 暂停/继续 → 缩放 → 退出 → 动作设置… |
| 双击 | 只执行 `double_tap` 绑定；**没绑定就什么都不做**（暂停已移到菜单）；拖动过则忽略 |
| 物理（小桌子） | 见 5.5 |

### 5.5 画布端的「小桌子」（`ui_app.py::ImageCanvas`）

| 功能 | 实现 |
|---|---|
| 开关 | 竖向工具栏第 6 个可勾选按钮「小桌子」 |
| 重力 | `acc = max(500, h*5) * fall`，`vy += acc*dt`，落地判定用平台世界宽度 `_plat_w` |
| 回弹 | 撞平台且 `vy > 140` 且 `bounce > 0.02` → `vy = -impact*bounce` |
| 小手拖拽 | 拖起时锁定，松手把拖拽速度交给物理 |
| 左右推动 | 松手保留横向速度，`exp(-2.4*dt)` 阻尼滑行；滑出平台边缘就掉落 |
| 掉出平台 | 一路掉到**可视底部**才 `_respawn_center()` 跳回平台正中心 |
| 平台宽度 | 「动作参数 → 平台宽度」，10%~2000%，**对数刻度滑条**；宽度是**固定世界值** |
| 固定世界约定 | 缩放只改变镜头，不重算平台宽；平台宽只在开桌/拖滑条/换图时定一次 |

> 注意：这里的「平台」是编辑器画布里的模拟；桌面壳的 `PetWindow` 用屏幕边缘/任务栏当“地”，两者逻辑独立。

### 5.6 `effect_director.py` + `effect_table.json`（待机编排）

```json
{
  "moods":   { "calm": {"weights": {"blink":0.45,"glance":0.25,"bubble":0.15,"half":0.15}, "idle_ms":[3500,8000]}, ... },
  "effects": { "glance": {"expr":"normal","hold_ms":700,"gaze":{"gx":-0.7,"gy":0.15}}, ... }
}
```

- 每个 tick：到期 → 按当前 mood 权重抽一个**未连播**的效果 → 同时设置表情、视线偏移、气泡
- 刚被摸过（`touch()`）3 秒内不触发，也不换 mood；mood 冷却 20~40s；`idle_ms` 不缩短（最短 2.5s）
- 逐素材覆盖：manifest 的 `"effects": {"enabled": [...], "mood": "sleepy"}`，缺省回落全局表

### 5.7 `action_bridge.py` + `action_dialog.py`（系统动作）

配置两层合并，用户层优先：

- 内置：`assets/<sku>/actions.json`（决定有哪些动作）
- 用户：`~/.config/desktop_pet/actions.json`（真实路径、开关、手势）

```json
{
  "enabled": true,
  "bindings": { "double_tap": "launch:wechat" },
  "paths": { "launch:wechat": { "path": "/usr/bin/wechat" } },
  "registry": {
    "launch:wechat": {"kind":"app","label":"打开微信","confirm":true,
                      "linux":["wechat"],"single_instance":true}
  }
}
```

安全边界（**改代码不要破坏**）：

- 只认 `action_id` 白名单，**绝不接受路径字符串**；未知 id 静默返回 False
- `QProcess.startDetached(argv[0], argv[1:])`，永不 `shell=True`、永不拼命令串
- 启动类默认二次确认；`single_instance` 已运行只提示不重复拉起
- `actions.enabled` 默认 **false**，逐 SKU 开
- 关闭/删除/写文件类动作不提供实现

`action_dialog.py` 是图形化配置：勾「启用动作」→ 选可执行文件 → 勾「双击触发」→ 保存即生效（写用户层 JSON）。

---

## 6. 配置文件格式

### `assets/<sku>/manifest.json`（每个素材一条 `items[]`）

```json
{
  "stem": "xxx_pet",
  "display_height_px": 380,
  "anchor": {"x": 0.5, "y": 0.99},
  "eyes": [{"cx": 146.3, "cy": 117.1, "level": "L2"}, ...],
  "eyes_mask": ["xxx_pet_eyes.png", "xxx_pet_eyes_2.png"],
  "eye_domains": [1, 1],
  "eye_level": ["L2", "L2"],
  "gaze": true,
  "effects": {"enabled": ["blink", "half"], "mood": "calm"}
}
```

可选手写字段：`gaze`（false 关视线但保留眨眼）、`effects`（逐素材效果开关）、`eye_domains`（烘焙时自动写）。

### 表情状态名（`expr_state.STATES`）

`normal / blink / half / surprise / wink_l / wink_r`

---

## 7. 功能总表（现状）

| # | 功能 | 位置 | 状态 |
|---|---|---|---|
| 1 | 选图 / 缩放拖动 / 框取 | ui_app ImageCanvas | ✅ |
| 2 | 自动去背 + 手动掩膜 | launch / cutout_to_pet | ✅ |
| 3 | 左右键标眼睛、多边形眼眶 | ui_app | ✅ |
| 4 | 四联预览、多边形发缕 | ui_app | ✅ |
| 5 | 生成 `_pet.png` + eyes mask + manifest | cutout_to_pet.build_one | ✅ |
| 6 | 桌宠窗口（透明/置顶/拖动/贴边） | pet_window | ✅ |
| 7 | 眨眼时间轴 / 视线跟随 / 头发摆动 | anim + pet_window | ✅ |
| 8 | 表情：半眼/惊讶/单侧眨眼 | expr_state | ✅（遮挡式） |
| 9 | 待机编排（mood × 多通道） | effect_director | ✅ A 档 5/6 |
| 10 | 系统动作（双击/右键启动程序） | action_bridge | ✅ |
| 11 | 动作设置图形界面 | action_dialog | ✅ |
| 12 | 桌宠空间 / 回收站 | ui_app | ✅ |
| 13 | 小桌子（重力/回弹/推动/固定世界宽度） | ui_app ImageCanvas | ✅ |
| 14 | 呼吸/摇摆随机化、4 种眨眼时序、组合规则 | — | ❌ 未做（依赖不存在的 body 通道） |

---

## 8. 必须遵守的约定（踩过坑）

1. **固定世界**：画布缩放只改镜头；平台宽、桌宠尺寸、贴片尺寸都不随缩放重算。
2. **不要整体替换 `build_gaze_geos` / `gaze_frame`**：眨眼、头发、表情都挂在同一份眼睛分层上，只能“功能剥离 + 渐进接管”。
3. **`opaque()` 的 cover 只铺真眼并集**，别回到整个 socket。
4. **idle_ms 不缩短**（最短 2.5s）；用户刚交互 3s 内不打断。
5. **不新增贴片与依赖**：表情/效果都靠组合已有通道实现。
6. **动作执行永远 argv 列表 + 白名单 id**。
7. `expr_state` 没有 `bgr` 参数，别再塞回来。
8. `half_drop ≤ 0.35`；惊讶禁止缩放整只眼球。

---

## 9. 已知问题 / 限制

| 问题 | 影响 | 建议 |
|---|---|---|
| `assets/custom/231eab…` 的眼眶很粗（约 `(32,56)-(208,156)`，糊住上半脸） | 非 normal 表情会重画一片脸（皮肤中位色，多半能融进去，但可能盖掉眉毛细节） | 用 SAM 重标一张紧贴眼眶的 mask |
| 第三只「装饰眼」曾混进 mask | 所有非 normal 表情会涂掉配饰 | 已修：数据删除 + `_halves` 前二 + cover 收窄；其它 SKU 若 `report` 有提示需重标 |
| 待机编排只有 5/6 | 缺呼吸/摇摆随机化、眨眼时序、组合规则 | 等做出耳朵/尾巴独立层时一起加，别现在造抽象 |
| 视线仍是旧 remap | 与 `rig.json` 方案不一致 | 将来在 `gaze_frame` 一处换 renderer，眨眼/头发调用点不用动 |

---

## 10. 改代码的推荐路径

| 想改什么 | 先看 | 再改 | 验证 |
|---|---|---|---|
| 半眼/惊讶外观 | `expr_state._lid_shutter / _iris_zoom` | 参数 `half_drop/half_sag` | `pytest tests -q` |
| 加新表情 | `expr_state.STATES` + build | `effect_table.json` 加一行 | 右键「表情 → 试一个」 |
| 调待机频率 | `effect_table.json` 的 `idle_ms` | — | `pytest -k director` |
| 加系统动作 | `action_bridge` 白名单 | 用户 `actions.json` | 右键菜单 / 双击 |
| 改物理 | `ui_app._step_gravity / _build_table` | — | 画布开小桌子；注意固定世界约定 |
| 改视线 | `anim.gaze_frame` + `pet_window._update_gaze` | 只换渲染，不动眨眼/头发 | 目检 + `pytest` |

---

## 11. 交接时的一句话总结

**素材是数据、渲染是通道、行为是编排。** 三层之间只通过 `PetAsset`（素材）与 `ExprState`（表情通道）通信；
新功能优先“组合已有通道”，不要新增渲染分支，更不要重写 `anim.py` 这个大渲染函数。
