# HANDOFF v0.3：Ⅳ 档边界与授权清单

> 本文件是 R1 的唯一产物：**只文档、零代码、零行为变更**。
> 七项都**未授权**；R2 需要逐项或整批显式授权后才允许动代码。
> 基线：`06008c9`（v0.2.2 之后的并批推结案点）。
> 保护 7 项自 `06008c9` 零 diff；`expr_state.py` 哈希 `c14c92c…` 不变。
> 测试数 / hash 以 `pytest -q` / `git log` 当前结果为准，本文不写死。

## 0. 读法

每节固定回答五件事：

1. **结论落到哪一层**——产物最终写到哪里、谁有权改状态；
2. **授权后允许动**——文件清单；保护文件是否同 commit 重订哈希；
3. **必答项**——授权前必须由人拍板、不能用「应该会」糊过去的问题；
4. **断言清单**——实现时必须能跑到的节点名（R2 落地时按此命名）；
5. **三档状态**——已实现 / 已接线 / 未接线，三者不合并。

统一红线（每项都适用）：不动 `v0.1`~`v0.2.2` 四个 tag；不引权重回仓；
默认零网络；不新增第二状态源；AI / 外部 Agent 只做候选源或触发源；
输出统一翻 `arbiter.request("interaction", ...)`；不写 manifest、不碰
`_render_rgba` / `self.base`；已否决 7 项不捡回。

---

## 1. ① W3 选 1：自动标注草案可写（动 manifest_io / schema）

### 结论落到哪一层

- 草案只落到 manifest item 的**可选只读字段** `annotation_draft`；
- **不直接写 `parts` / `hair` / `cloth` / `sway`**；人工逐项确认后，仍走
  `completers.base.ChangeSet` → `upgrade_core.commit_changes()`
  （`upgrade_core.py:242`）这一条写盘路径；
- 草案默认零勾选；未确认的草案对渲染零影响。

### 现状（源码事实）

- `ai_annotate.py:107-163`：只产 `AnnotationReport`，不引用 `manifest_io` /
  `upgrade_core`，不落文件；超过两只眼只写 warning（`ai_annotate.py:138-140`）；
- `manifest_io.py:39-58` 读入并归一化；`manifest_io.py:157-236` 手写校验；
  `SCHEMA_VERSION = 2`（`manifest_io.py:18`）；
- `manifest.schema.json` 的 `item` 只列基础字段（`manifest.schema.json:98-147`），
  `expr_overlays` / `mouth_level` / `smile_geometry` 目前靠
  「JSON Schema 允许 additionalProperties + 手写校验忽略未知键」存在；
- `upgrade_core.py:269-285` 已统一走 v1→v2 迁移 → patch → validate → 原子写。

### 授权后允许动

`desktop_pet/manifest_io.py`、`desktop_pet/manifest.schema.json`、
`desktop_pet/ai_annotate.py`、`desktop_pet/ui_app.py`、`tests/test_ai_annotate.py`、
`tests/test_manifest_*`、docs。

**保护文件两项（`manifest_io.py` / `manifest.schema.json`）必须在同一个
授权 commit 内重订哈希**，否则 R2 的保护 7 项检查必红。

### 必答项

- v1→v2 迁移是否保持不变：`annotation_draft` 缺失时零迁移差异；
- `read_manifest()` 对未知字段是否原样保留（当前是），草案读回不能丢；
- `validate_manifest()` 是否只校验草案形状，不校验「眼是否真眼」；
- 外部旧产物（无草案字段）是否完全兼容；
- 草案字段是否只读：任何自动流程不得把草案直接展开成 `parts`。

### 断言清单（R2 命名）

