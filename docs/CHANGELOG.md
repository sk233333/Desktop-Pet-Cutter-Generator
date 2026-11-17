# CHANGELOG

按时间倒序。只记会影响观感或数据格式的行为变更；内部重构不列。

## 能力补全向导（首期）

### 行为变更

- **新增六步「能力补全向导」**（`upgrade_wizard.py`，主界面唯一入口）：
  选 SKU → 体检 → 挑补全器 → 并排预览 → 落盘 → 报告。纯本地、不联网。
- **三个补全器**：`mask.from_ellipse`（椭圆 socket，已有 mask 跳过、逐只点选）、
  `mask.domain_hint`（只读候选框）、`mouth.open`（独立覆盖层 PNG）。
- **体检面板**：把 Issue #1/#2、25° 未标定、`bias` 未暴露、`smile` 缺失
  列成可见清单；`smile` 标 `v0.1.1` 且不可勾选，不自动关 Issue。
- **落盘闸门**：逐候选 `commit` 只返回 `ChangeSet`，由 `upgrade_core` 统一
  过 `validate_manifest`、原子写、失败整体回滚；路径锁死在当前 SKU 目录。
- **`allowed` 只产提案**：`derive_allowed()` 未实现前不写运行时词表，
  提案不含 `smile` / `blink`。

### 数据格式

- v1 manifest 首次落盘时走 `manifest_io.v1_to_v2` 同一迁移入口，转 v2 后再
  应用补丁；不另写转换。
- `items[]` 可带可选 `mouth_dir` / `mouth_level`；**当前为数据槽，运行时未消费**
  （`anim.build_expr_bank()` 未传 `mouth_dir`，接线需显式放行，排 v0.1.1）；
  `ExprBank.STATES` 与 `smile → half` 别名不变。

### 测试与状态

- `pytest -q` = **108 passed**（pre-wizard 基线 93，以 `pytest -q` 当前结果为准）。
- 三档状态：**已实现** = 六步向导 + 三个补全器；**未接线** = `mouth_dir` 运行时消费；
  **待拍板** = `smile` 独立状态、`derive_allowed()`、配置期生成式候选（AI 范围页）。
- Step 1 的「smile 缺失」只是体检项，标 `v0.1.1`，**不代表本轮能补**。

## 摆动收敛（逐区域幅度 / 方向 / bias）

### 行为变更

- **编辑器「头发 / 衣服」合并为「摆动」页**：同一张列表（类型 / 区域 / 摆动轴 /
  幅度 / 末端），每行可单独调幅度（0~3.0×）与摆动轴。
- **「方向」改称「摆动轴（往返轴）」**：它定义的是来回摆的轴，不是「往哪边飘」；
  运行时九宫格同步改称「整体漂移方向」。
- **轴与丝长夹角 < 25° 时自动取垂直分量**：避免沿丝长方向出现拉长 / 压缩；
  列表行内以 `⊥` 提示（保存的 `dir` 仍是用户所选值）。
- **新增 `bias`（角度偏置，度）**：`angle = A·sin(ωt) + bias`，走同一个 angle
  通道，`swing_all` 未改；`bias=0` 时逐帧结果与旧实现一致（有 golden 测试）。
- **自定义轴恢复内摆保护**：向身体内侧的目标摆幅仍为外摆的 60%；外缘临透明的
  发丝保持原例外。
- **像素位移上限合并**：逐区域 3× 叠运行时 3× 不再到 9×，单块区域发梢位移
  上限 14px（`MAX_SWAY_SHIFT_PX`）。
- **调参落盘拆分**：拖幅度 / 改方向只写 JSON（`masks_dirty=False`），不重写
  也不删除 mask PNG；增删区域仍走完整保存。

### 数据格式

- sidecar（`<stem>_hair.json` / `<stem>_cloth.json`）与 manifest 的
  `hair[]` / `cloth[]` 逐项支持 `amp`（0~3，缺省 1.0，等于 1.0 时省略）、
  `dir`（0~360，`null` = 沿法线，缺省省略）、`bias`（-45~45，0 时省略）。
- `manifest.schema.json` 新增 `$defs.swayWisp`，`hair` / `cloth` 共用；
  `manifest_io.validate_manifest` 同步校验，口径常量 `SWAY_DEFAULTS` /
  `SWAY_RANGES` 与 schema 由一致性测试钉住。
- 运行时配置 `~/.config/desktop_pet/motion.json` 改为按 SKU：
  `{"skus": {"<sku>": {"scale": 1.0, "dir": null}}}`；旧扁平格式读取时降级、
  不自动写回，用户下次修改才按新格式保存，且保留其他 SKU。

### 已知边界（显式接受，不是待修 bug）

| 事实 | 原因 / 处理 |
| --- | --- |
| `dir` 是往返轴，不是单向偏置 | 由正弦驱动；要单向飘需另加 bias，已保留字段 |
| 方向只有 8 档，无逐区域风力 / 相位 | 未实现；当前共用 `wind_speed / amp / stiffness / damping` |
| `bias` 已支持但 UI 不暴露 | 先落数据与渲染，避免再做一轮 UI 改动 |
| 结构性修改（增删区域 / 改眼眶 / 时间轴）仍整体重建引擎 | 纯调幅度 / 方向已走 `apply_params` 就地更新 |
| `swing_all` 单帧 512² 5.8ms / 1024² 18.1ms | 口径：同机同图、3 块区域、7 次中位数、T-S1 之后；内核未改，拖动流畅来自 `apply_params` 就地更新 + 80ms 预览合并，不是内核提速。大图预览仍有轻微掉帧风险，未做降采样 |
| 运行时逐区域参数与右键「整体漂移方向」互不读取 | 两个概念，文案已区分 |

