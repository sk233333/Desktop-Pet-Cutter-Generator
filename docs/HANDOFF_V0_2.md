# HANDOFF v0.2：可交付集与分轮执行表

> 本文件是 v0.2 的仓库内真源。执行顺序 **W0 → W8**，中间不逐轮等确认；
> 只在 §7 红线处停下。测试数与 hash 以 `pytest -q` / `git log` 当前结果为准，
> 本文不写死。文档头「不产代码」保持。

## 0. 范围与定性

### 0.1 v0.2 范围

| 进 v0.2 | 不进 v0.2（登记，不是欠账） |
|---|---|
| 已实现六项的收口与端到端验证 | A 档真素材人眼（Issue #1/#2、25° 标定、chevron 自绘） |
| 向导 AI 抠图验证（local / remote 分层） | 已否决 7 项 |
| 文本引导切割（建议 1） | C-1（不触发不动） |
| AI 自动标注（建议 2） | rembg 真实模型下载受限（环境限制，fake/stub 验证协议，不阻塞手动） |
| 更多表情 / 动作 + B-4 说话驱动切档（建议 3） | |
| 切割质量增强（建议 4） | |
| 独立 Agent + 按 SKU memory（建议 5 的落地） | |
| 对话 / 语音 + 工程增强（建议 5/6） | |

### 0.2 定性

- v0.2 是 **全绿 + 一次性 push 之后**才打的 annotated tag。
- `v0.1` 是 annotated tag 对象 `fe5d76f` → commit `b03cdb7`；不移动、不重打。
- 40M clone、零权重不破。

### 0.3 当前第一步

工作树已有 feat / test / docs 变更 → W0 本地拆 commit（满足 Go/No-Go ⑤），
不 push。这是「等 AI 功能加好再一次性推」的既定口径。

## 1. W0 三处收口（零功能）

### ① Go/No-Go ③ 定性

- W0~W5 标 **N/A（Agent 未接线）**，不补临时测试、不阻塞其余四项。
- W6 由 `test_agent_*` 闭环：Agent 进程被 kill → 桌宠不黑、回 idle。
- 判据仍写死在 §6-③。

### ② AI 切割落盘分层

- 源图：`launch.save_cut_source()`，手动 / AI 共用，只写
  `<stem>.png` + `<stem>_mask.png`；不写 manifest / 覆盖层 / 最终透明素材。
  这是**新建期**的初始 mask；调参期 `test_c5_bias_meta_only_keeps_masks_untouched`
  的「字节 / mtime 不变」是另一条口径，两者不冲突。
- 候选产物：manifest patch / 覆盖层仍走 `upgrade_core.commit_changes()`，
  不绕过 `validate_manifest` / 原子写 / 回滚。
- 断言：`test_ai_cut_source_path_is_shared_and_commit_free`
  （字节一致、只两个文件、源码不含 `commit_changes` / `manifest.json`）。
- 结论：**源图走 `_save_source`、候选产物走 `commit_changes`，两层不是
  第二条状态源。**

### ③ 覆盖层键不是别名

- `smile → joy` / `half → sleepy` 是 `PetWindow._expr_frame()` 的渲染层取图
  键：有 `<stem>_joy.png` / `<stem>_sleepy.png` 才叠。
- 不是 `ExprBank.apply()` 的字符串别名；不重建别名表。
- `expr_state.py` 自 `7531ff3` 零字节（哈希 `c14c92c…` 为 v0.2 全程基线）。
- `derive_allowed()` 仍只出七态；`smile` 不因 joy 覆盖层被吞掉。
- 「七态不再增加」= 保持 `7531ff3` 的七态字节，**不是退回六态**。
- 断言：`test_overlay_keys_do_not_rebuild_alias`（词表逐项一致、
  `apply(..., state)` 而非 `overlay_key`）。

## 2. 统一红线（每轮）

- `expr_state.py` 自 `7531ff3` 零字节（含 STATES / 别名表）。
  v0.2 之后保护基线改写为 `d21ac36`，v0.2.2 实现期用 `55ca8fe`（逐字节核对），
  v0.2.2 结案后下一批起改用 `9981be8`，哈希值不变。
- `anim.py / arbiter.py / manifest_io.py / manifest.schema.json /
  effect_table.json / .github/workflows` 零 diff（基线随版本前移）。
- 保护文件 7 项哈希；tag `v0.1` 不动。
- 零权重：`.pt/.pth/.ckpt/.safetensors/.onnx`、`desktop_pet/models/` 全 ignore；
  40M clone 不破。
