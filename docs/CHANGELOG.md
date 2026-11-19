# CHANGELOG

按时间倒序。只记会影响观感或数据格式的行为变更；内部重构不列。

## AI 生成动作参数 / 表情（本地未 push）

### 行为变更

- 主界面齿轮旁新增 **AI** 圆形按钮：「模型接口」弹窗填写请求地址 / 模型 ID /
  API 密钥，可点「测试连接」发一条极短请求；保存写入本地 `ai_motion.json`。
- 主页「AI 切割（框手动）」：框必须人画，SAM/GrabCut 只做框内像素；不再让
  视觉模型猜框，图片也不上传。模型框太小/为空导致的失败随之消失。
- 主界面「贴内层黑边」默认勾选；AI 切割开始前恢复「要切成什么？」提示，
  文字只用于提示框选和预览标题，不上传、不参与分割。
- SAM 返回 <0.5% 的异常小掩膜时自动整图重估，仍无效才提示换手动/补点。
- 新增「文本引导（会上传图片）」实验入口：默认关，勾选即同意上传；
  文本 → 模型出框 → 本地切割，失败自动回手动；日志不打原图名。
- 向导 AI 抠图补 fake 端到端（候选 → ChangeSet → validate → 读回）；
  真实 rembg 模型下载受限，登记为环境限制。
- W3 AI 自动标注按红线暂停：眼眶/嘴缺少 manifest 字段，需动 schema 或
  自动判真眼；未 push、未打 v0.2。
- LongCat 的思考开关按官方参数发 `thinking={"type":"disabled"}`（不再发布尔值），
  视觉框实测从 49s 降到 2~3s；其它兼容服务保留 4096 tokens / 90s 兜底。
- 只拿到 `reasoning_content` 时提示「增大 max_tokens 或换非推理模型」。
- 设置「动作参数」组新增「AI 生成参数…」：用独立 `ai_motion.json` 调
  OpenAI 兼容 `chat/completions`，一次返回九项 `items[].anim`、逐区域
  `hair[] / cloth[]` 的 `amp / dir / bias`，以及 `joy / sleepy` 两个表情覆盖层。
- AI 只给几何参数，不发明状态名；joy / sleepy 是封闭白名单，写入
  `items[].expr_overlays`，运行时由 PetWindow 读 `<stem>_joy.png` /
  `<stem>_sleepy.png` 合成，缺文件回退原帧。
- 默认零网络：只有点按钮才发请求；缺 `base_url` / `model` / 密钥
  （弹窗保存或环境变量 `DESKTOP_PET_AI_MOTION_KEY`）时在发请求前直接失败，
  参数不动。
- 返回的 JSON 逐项夹取（anim 九项 / SWAY_RANGES / smile_geometry 参数区间）；
  弹窗逐项勾选，「当前 → 建议」可见；未勾选字段逐字节保持原值。
- 任何失败（网络 / 429 / 5xx / 非 JSON / 结构不对 / 写盘失败）都整体回手动，
  不产生半份参数。

### 数据格式

- `ExprBank.STATES` 七态 → 九态（`joy`、`sleepy` 追加在末尾，不挪旧下标）；
  ExprBank 不为这两个状态烘焙贴片，`apply()` 无 patch 时原样返回。
- manifest 仅新增可选字段 `items[].expr_overlays`
  （`{状态: {overlay, source, params}}`），不升 schema；
  `validate_manifest` 不拒绝未知 item 字段。
- 覆盖层 PNG 与 manifest patch 都作为 `ChangeSet.files` /
  `ChangeSet.manifest_patch` 交给 `upgrade_core.commit_changes()`；
  不新增第二条写盘路径，不改 base / mask，失败整体回滚。

### 测试

- 测试数与 hash 以 `pytest -q` / `git log` 当前结果为准，本文不写死。
- `test_ai_motion.py` 用 fake provider / 注入 opener，CI 永不连真实 API。
- `joy / sleepy` 不注册 `expr_state.STATES`，只做覆盖层键：运行时
  `smile → joy`、`half → sleepy`；默认勾选为零，逐项确认后才写入。