摆动是 `arbiter` 之外**显式接受的例外**：它是 30fps 连续物理量 + 逐实例交互，
不塞进 `resolve()`（离散事件状态机）。状态归属如下，任何一处不得反向覆盖：

| 状态 | 唯一写者 | 读方 |
| --- | --- | --- |
| 编辑器 `_cut["hair"]` / `["cloth"]` | `_amp_cell` / `_set_sway_dir` / `_annotate_sway` | 预览、sidecar |
| `<stem>_hair.json` / `_cloth.json` | `save_hair_polys()`（调参只写 JSON） | `load_hair_sidecar` |
| manifest `hair` / `cloth` 数组 | `map_hair_wisps()` | `hair_from_dicts` |
| `asset.motion`（共享对象） | 构建期一次 | 运行时只读（逐实例倍率不写回） |
| `motion.json` 的 `skus.<sku>` | `_apply_motion_scale` / `_set_motion_dir` | 当前窗口 |

> 逐区域 `amp` / `dir` / `bias` 的唯一真源是 **manifest 的 `hair` / `cloth` 数组**；
> 编辑器态、sidecar、`asset.motion`、`motion.json` 都是它的上下游投影，
> 任何一处不得反向覆盖。
>
> 例外说明：`motion.json` 是**用户调参覆盖层**（只存整体漂移 `scale` / `dir`），
> 读时覆盖 manifest / `asset.motion` 的默认值，仅在用户主动调参时写，
> **manifest 不被反写**；两者都有值时运行时以 `motion.json` 为准。

## 收敛轮（单一状态源 / 仲裁 / manifest v2）

### 行为变更

- **眨眼默认观感保持不变**：`half:false`（默认）前半段仍是 `make_blink_frames`
  的 0.55 遮挡过渡帧，后半段 cover=1.0 全闭。0.55 帧是**遮挡式**（把上眼睑
  压下来的部分填成肤色），眼球几何零形变，不是压缩式。
- **`half:true` 是新增可选**：只在时间轴 item 显式写 `"half": true` 时，前半段
  改用 `ExprBank.apply(base, "half")`（按真实 socket 遮挡、睑缘复用 lash 层）。
- **全闭眼统一**：运行时时间轴眨眼与 idle 随机眨眼都取 `make_blink_frames`
  的 `frames[-1]`，不再出现两套全闭实现。
- **表情 / 视线 / 气泡统一由 `arbiter` 仲裁**：`timeline(3) > interaction(2) >
  idle(1)`；blink 通道只允许时间轴写；交互后 3 秒内 idle 效果不打断。
- **多眼素材**：`active_eye_parts` 过滤后，`mask=null` 的眼不参与眨眼/视线/表情
  （之前会跟着闭眼）。过滤只改「哪些索引参与」，不改索引本身。

### 数据格式

- manifest 支持 `schema_version: 2`：`parts[]`（`id / eye_index / group /
  eye_level / mask / channel`）与 `timeline.items[]`（`group / t / hold / half`）。
- `eyes / eye_level / eyes_mask / blink` 在 v2 下是**派生视图**，由 parts/timeline
  反推；旧渲染路径不用改，值永远与 parts 一致。
- 组号统一为字符串 `g{n}`；读取层只归一化一次，下游不再判类型。
- 眼球通道：`file_idx = i // 2`，`channel = R / G`（B 恒为高光）。
- 迁移工具：`tools/migrate_manifest_v1_v2.py`（`--check` 纯只读）。

### 已知差异（显式接受，非缺陷，C5b 已关闭）

- 右键「单眼眨一下」的 `wink_l/wink_r` 仍走 `ExprBank` 单眼贴片；它与整帧
  `frames[-1]` 的全闭观感不完全一致（单眼手势本来就不等于全闭）。
- 编辑器预览的 blink 仍用 `ExprBank` 的 blink 贴片，运行时用 `frames[-1]`。
- 单眼素材（如 9991）的 `wink_l/wink_r` 会退化成同一边，需用逐 SKU
  `effects.enabled` 关掉对应效果。

### 测试与 CI 范围

- `pytest tests` 覆盖纯逻辑 / manifest / 几何层（arbiter、manifest_io、anim 的
  纯函数），不依赖 PyQt6。
- 窗口层（帧循环、QPixmap 转换、真实 tick）**不在 pytest 里**，需要本地
  `QT_QPA_PLATFORM=offscreen` 跑真实素材抽查；CI 全绿不代表全链路已验。
- CI：`.github/workflows/tests.yml`（numpy + opencv-headless + pytest）。

### 数据待办

- `3b3d…`（6 只检测眼，其中 2 只在脚下挂件上）与 `d37e…`（4 只，其中 2 只在
  龙头挂件上）：先用标注工具点清真眼再补 mask；补之前 active=0，不眨眼/不跟视线。
  迁移 report 会打 `[pending 需补 mask]`。
- 三张新补 mask 的素材（`3c1e…` / `9991…` / `da90…_preview_pet`）用的是由
  `eyes` 椭圆生成的 socket（rx+1, ry+1），偏宽松；待抽查「往窄侧移到上限时
  虹膜不压到上睑线」，不通过就逐 SKU 降 amp。
- `da90…_preview_pet` 的坐标基于 509×508 重裁版；换回 3.3MB 原图时必须整套
  重标 eyes + mask。