- `docs/images/` 唯一未跟踪。
- 默认零网络；密钥 0600 + 环境变量优先、不入库。
- 写盘单一路径（源图例外见 §1-②）。
- 默认零勾选 + 整体否决优先。
- 批量只出 report，不批量 commit。
- `chat.enabled` / 生成开关 / Agent 开关三者独立。

已否决 7 项不做、不捡回：`partial_text` 二次绘制跳过 · `arbiter` 加
`"agent": 2` · 首期同步 `PetPreview` · 权重回仓 · 预防性改 workflow ·
批量补 socket · P1-8 修订号纳入 `_pet_key`。

## 3. 统一验收口径（每轮）

```bash
python3 -m pytest -q
python3 -m pytest --collect-only -q | grep -E 'test_aig_|test_agent_|test_ai_'
# 注意：匹配串含 test_ai_*；70 不是 25 的增长量，不拿差值当新增断言数
python3 -m py_compile desktop_pet/*.py
# 干净 venv 五包 + MPLBACKEND=Agg + import PyQt6 → ModuleNotFoundError
QT_QPA_PLATFORM=offscreen python3 desktop_pet/ui_app.py
git diff --name-only 9981be8..HEAD -- <保护文件 7 项>   # 期望空
git status --porcelain                                # 期望仅 ?? docs/images/
git rev-parse --short origin/main                     # push 前期望 9981be8
```

- 数字以实测为准；commit message 写实际值，文档写「以 pytest -q / git log
  当前结果为准」。
- 差值与 skip 数按每轮实测定性，不沿用旧口径。
- 真实 API / 本地模型不进 CI，一律 fake provider + monkeypatch。
- 每轮本地多 commit：feat / test / docs 分开；都不 push。

## 4. 分轮执行表

| 轮 | 做什么 | 必带断言 / 验收 | 停线条件 |
|---|---|---|---|
| W0 | 三处收口 + 3 个本地 commit（feat/test/docs）+ 本文件；不 push | ①②③ 结论与断言节点；工作树满足 ⑤ | 发现切割连 manifest patch 也自己写 → 先列冲突 |
| W1 | 向导 AI 抠图端到端：local(rembg) / remote(/ai/candidates) 两条入口分层 | 默认 local/手动、零网络；remote 显式同意 + 只发原图 + 日志无 URL/路径/原图名/key；fake 走完候选→点选→ChangeSet→validate→读回 | remote 与本地切割合成同一上传开关 → 停 |
| W2 | 文本引导切割：文本 → 框 + 掩膜候选；手动画框/补点兜底保留、默认手动 | 文本进请求隐私口径同步；返回候选不自动落盘；权重用户自备 + ignore；真实素材评测（IoU/F1、软 alpha、socket 转化率、返工分钟） | 打包权重 / 改 `manifest_io` / schema → 停 |
| W3 | AI 自动标注：眼眶/嘴/发缕/摆动分区 → manifest 草案 → 逐项确认 | 四段式 propose + ChangeSet + validate；不自动判真眼；不自动写 STATES；草案默认零勾选 | 自动写状态表 / 批量 commit → 停 |
| W4 | 更多表情/动作：`angry/sad/shy` 封闭候选；B-4 说话驱动嘴型切档单列 | 任意状态名 → normal；不注册 STATES；覆盖层键/别名断言；整帧重绘只写边界 | 动 STATES/别名表 → 单独边界文档 + 显式授权 |
| W5 | 切割质量：alpha matting、阴影/白边识别、打分与自动重切、批量文件夹 | 打分不覆盖人工选择；重切可否决；批量只出 report、逐 SKU 点选后落盘；黑边断开回退口径不回退 | 批量 commit → 停（已否决） |
| W6 | 独立 Agent + 按 SKU memory | Agent=触发源不是状态源，统一 `arbiter.request("interaction", ...)`；不塞 Qt 主线程；kill → 回 idle；memory 不走 `commit_changes`、tmp + `os.replace`、显式触发词、结构化、sku 守卫、warn 去重、不喂 `derive_allowed` | 加 `"agent": 2` / 第二状态源 / 权重回仓 → 停 |
| W7 | 对话语音 + 工程增强 | agent.json → 情绪 → 表情；TTS/ASR 独立轮、默认关、不进 CI 五包；路由 `key_env` 占位、零 host；流式/重试默认关且「已吐字不重试」；费用只打类别 | 默认联网 / 静默轮询 / LLM 生成 Python 进 L3 → 停 |
| W8 | Go/No-Go → 一次性 push → v0.2 | §6 五项全绿；`git push origin main` 唯一一次；再打 annotated `v0.2` | 任一不绿 → 不 push |