- 保护文件自 `7531ff3` 零 diff（含 `expr_state.py`）；tag `v0.1` 不移动；
  无权重、无新依赖。硬约束与 Go/No-Go 见 `HANDOFF_AI_MOTION.md` §8。

## AI 生成候选入口（B-3 首期，本地未 push）

### 行为变更

- 主页「图片」组新增「抠图方式：手动框选（默认）/ 本地 AI 抠图」；AI 只
  替换「框取 + 抠图」，落盘仍是同一份 `self._cut`，后续眼睛 / 摆动 / 生成
  完全共用。
- 新建 SKU 增加「手动切割（默认）/ AI 生成」二选一入口（`new_sku_wizard`）。
  「AI 一键」收窄为**一次请求出全部候选 + 逐项人工确认**，不等于自动写盘。
- AI 只出 soft alpha / 区域框 / 置信度 / warnings / 出处类别；不做表情贴片、
  不注册 STATES、不端到端自动成品。
- 默认零网络：`ai_generate.json` 不存在或 `enabled=false` 时不发请求；
  远端模式上传默认关，显式勾选后才发送原图字节（不发路径 / key / 日志）。
- `transport=local` 用 `LocalMattingProvider`（rembg，惰性导入）在进程内推理，
  不读 key、不上传；推理在 QThread，UI 不冻结。rembg 为可选本地依赖，
  不进 CI、不进仓库；模型文件由 rembg 放到 `~/.u2net`。
- 失败（缺 key / 超时 / 429 / 5xx / 非 JSON / 依赖缺失 / 候选全无效 /
  写盘失败）一律整体回手动，不留半份 SKU，不产生临时目录。

### 数据格式

- 新 SKU 的 base 由 soft alpha × 原图生成，只作为 `ChangeSet.files` 交给
  `upgrade_core.commit_changes()`；已有 base 拒绝覆盖。
- manifest 仅新增可选字段 `items[].ai_provenance`
  （provider 类别 / confidence / warnings / 是否上传），不升 schema。
- 预览 PNG 同为 `ChangeSet.files`；不写 mask PNG。
- `upgrade_core.commit_changes()` 新增唯一行为：manifest 不存在且调用方给出
  内存 v2 骨架时以其为起点；写盘仍只有这一条路径，失败清理本次新建空目录。

### 测试

- 本地 `pytest -q`：**234 passed**；干净 venv 五包：**228 passed, 1 skipped**
  （唯一 skip 仍是 GUI 文件，未新增 GUI 测试）。
- `test_aig_*` 25 个节点，fake provider / 注入 opener；真实 API 不进 CI。
- 保护文件自 `7531ff3` 零 diff；tag `v0.1` 不移动；无权重、无新依赖。

## B-2 smile 独立状态（R2，已接线）

### 行为变更

- `ExprBank.STATES` 六态 → 七态（`smile` 放末尾，不挪旧下标）；删除
  `'smile': 'half'` 别名，`from_agent("smile")` 原样返回 `smile`。
- 运行时词表 `derive_allowed()` 含 `smile`；`clamp()` 放行，不再归一为 `normal`。
- 渲染：PetWindow 读 `items[].smile_geometry.overlay` 合成；**不烘焙 ExprBank
  贴片**，`apply("smile")` 无 patch 时原样返回（回退原帧，不是半眼）。
- R2-A 中间态用 `_UNWIRED_STATES` 显式排除 `smile`；R2-B 接线同 commit 删除，
  不存在「STATES 已含、词表悄悄含」的提交。
- 素材已有 `mouth_dir` 时，B-1 生成侧不画嘴角弧（`mouth_arc: null`），避免双嘴。

### 数据 / 兼容

- `smile_geometry.runtime_consumer` 由 `null` 改为 `"pet_window"`；`mouth_arc`
  可为 `null`，沿用可选字段先例，不升 schema，`validate_manifest` 放行。
- `ExprBank.save()` 图集从六行变七行（`smile` 空 tile）；仓库内无调用方
  （读码结论），外部图集需重生成，加载侧不自动兼容。
- `anim.py:2049` docstring 仍写「6 个」；为守 `anim.py` 零字节不改，注释旧、
  行为不变。
- 向导 `allowed` 提案继续排除 `smile`（proposal-only），与运行时词表不是同一张表。