- `test_v03_draft_roundtrip_v1_v2`：v1 读入、v2 读回、无草案时不增字段；
- `test_v03_draft_never_auto_applies`：草案存在时 `parts` 字节不变；
- `test_v03_validate_rejects_bad_draft`：越界 / 非数字 / 缺字段拒绝；
- `test_v03_draft_zero_selected_by_default`：UI 默认零勾选；
- `test_v03_commit_keeps_single_write_path`：仍只经 `commit_changes()`。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 只读报告、坐标夹取、warning、UI 入口 |
| 已接线 | 「AI 标注报告…」按钮 → 后台线程 → 只读弹窗 |
| 未接线 | 草案持久化、逐项确认后写回 |

### 停线

自动判真眼、自动写 `parts` / STATES、批量 commit → 停。

---

## 2. ② 整帧重绘 + 中间帧

### 结论落到哪一层

- 产物落到 SKU 目录的覆盖层 PNG + manifest item 的可选
  `expr_frames`（每状态 3~5 帧，最多 8 帧）；
- 只在**渲染层**按已预计算的帧切索引；不是状态源，不新增 STATES；
- 复用现有覆盖层加载器（尺寸守卫 / alpha bbox），不另造一套贴图管线。

### 现状（源码事实）

- 七态图集由 `ExprBank.build()`（`expr_state.py:231`）生成、`save()`
  （`expr_state.py:321`）落盘；运行时 `apply()`（`expr_state.py:339`）只做
  覆盖层合成；
- 五键覆盖层在 `pet_window.py:86-91` 绑定；`_expr_frame()` 惰性重采样并缓存
  （`pet_window.py:698-720`）；加载器在 `pet_window.py:979-1003`；
- `MouthOverlay.load()` 已做尺寸 / 全透明 / 越界守卫
  （`mouth_overlay.py:92-181`），且 bbox 外 alpha 逐比特不变；
- 当前没有中间帧 / 整帧重绘；B-4 只在三档嘴型之间切。

### 授权后允许动

新增 `desktop_pet/frame_redraw.py`；`desktop_pet/pet_window.py`（只加加载与
索引）；`desktop_pet/manifest_io.py` + `manifest.schema.json`（可选
`expr_frames`，若决定进 schema）；`tests/test_frame_redraw.py`；docs。

**不动** `anim.py` / `arbiter.py` / `expr_state.py`；不混 PachiPakuGen（CUDA /
RIFE）或 Anime2.5DRig（标准脸比例 / 3D 素材链）的技术栈。

### 必答项

- 帧数与字节预算：建议每帧 ≤ 1.5MB、单 SKU 新增 ≤ 8MB、单状态 ≤ 5 帧；
  当前 `desktop_pet/assets` 约 4.4MB、`.git` 约 28MB，必须保住 40M clone；
- 与 B-4 嘴型的优先级：说话窗口内嘴型优先，表情帧暂停；窗口结束回静态档；
- 帧切换是否只在 `_pick_frame()` 内做索引，**禁止每帧 `.apply(`**；
- 缺帧 / 尺寸不符 / 全透明是否回退静态覆盖层或原帧。

### 断言清单

- `test_v03_frames_precomputed_once`：初始化后 `_pick_frame()` 内 `.apply(` 为 0；
- `test_v03_frame_alpha_unchanged_outside_bbox`：bbox 外逐比特不变；
- `test_v03_frame_budget`：单帧 / 单 SKU 字节上限；
- `test_v03_frame_fallback_static`：缺帧回静态，不崩、不半眼；
- `test_v03_frame_no_state_change`：`STATES` / 词表逐项不变。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 七态图集、五键覆盖层、B-4 三档嘴型 |
| 已接线 | `_expr_frame()` 缓存与 `_pick_frame()` 切图 |
| 未接线 | 整帧重绘、中间帧、帧预算守卫 |

### 停线

动 STATES / 别名表 / `anim.py` 物理 → 停，转 ⑦ 授权。

---

## 3. ③ L2 自主循环 Agent

### 结论落到哪一层

- 循环只产出 **ChangeSet / 报告**；**不 commit、不写 manifest、不碰
  `arbiter`**；
- 真正落点仍由桌宠侧把候选交给 `arbiter.request("interaction", ...)`，
  或交给人工点选后走 `commit_changes()`；