并行不占实现位：A 档人工（真素材人眼）。W2/W3/W5 的质量评测依赖它；
素材未到则用现有素材 + 标注「未标定」，功能以默认关实验开关交付。

## 5. 文档同步（每轮 docs commit 带）

- `README.md`：长期记忆「方案已定、尚未接线」随 W6 改准；清旧测试数 /
  bias / mouth_dir 旧快照。
- `CHANGELOG.md` / `HANDOFF_V011.md`：分项行 + 底部总览同步。
- `HANDOFF_AI_MOTION.md` §8：硬约束与 Go/No-Go。
- `HANDOFF_AI_GENERATE.md`：向导 AI 抠图在 W1 前保持「未端到端验证」。
- 本文件 `docs/HANDOFF_V0_2.md`：总表真源。
- 三档写清：已实现 / 已接线 / 未接线（待拍板）；不写死测试数与 hash。

## 6. W8 出口：Go / No-Go

1. 本地 `pytest -q` + 干净 venv 五包 `MPLBACKEND=Agg` 全绿，
   `--collect-only -q` 核得到全部新节点，skip 仍 1。
2. fake 端到端：候选 → 点选 → ChangeSet → `validate_manifest` → 读回
   （含 v1→v2 字段不丢）。
3. Agent / 模型不可用 → 桌宠回 idle 且不黑；失败矩阵（缺 key / 断网 /
   429 / 乱码 / 尺寸不符 / 全透明 / 路径越界 / 写盘失败 / 用户否决）
   无半份 SKU、原子回滚。
4. `git diff --name-only 7531ff3..HEAD -- <保护 7 项>` 为空；无权重、
   无新增 workflow 依赖；tag `v0.1` 不动。
5. 提交完成后 `git status --porcelain` 仅 `?? docs/images/`；
   `origin/main` 仍 `7531ff3`。

全绿后：

```bash
git log --oneline 7531ff3..HEAD
git push origin main          # 唯一一次
# 再打 annotated tag v0.2，不重打 v0.1
```

## 7. 什么时候停

只有读码要求动 `expr_state.py` / STATES / 别名表 / `anim.py` /
`arbiter.py` / `manifest_io.py` / schema / workflow / tag `v0.1`，或要改
生产行为语义、把权重请回仓、把已否决项捡回来时 → 停下先列冲突。

其余与清单不一致：按仓库事实就地定，回执列偏离，不中断等待。
需要真素材人眼、外部模型下载受限、或要产品拍板 → 登记默认关 / N/A，
不阻塞 v0.2 其余部分。

## 8. 状态

| 项 | 状态 |
|---|---|
| R2 `7531ff3` / B-2 结案 / tag `v0.1` = `fe5d76f` → `b03cdb7` | ✅ |
| 三处硬冲突已修（恢复字节 / 封闭覆盖层键 / 默认零勾选 + ignore + 去快照） | ✅ |
| 补 1~补 6 并入 `HANDOFF_AI_MOTION.md` §8 | ✅ |
| W0 三处收口 + 3 个本地 commit | ✅ |
| W1 向导 AI 抠图 fake 端到端 | ✅（真实 rembg 下载受限，登记环境限制） |
| W2 文本引导切割（API 实验入口） | ❌ 已删除（2026-11-20）：主界面 AI 切割 / 文本引导 / 贴内层黑边一并移除，`ai_cut_box.py` 与 `test_ai_cut_box.py` 删除；手动框选 + 补点保留 |
| W2 上传通道 | 主界面不再有切割上传通道；W1 remote 仍用 `ai_generate.json` 的 `upload_consent` |
| W3 AI 自动标注（只读报告） | ✅ `ai_annotate.py` + 「AI 标注报告…」按钮；不写 manifest、不改 schema、不自动判真眼 |
| W4 更多表情 / 动作 | ✅ 封闭覆盖层键扩到 joy / sleepy / angry / sad / shy；基态绑定 smile→(joy,shy)、half→(sleepy,sad)、surprise→angry；STATES / 词表不变；B-4 嘴型与整帧重绘单列子轮 |
| W5 切割质检 | ✅ `cut_quality.py` 只读打分（白边/阴影/软边/覆盖率/毛刺）+ 主界面「切割质检…」+ `--batch-report`；不自动重切 |
| W6 memory + 独立 Agent | ✅ `memory_store.py`（显式触发、原子写、0600、sku 守卫、不喂词表）+ `agent_bridge.py`（JSONL/stdio，触发源不是状态源） |
| W7 对话语音 | ⚠ TTS 接线、ASR 仅函数；`mood_hint` 接覆盖层取图；ASR UI 与说话驱动嘴型留独立轮 |
| N1（v0.2.1）B-4 + ASR UI | ✅ 同族三档预计算切帧（不每帧 apply）+ ASR 后台线程与菜单入口 + TTS 非阻塞；单档 SKU 行为不变 |
| N2（v0.2.2）本地 MCP | ✅ 方向无关 stdio 窄协议，五个只读/propose 工具；notify 只写 arbiter；默认关、无网络 socket |
| Ⅳ 档边界文档（R1） | ✅ `docs/HANDOFF_V0_3.md`；七项全部未授权、零代码、零行为变更 |
| W8 Go/No-Go → push → v0.2 | ⬜ 本地验证已跑；push / tag 等人工核查后再执行 |
| A 档人工 / C-1 / 已否决 7 项 | 并行 / 不动 / 不做 |