### 测试

- 本地 `pytest -q`：**209 passed**；干净 venv 五包：**203 passed, 1 skipped**
  （唯一 skip 仍是 `tests/test_pet_window_mouth.py`，未新增 GUI 文件）。
- 保护文件仅 `expr_state.py` 按授权变化；`anim.py` / `arbiter.py` / workflow /
  `manifest_io.py` / schema / `effect_table.json` 零字节。

## B-1 配置期微笑几何数据槽（B-2 后已接线）

### 行为变更

- 向导新增第四个补全器 `expr.smile_geometry`：零 AI、纯几何，输出「眼睛轻眯 /
  嘴角弧 / 腮红 alpha」三组参数与一张独立覆盖层 PNG。
- 只产配置期数据，**不注册 `smile` 状态**：不改 `ExprBank.STATES`、不改
  `smile → half` 别名表、不进 `derive_allowed()` / 向导 `allowed` 提案；
  词表输出与之前逐项相同。
- 不复刻 `mouth.open` 的三档微笑嘴贴片；与已有 `mouth_dir` 不互相写入。

### 数据格式

- 新增可选数据槽 `items[].smile_geometry = {schema, preset, overlay,
  runtime_consumer, params, eyes[], mouth_arc, blush[]}`；`overlay` 指向
  `<stem>_smile.png`（独立 RGBA 覆盖层）。
- B-1 交付时 `runtime_consumer: null`；B-2 R2 已改为 `"pet_window"`。
  `preset` 只允许 `light / medium / strong`，几何数值按画布尺寸裁剪。
- `manifest_io.py` / `manifest.schema.json` 未改：沿用 `mouth_dir` 的先例，
  可选字段不升 schema，`validate_manifest` 原样放行。

### 测试与状态

- Qt-free 断言在 `tests/test_smile_geometry.py`；`pytest -q` 以实际输出为准。
- 三档状态：**已实现** = 几何生成器 + 并排预览 + 人工点选 + 独立覆盖层落盘；
  **已接线** = PetWindow 的 `smile` 分支（B-2 R2 完成）；**待拍板** = 无。

## 嘴型 overlay 运行时（T-M3 ~ T-M5）

### 行为变更

- `items[].mouth_dir` 指向的 PNG 在桌面运行时**静态常驻**：素材分辨率合成一次，
  `base / blink / expr / gaze` 四路全部从合成后的源帧派生；无 `mouth_dir` 时完全不合成。
- 合成只读：不改 `asset.rgba`、不写 manifest、不改 mask PNG；失败（尺寸不符 / 损坏 /
  缺失 / 全透明）一律按无 overlay 处理，基础帧零变化。
- 编辑器 `PetPreview` 仍是独立取帧管线，**首期不同步**，按「显式接受差异」处理。
- 边界与断言见 `docs/HANDOFF_MOUTH_OVERLAY.md`。

### 测试与状态

- 集成断言（C-M1 四路 golden、C-M3a~d 四类失败、C-M5 200 次 apply 零写盘、
  C-M6 保护模块、C-M7 状态表/签名、C-M8 双 SKU）在 Qt-free
  `tests/test_mouth_runtime_ci.py`，CI 五包会真跑；仅需 QApplication 的 200 帧
  真实帧循环留在 `tests/test_pet_window_mouth.py`，CI 五包只 skip 这一个文件。
- 本地 `pytest -q` 以实际输出为准。
- 已实现：静态常驻运行时合成；未做：说话驱动切档、编辑器 `PetPreview` 同步。

## AI 对话接入（A1，默认关闭）

### 行为变更

- **右键「说句话…」**：流式气泡 + 只写 `expr`；`action` 走 `ActionBridge.dispatch`
  既有白名单与确认流程。
- `chat.enabled=false` 时**零网络、不创建 worker**；密钥只从 `key_env` 环境变量读，
  不落盘、不进日志。
- `expr` 与最终气泡走既有 `interaction` 源（矩阵无 `agent` 源）；流式窗口只屏蔽
  `idle→bubble`，`expr/gaze/body` 照常；`arbiter.py` 字节不变。