- 不新增 `"agent": 2`。

### 现状（源码事实）

- `agent_bridge.respond()`（`agent_bridge.py:80-98`）是**一次性** JSONL 请求，
  有 `timeout_ms`，没有 `max_steps` / 循环；
- `chat_provider.stream_chat()` 的 `agent_stdio` 分支
  （`chat_provider.py:84-92`）把 Agent 输出当一行 JSON 交回 worker；
- 回复唯一落点：`pet_window._chat_on_done()`（`pet_window.py:1427-1435`）
  统一 `arbiter.request("interaction", "expr"/"bubble", ...)`；
- 失败路径已有：`test_agent_failure_keeps_pet_idle_and_not_black`
  （`tests/test_agent_memory.py:120`）。

### 授权后允许动

新增 `desktop_pet/agent_core.py`；`desktop_pet/agent_bridge.py`、
`desktop_pet/chat_provider.py`、`desktop_pet/agent.json.example`、
`tests/test_agent_*`、docs。

**不 import Qt、不 import arbiter、不写盘**；循环放在现有 worker 线程，
不塞 Qt 主线程。

### 必答项

- 工具白名单：只读 + propose（建议与 MCP 五个同集）；
- `max_steps`（建议默认 4）、`timeout_ms`（默认 8000）、总超时硬停；
- 终止条件写死在代码：步数 / 超时 / Agent 返回空 / 无新信息；
- 失败回退：kill 子进程 → 回 idle → 不黑屏；
- 输出只允许 `{text, emotion, action, mood_hint, remember}`，未知字段丢弃。

### 断言清单

- `test_v03_agent_max_steps`：超过步数硬停，不多发一次；
- `test_v03_agent_timeout_kills_child`：超时 kill，回 idle；
- `test_v03_agent_change_set_not_committed`：只回候选，manifest 字节不变；
- `test_v03_agent_no_qt_no_arbiter`：源码 / import 层双重断言；
- `test_v03_agent_unknown_fields_dropped`。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 一次性 JSONL、超时、失败回 idle |
| 已接线 | `agent_stdio` profile → worker → arbiter |
| 未接线 | 多步循环、白名单工具调用、硬停计数 |

### 停线

加 `"agent": 2`、第二状态源、直写 manifest → 停。

---

## 4. ④ 多 Agent 讨论 / 协作解题

### 结论落到哪一层（头号必答）

**只能是候选 / 校验门，不能是状态源。**

- 配置期 fan-out：每个 profile 各自出报告，聚合器只回
  `{reports, conflicts}`；不产生任何状态写入；
- 辩论当校验门：多份结论不一致时标 `conflict` 交人，不自动合并；
- 停止规则写在代码（`max_agents` / `max_rounds` / `timeout_ms`），
  不能是模型「觉得同意了」。

### 现状（源码事实）

- 现在只有单 provider 选择：`profiles` 按 `provider` 取一个
  （`chat_provider.py:80-83`）；`agent.json.example:14-21` 只有 profile 表；
- 没有 mailbox / 共享任务表 / 消息总线 / 运行时多 Agent 循环。

### 授权后允许动

新增 `desktop_pet/multi_agent.py`（纯配置期）、`agent.json.example`、
`tests/test_multi_agent.py`、docs。

**不加 mailbox / 共享任务表 / 消息总线；不进运行时；不 import
`pet_window` / `arbiter`。**

### 必答项

- 形态二选一：配置期 fan-out，或辩论当校验门；不能两者混成运行时；
- 结论最大集合：候选 / 报告 / conflict；不得是状态名或 manifest patch；
- 停止规则：`max_agents ≤ 3`、`max_rounds ≤ 2`、总超时；
- 冲突处置：逐条列出，人工点选，不自动裁决。

### 断言清单