### 8.1 W0~W2 收口实测

- 本地 `pytest -q` 与干净 venv（`MPLBACKEND=Agg`）全绿；skip 仍为 1 个
  GUI 模块；具体数字以命令当前结果为准。
- 保护文件 7 项自 `7531ff3` 零 diff；`expr_state.py` 哈希 `c14c92c…`。
- 工作树仅 `?? docs/images/`；`origin/main` 仍 `7531ff3`；未 push、未打 v0.2。
- Go/No-Go：①②④⑤ 通过；③ 按 W0 口径标 **N/A（Agent 未接线）**，
  由 W6 的 `test_agent_*` 闭环，**不是当前阻塞项**。当前不能 push 的原因是
  W3 红线未解 + W4~W8 未做，不是 ③。
- W3 按选 2 落地：`ai_annotate.py` 只产 `AnnotationReport`，坐标同时给
  归一化与像素值；不引用 `manifest_io` / `commit_changes`，不写文件；
  眼部候选超过两个只写 warning，不自动判真眼。UI 入口「AI 标注报告…」，
  上传前二次确认，报告只读。
- W4 停线：`angry/sad/shy` 不能注册 STATES（`expr_state.py` 保护），
  把它们映射到已有状态（如 angry→surprise、sad→half、shy→smile）会改变
  生产行为语义；B-4 说话驱动嘴型还需要嘴型切档的产品口径。按 §7 停下，
  待拍板后再做，不擅自定映射。
- W4 口径已定并落地：`angry→surprise`、`sad→half`、`shy→smile` 只做
  渲染层取图；同基态多张按固定顺序（joy 先于 shy、sleepy 先于 sad），
  `mood_hint` 命中且有素材时优先。`angry/sad` 由 adapter 补眉毛，
  `STATES` / `expr_state.py` / 别名表零改动；B-4 与整帧重绘未做。
- W5 质检只读：`score_cut()` / `batch_report()` 不写素材、不覆盖人工选择；
  主界面按钮与 `--batch-report` 共用同一套打分。
- W6/W7 已接线：memory 只由显式触发词写入，注入 system prompt 但不进
  `derive_allowed()`；Agent 走 `agent_stdio` 子进程，输出仍统一走
  `arbiter.request("interaction", ...)`；TTS 接在回复气泡后、默认关，
  ASR 只有函数与配置，UI 入口留独立轮。
- W2 的本地 GroundingDINO/SAM2 权重路线未接线；主界面 AI 切割与文本引导
  已于 2026-11-20 按用户决定删除，向导 AI 抠图与 AI 标注报告保留。

### 8.2 W8 证据（push 前）

**25 个 commit 分轮**（`git log --oneline 7531ff3..HEAD`）：

- 前置 AI 生成（7）：`e7c97d3` `f4f3ec8` `34910f7` `68fc39b` `76cb965`
  `6c254cb` `e369c5e`
- W0~W2（7）：`2283758` `c33e50a` `1cb0579` `fbda3b9` `5714a63`
  `33a200e` `dd72c24`
- W3 只读报告（3）：`9459350`（feat）+ `4eea11f`（test）+ `9670b3f`（docs）
- 清理与日志（3）：`5b2d49f` `20240b2` `8b2fc07`
- W4~W7（3）：`deb9b9b`（feat）+ `96596a3`（test）+ `e91ec2e`（docs）
- W8 证据（2）：`dc32cc4`（test）+ 本 docs commit

**五项 Go**：

1. 本地 `pytest -q` 288 passed；干净 venv 五包 282 passed, 1 skipped
   （skip 仍是 `test_pet_window_mouth.py` 的 QApplication 用例）。
2. fake 端到端节点：`test_aig_end_to_end_fake_candidate_to_manifest`、
   `test_aig_selected_candidates_validate_and_readback`、
   `test_aig_commit_is_changeset_only`。