- 词表启动时反推一次：只取 `expr_state.STATES`、排除 `blink` / `smile`、按逐 SKU
  `effects.enabled` 裁剪；**改配置需重启窗口生效**。
- 无 key / 断网 / 429 / 超时 / 乱码 → 静默回 idle，不弹错误文本。

### 已知增补（原交接文档外）

- `partial_text()`：流式 JSON 只把 `text` 字段的已到达部分冒进气泡，避免先闪
  `{"text":...}`。抽不到 `text` 时：JSON 头部未到 → 返回空串（气泡显示 `…`）；
  非 JSON 纯文本 → 回退为可见原文尾部。最终仍由 `safe_parse` 判定；
  非 JSON 回复会**先短暂显示这段原文尾部，随后回 idle**（不是完全无显示）。
- system prompt 显式包含小写 `json` 字样，作为 `json_mode=object` 的稳妥条件
  （只给 `response_format` 不给指令时，部分实现会失败或返回空）。

### 数据格式

- 新增 `desktop_pet/agent.json.example`；真实 `agent.json`、`*.local.json`、`.env`
  均被 ignore，只提交 example。
- 不写 manifest、不改 manifest v2；`mouth_dir` 此时仅为数据槽，运行时消费由后续
  嘴型 overlay 落地。

### 测试与状态

- A1 交付时为 **133 passed**（pre-A1 基线 108，历史值；以 `pytest -q` 为准）。
- 已实现：A1 对话（默认关闭）；`mouth_dir` 的运行时消费在后续嘴型 overlay 落地；
  待 v0.1.1：生成式候选、心跳、滚动摘要、mood_hint。

## 能力补全向导（首期）

### 行为变更

- **新增六步「能力补全向导」**（`upgrade_wizard.py`，主界面唯一入口）：
  选 SKU → 体检 → 挑补全器 → 并排预览 → 落盘 → 报告。纯本地、不联网。
- **首期三个补全器**：`mask.from_ellipse`（椭圆 socket，已有 mask 跳过、逐只点选）、
  `mask.domain_hint`（只读候选框）、`mouth.open`（独立覆盖层 PNG）；
  第四个 `expr.smile_geometry` 见上方 B-1 节。
- **体检面板**：把 Issue #1/#2、25° 未标定、`smile` 独立状态列成可见清单；
  `smile` 标 `v0.1.1` 且不可勾选，不自动关 Issue。`bias` 行已在 C-5 改为
  「已暴露」。
- **落盘闸门**：逐候选 `commit` 只返回 `ChangeSet`，由 `upgrade_core` 统一
  过 `validate_manifest`、原子写、失败整体回滚；路径锁死在当前 SKU 目录。
- **`allowed` 只产提案**：运行时词表由 `derive_allowed()` 从 `expr_state.STATES` +
  `ActionBridge.registry` + `effects.enabled` 反推；向导提案不写词表，且不含
  `smile` / `blink`。

### 数据格式

- v1 manifest 首次落盘时走 `manifest_io.v1_to_v2` 同一迁移入口，转 v2 后再
  应用补丁；不另写转换。
- `items[]` 可带可选 `mouth_dir` / `mouth_level`；运行时由 `PetWindow` 在
  素材分辨率合成（见「嘴型 overlay 运行时」节），`anim.build_expr_bank()` 不参与；
  `ExprBank.STATES` 与 `smile → half` 别名不变。

### 测试与状态

- 向导交付时为 **108 passed**（pre-wizard 基线 93，历史值；以 `pytest -q` 为准）。
- 三档状态：**已实现** = 六步向导 + 首期三个补全器；**已接线** = `mouth_dir` 静态常驻；
  **待拍板** = `smile` 独立状态、配置期生成式候选（AI 范围页）。
  `derive_allowed()` 已在 A1 对话里落地，向导提案不写词表。
- Step 1 的「smile 独立状态」只是体检项，标 `v0.1.1`，**不代表状态表能动**；
  B-1 补的是几何数据槽（见上节），不是 `smile` 状态。

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
| `bias` 已支持（C-5 已暴露到「摆动」页行内控件） | 旧句「UI 不暴露」已作废；上下限与 `SWAY_RANGES` 同源 |
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