- `test_v03_fanout_reports_only`：输出只有 reports / conflicts；
- `test_v03_conflict_requires_human`：不一致不自动合并；
- `test_v03_no_runtime_bus`：源码无 mailbox / queue / thread；
- `test_v03_multi_agent_no_state_write`：manifest 字节 + mtime 不变。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 单 Agent、单 profile |
| 已接线 | `agent_stdio` / HTTP profile 选择 |
| 未接线 | fan-out、辩论校验门、冲突报告 |

### 停线

运行时多 Agent、共享状态、自动裁决 → 停。

---

## 5. ⑤ 公网 / 云 API、自动执行办公动作

### 结论落到哪一层

- 云 provider 只回**候选 / notify**；确认结果第一版仍不回传；
- 不自动执行办公动作；办公事件只落气泡 + 可选表情；
- 外部通知唯一落点仍是 `arbiter.request("interaction", ...)`。

### 现状（源码事实）

- 远端 AI 候选已有硬闸门：`enabled` / `upload_consent` / `base_url` /
  `model` / `key_env` / `max_image_bytes`
  （`ai_generate_contract.py:39-48`、`ai_generate_provider.py:63-76`）；
- 上传体只含图片字节与 sku/stem，不含本地路径
  （`ai_generate_provider.py:82-96`）；
- MCP 只做本地 stdio：`mcp_stdio.py:11-13` 明确无 socket、不监听；
  `notify` 只到 `OfficeHost`（`mcp_office.py:132-145`）；
- 没有公网常驻、没有 0.0.0.0 监听、没有自动办公执行。

### 授权后允许动

新增 `desktop_pet/cloud_office.py`（或扩 `ai_generate_provider.py`）、
配置示例、`tests/test_cloud_office.py`、docs。

**零新 Python 依赖**（标准库 `urllib`）；不引云 SDK。

### 必答项

- 默认关：`enabled=false`、`upload_consent=false` 时零网络、零子进程；
- 绑定：只允许用户填写的 HTTPS base_url；不监听 `0.0.0.0`，不做本地
  HTTP server；
- 日志：不打 URL / 路径 / key / 原图名；只打类别与状态码；
- 办公动作：只播报，不执行；确认结果不回传；
- 失败矩阵：缺 key / 断网 / 429 / 乱码 / 超时 → 回 idle，不写半份 SKU。

### 断言清单

- `test_v03_cloud_default_off_no_network`：默认两种关闭下零请求；
- `test_v03_cloud_consent_gate`：未同意不发送原图；
- `test_v03_cloud_logs_redacted`：日志无 URL / 路径 / key；
- `test_v03_no_listen_socket`：`/proc/net/tcp*` 无监听条目；
- `test_v03_office_notify_only`：manifest 字节不变。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 远端候选 + 本地 rembg；上传同意与 key_env 闸门 |
| 已接线 | 向导 AI 抠图（remote / local） |
| 未接线 | 公网办公事件、云 API 常驻、自动执行 |

### 停线

默认联网、常驻监听、自动执行办公动作、确认回传 → 停。

---

## 6. ⑥ 真实 GroundingDINO / SAM2 权重路线

### 结论落到哪一层

- 权重与推理都在**本机适配层**；产物是 mask / 候选；
- 人工框选 + 补点兜底保留；权重不进仓库、不进 CI；
- 文本提示只进请求，不写日志。

### 现状（源码事实）

- SAM2 走本地权重：`launch.py:84-85` 指向
  `Punctuation_selection_of_images/sam2.1_hiera_small.pt`；
  `load_sam()`（`launch.py:458-468`）惰性 import `sam2`；
- 权重缺失 / 加载失败回退 GrabCut（`launch.py:631-650`）；
- 本地 rembg 惰性导入（`ai_generate_provider.py:152`），模型在用户缓存
  `~/.u2net`（`ai_generate_provider.py:127-131`）；
- `.gitignore:8-13` 已 ignore `.pt/.pth/.ckpt/.safetensors/.onnx` 与
  `desktop_pet/models/`；