3. Agent 不可用：`test_agent_failure_keeps_pet_idle_and_not_black`、
   `test_agent_rejects_bad_output_and_timeout`、
   `test_agent_disabled_makes_no_process`；失败只清 `_chat_streaming`，
   不写 `arbiter.request`、不动 `_render_rgba` / `self.base`。
4. 保护 7 项自 `7531ff3` 零 diff；无权重；`test_aig_no_weights_no_new_deps`。
5. 工作树仅 `?? docs/images/`；`origin/main` 仍 `7531ff3`。

**失败矩阵节点**：缺 key `test_aig_missing_key_fails_before_network` /
`test_missing_key_blocks_before_network`；断网 / 429 / 5xx / 超时
`test_http_provider_network_failures` 与
`test_aig_failure_matrix_returns_to_manual[429|500|timeout]`；乱码
`[garbage]` + `test_parse_json_content_accepts_fence_and_rejects_garbage`；
尺寸不符 `test_load_size_mismatch_returns_none`；全透明
`test_load_all_transparent_returns_none`；路径越界
`test_aig_path_guard_rejects_parent_absolute_manifest` +
`test_memory_sku_guard_blocks_path_escape`；写盘失败
`test_write_failure_rolls_back` + `test_rollback_on_write_failure` +
`test_aig_failure_leaves_no_half_sku`；用户否决
`test_veto_and_empty_changeset_leave_bytes_equal`。

**三处口径**：memory 在 `~/.config/desktop_pet/memory/`，不走
`commit_changes()`、不写 assets；`voice_io.py` 只 import 标准库
（`shlex` / `shutil` / `subprocess`），默认关；`mood_hint` 只接受
`MOOD_KEYS`，未知值由 `clamp()` 丢弃，记忆注入 system prompt
但 emotion 仍由七态 `clamp()` 管住、不进 `derive_allowed()`。

新节点 19 个（`test_agent_*` 13 + `test_cut_quality` 5 +
`test_new_overlay_keys_render_without_new_states` 1）。

### 8.3 N1（v0.2.1）：B-4 + ASR UI

- **保护基线**：v0.2 之后以 `d21ac36` 为基线；保护 7 项继续按内容哈希核对，
  `expr_state.py` 哈希值不动（仍是 `c14c92c…` 的同一字节）。
- **B-4 落点**：`desktop_pet/mouth_runtime.py`（Qt-free）按
  `<stem>_mouth_<closed|smile|o>.png` 约定展开同族档位；`MouthOverlay.load`
  只新增 `quiet_missing`（缺档不 warn，路径/尺寸守卫不放松）。
  `PetWindow.__init__` 每档在素材分辨率合成一次，`_rebuild()` 烘焙显示帧；
  `_pick_frame()` 只在说话窗口内换预计算帧，**没有任何 `apply()`**。
  说话窗口 = 流式 chunk（每次 +0.8s）、`done` 后按文本长度延长、
  TTS worker 运行期间兜住；暂停时冻结。
- **ASR UI**：右键「语音输入…」→ `QFileDialog` → `AsrWorker`（QThread）→
  `voice_io.transcribe` → `_chat_send`。`voice.enabled` 未开或命令不可用时
  置灰。TTS 的同步 `speak()` 改为 `SpeakWorker`，不再阻塞 GUI。
- **边界未变**：不新增 STATES / 别名 / schema；`arbiter` 通道矩阵不动；
  零新 Python 依赖；ASR/TTS 默认关，CI 五包仍只 skip GUI 文件。
- **下一批未并项**：整帧重绘与中间帧、A 档真素材标定、C-1、已否决 7 项，
  继续按原口径单列。

### 8.4 N2（v0.2.2）：本地 MCP 收窄版

- **入口**：`pet_window.py --mcp-stdio`，默认关；stdout 只走协议，
  print / warn 改 stderr。
- **协议**：`mcp_stdio.py` 方向无关 JSONL（`tools/list` / `tools/call` /
  `notify`）；64KB 上限、畸形 JSON、未知工具/方法都回错误，不崩。
- **适配**：`mcp_office.OfficeHost` 只读 manifest 白名单字段 + 候选 +
  notify；`notify` 注入桌宠侧回调，最终只走
  `arbiter.request("interaction", "bubble"/"expr", ...)`；无 commit /
  写盘 / exec / socket / 新依赖。
- **确认**：点一下气泡 = 知道了，仅本地收起，不回传外部（Q4a）。
- **基线**：保护 7 项自 `55ca8fe` 零 diff；`v0.1` / `v0.2` / `v0.2.1` 未动。