- **文本引导切割入口当前不存在**：`desktop_pet/ai_cut_box.py` 已删除，
  `HANDOFF_V0_2.md` §8 也记为「W2 文本引导切割已删除」；现在只有
  图片框 / 点的 SAM2 与 GrabCut。

### 授权后允许动

新增 `desktop_pet/weights.py`（用户路径解析）、`launch.py`（只在文本提示
适配处）、`ai_generate_provider.py`、配置示例、`tests/test_weights.py`、docs。

**不打包权重、不自动下载、不改 40M clone 预算。**

### 必答项

- 权重路径由用户配置 / 环境变量给出；缺失时明确报错并回退 GrabCut；
- 缓存目录 ignore（`.u2net`、`models/`、`weights/`）；
- CUDA 前提登记为环境项，不进 CI 五包；
- 文本进请求的隐私口径与 W1 一致：显式同意、只发必要字段、日志脱敏；
- 手动画框 + 补点兜底不回退。

### 断言清单

- `test_v03_weights_not_in_repo`：仓库无权重 blob，clone 体积守住；
- `test_v03_missing_weight_falls_back`：缺权重 → GrabCut，不崩；
- `test_v03_text_prompt_not_logged`：日志无原始提示 / 路径；
- `test_v03_no_auto_download`：未显式配置时不发下载请求。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | SAM2 图片提示、GrabCut 回退、rembg 本地分割 |
| 已接线 | 手动框选 / 补点 / 向导入口 |
| 未接线 | GroundingDINO 文本提示、权重路径配置、CUDA 登记 |

### 停线

权重回仓、自动下载、40M clone 破裂 → 停。

---

## 7. ⑦ 新增状态名 / 动别名表

### 结论落到哪一层

- 这一项是**唯一允许改状态源**的例外；必须单独显式授权；
- 影响面不是「加一个名字」，而是 STATES、图集、词表、取帧、存盘五处
  同时变；任何一处漏改都会出现「生成了却看不见」或「词表有、图没有」。

### 现状（源码事实，逐处行号）

| 位置 | 现状 |
|---|---|
| `expr_state.py:17` | `STATES` 七态：normal / blink / half / surprise / wink_l / wink_r / smile |
| `expr_state.py:231` | `ExprBank.build()` 按 STATES 构图集 |
| `expr_state.py:321-336` | `save()` 按 STATES 写图集与 meta |
| `expr_state.py:339-356` | `apply(frame, state)` 未知状态原样返回 |
| `expr_state.py:382-392` | `ExprState.set()` 未知状态退回 `normal` |
| `expr_state.py:403-408` | `from_agent()` 仍有一张语义 alias（shy/angry/sad→half、happy→surprise） |
| `chat_contract.py:188-225` | `derive_allowed()` 只从真实 STATES 反推，排除 blink / normal |
| `pet_window.py:86-91` | 渲染层覆盖层绑定：smile→(joy,shy)、half→(sleepy,sad)、surprise→angry |
| `pet_window.py:673-687` | `_overlay_key_for()` 只改取图，不改状态名 |
| `pet_window.py:698-720` | `_expr_frame()` 四路取帧（覆盖层 / smile / ExprBank / 原帧） |
| `pet_window.py:979-1003` | `_load_expr_overlays()` 加载封闭覆盖层键 |

**当前已知缺口（必须如实写）**：生成侧五键齐全
（`ai_motion_contract.py:49`、`ai_motion_adapter.py:195-208`），但运行时
`_load_expr_overlays()` 的循环仍是 `("joy", "sleepy")`
（`pet_window.py:994`）。因此 `angry / sad / shy` 目前**能生成、能写入
manifest，但运行时不会加载**；`smile` / `half` / `surprise` 仍走原贴片。
这不是 ⑦ 的授权项，而是 W4 的运行时缺口，登记进 R3 的一行 feat 修复，
不在 R1 夹带。

### 授权后允许动

`desktop_pet/expr_state.py`、`desktop_pet/anim.py`、
`desktop_pet/pet_window.py`、`desktop_pet/chat_contract.py`、
`desktop_pet/manifest_io.py`、`manifest.schema.json`、tests、docs。

**保护文件哈希在同一个授权 commit 内重订**；否则保护 7 项必红。

### 必答项

- 新状态名是否真需要进 STATES（而不是继续用封闭覆盖层键）；
- `ExprBank.build()` / `save()` / `apply()` / `derive_allowed()` /
  `_OVERLAY_FOR_STATE` / `_expr_frame()` 六处是否同时更新；
- 旧 manifest / 旧图集是否仍能读（缺新状态回退 normal）；
- 失效回退：新状态图缺失 → 原帧，不半眼、不崩；
- `smile→joy` 一类只允许留在渲染层，**不得写成 `apply()` 的字符串别名**；
- 词表是否仍由真实产物反推，不能手填。

### 断言清单

- `test_v03_new_state_requires_all_tables`：六处缺一即失败；
- `test_v03_unknown_state_normal`：未知状态回 normal；
- `test_v03_overlay_keys_stay_out_of_states`：joy/sleepy/angry/sad/shy
  不进 STATES / 词表；
- `test_v03_atlas_roundtrip`：save → load → apply 一致；
- `test_v03_legacy_manifest_without_new_state`：旧产物可读。

### 三档状态

| 档 | 内容 |
|---|---|
| 已实现 | 七态、图集、alias 回退、覆盖层取图 |
| 已接线 | 五键生成 → manifest；运行时只加载 joy / sleepy |
| 未接线 | 新状态名、别名表改动、angry/sad/shy 运行时加载 |

### 停线

未授权就动 STATES / `expr_state.py` / 别名表 → 停；这是 B-2 同款红线。

---

## 8. 授权与执行

### R2 授权模板（逐项或整批）

```text
授权项：① / ② / ③ / ④ / ⑤ / ⑥ / ⑦（可多选）
允许动：<上面该节列出的文件>
保护文件重订：<哪几项，在同一 commit>
tag：v0.3（annotated），v0.1~v0.2.2 不动
```

### 顺序建议

1. ① 与 ② 都碰 manifest，放同一批，避免两次 schema 迁移；
2. ③ 与 ④ 都属 Agent 边界，先 ③ 再做 ④，④ 不依赖 ③ 的运行时；
3. ⑤ 与 ⑥ 都属外部 / 权重，默认关，可并行；
4. ⑦ 最后单独一批，必须单独授权，不和前六项混。

### R2 验收

- 数字该变、skip 仍 1、`--collect-only -q` 核得到新节点；
- 保护 7 项自 `06008c9` 零 diff（授权范围除外，且同 commit 重订哈希）；
- 五项 Go ③ 保持；
- 失败矩阵无半份 SKU、原子回滚；
- 纯 docs 与 feat 分开 commit，一次 push；
- 全绿后按 M2 打 annotated `v0.3`。

### R2 停线

撞 `expr_state.py` / STATES / 别名表 / `anim.py` / `arbiter.py` /
`manifest_io.py` / schema / workflow / tag；权重回仓；默认联网；
第二状态源；已否决 7 项被捡回 → 停下列冲突。

---

## 9. 当前事实与差异登记

1. **W4 运行时缺口**：五键生成 / 写 manifest 已通，运行时只加载
   joy / sleepy（`pet_window.py:994`）；angry / sad / shy 需一行 feat 修复，
   归 R3，不混进 R1。
2. **W2 文本引导切割**：当前仓库已删除 `ai_cut_box.py`，主界面无文本入口
   （`HANDOFF_V0_2.md` §8 同口径）；对外不要再写「文本引导切割已交付」。
3. **CHANGELOG 旧表述**：v0.2.2 段「没有 socket」在本批 docs 订正为
   「没有网络 socket、不监听端口」；Qt 内部未命名 Unix socketpair 不计。
4. **本文件不授权任何实现**：七项全部未接线；R2 需显式授权。
