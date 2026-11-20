# AI 动作参数 / 表情 · 交接（本地开发，未 push）

> 起点：`7531ff3`（origin/main 冻结）。落盘仍只走
> `upgrade_core.commit_changes()`。CI 永不连真实 API。

## 1. 目标

在「动作参数」页给已生成的桌宠加一个 **AI 生成参数…** 入口：

```
ai_motion.json → chat/completions（严格 JSON）
→ {anim: 九项, sway: {hair:[...], cloth:[...]},
   expressions: {joy:{...}, sleepy:{...}}}
→ 本地夹取 → 「当前 → 建议」逐项勾选 → ChangeSet
→ commit_changes() 原子写 / 失败整体回滚
```

AI 只给**几何参数**，不自由发明状态名。表情首期只开放 `joy` / `sleepy`
两个封闭覆盖层候选；它们不注册 STATES，分别挂到 `smile` / `half`，
缺覆盖层时运行时回退原帧。

## 2. 配置（独立真源）

- 模板：`desktop_pet/ai_motion.json.example`
- 本地真配置：`desktop_pet/ai_motion.json`（已加入 `.gitignore`，
  保存时权限收紧到 `0600`）
- 字段：`base_url` / `model` / `key_env` / `api_key` / `timeout_ms` /
  `json_mode` / `thinking` / `temperature` / `max_tokens`
- 入口：主界面齿轮旁的 **AI** 按钮 →「模型接口」弹窗；可填地址 / 模型 ID /
  密钥并「测试连接」（后台线程发一条极短请求，不冻结界面）。
- 密钥优先级：环境变量 `key_env` > `api_key`；环境变量便于 CI / 临时覆盖。
  密钥只存本地 gitignored 文件，不打日志、不下发。
- 默认零网络：只有点按钮才构造请求；缺任一前置项直接失败，参数不动。
- 封闭覆盖层键（2026-11-20 扩到五键）：`joy / sleepy / angry / sad / shy`
  只决定渲染取图；基态绑定 `smile→(joy,shy)`、`half→(sleepy,sad)`、
  `surprise→angry`。`surprise→angry` 是刻意美术口径：惊讶的圆眼复用
  angry 眉毛，不为「怒」单开状态名。
- 主界面已不提供 AI 切割入口（2026-11-20 移除）：图片组只保留「手动框选」，
  框内像素由 SAM/GrabCut 完成；视觉模型不再参与主界面抠图，图片不上传。
- `ai_cut_box.py`、`_AICutPromptDialog`、`_ai_cut_flow`、`ck_outline`、
  `ck_text_guide` 与 `_cut_flow(auto_seg=True)` 一并删除；向导 AI 抠图与
  「AI 标注报告…」保留。

## 3. 文件分工

| 文件 | 职责 |
|---|---|
| `ai_motion_contract.py` | 配置闸门、请求/提案 dataclass、九项 anim 范围、sway 夹取、严格 JSON、prompt |
| `ai_motion_provider.py` | `FakeMotionProvider`（测试）/ `HttpMotionProvider`（urllib，无重试） |
| `ai_motion_adapter.py` | 提案 → ChangeSet；覆盖层 PNG + `items[].expr_overlays`；不写盘 |
| `expr_state.py` | 零字节保护（`7531ff3`）；不新增 STATES |
| `pet_window.py` | `_load_expr_overlays()` + `_expr_frame()`：`smile→(joy,shy)` / `half→(sleepy,sad)` / `surprise→angry`；缺文件回退 |
| `ui_app.py` | 齿轮旁 AI 模型接口按钮 / 弹窗、动作参数页入口、后台 QThread、勾选弹窗、写后同步画布、切割质检 |
| `memory_store.py` | 按 SKU 长期记忆：显式触发、原子写、0600、sku 守卫 |
| `agent_bridge.py` | 独立 Agent 的 JSONL/stdio 窄协议；触发源，不做状态源 |
| `voice_io.py` | 可选 TTS / ASR 外部命令；默认关、无新依赖 |
| `cut_quality.py` | 只读切割质检：白边 / 阴影 / 软边 / 覆盖率 / 毛刺 |

## 4. 数据格式（不升 schema）

- `items[].expr_overlays = {"joy": {"overlay": "<stem>_joy.png",
  "source": "ai_motion", "params": {...}}, ...}`
- 与 smile 同一套覆盖层加载规则：路径守卫 / 尺寸 / 损坏 / 全透明都返回
  `None`，不把异常带进帧循环。
- `anim` / `hair` / `cloth` 采用**整块 patch**（先与当前值合并再写），
  避免逐下标 pointer 在缺容器时失败。
- 未勾选的字段逐字节保持原值。

## 5. 失败矩阵（全部整体回手动）

缺 `base_url` / `model` / 密钥（弹窗或环境变量）；HTTP 非 2xx（含 429/5xx）；超时 /
URLError；响应非 JSON / 缺 `choices` / 缺 `message.content`；模型输出不是
JSON 对象；勾选项为空；覆盖层渲染为空（缺眼睛标注）；写盘失败（原子写
回滚，连同新 PNG 一起恢复）。

## 6. 测试

- `tests/test_ai_motion.py`（Qt-free）：配置模板无密钥、缺 key 前置失败、
  JSON 围栏与乱码、夹取与补齐、注入 opener 的 HTTP 成功/失败矩阵、
  逐项勾选、dry-run、写盘失败回滚、保护文件自 `7531ff3` 零 diff
  （含 `expr_state.py`）。
- 真实 API 不进 CI；`FakeMotionProvider` 覆盖正常路径。
- 测试数与 hash 以 `pytest -q` / `git log` 当前结果为准，本文不写死。

## 7. 已知边界

- `joy` / `sleepy` 的覆盖层是 `smile_geometry` 的线稿几何（眼睑线 / 嘴角弧 /
  腮红），不是整帧重绘；观感仍以人工勾选为准。
- `sleepy` 不画嘴角弧；`joy` 在素材已有 `mouth_dir` 时也不画嘴角弧，避免双嘴。
- 逐区域 sway 写入 manifest 后，同时回写源 `<stem>_hair/cloth.json`，
  保证下次「保存素材」不会丢参数。
- `joy` / `sleepy` **不注册** `expr_state.STATES`，也不进 `derive_allowed()`；
  运行时只挂到已有状态：`smile → joy`、`half → sleepy`，有覆盖层时优先。
- `effect_table.json` 未改，待机随机池不会自动选这两个覆盖层。

## 8. 硬约束与验收门槛（补 1~补 6，本地并入，不 push）

### 8.1 六条硬约束

1. **零权重**：SAM2 / GroundingDINO / rembg 权重一律用户自备路径 + 缓存
   ignore + 默认关；`.pt/.pth` 不回仓，clone 下来不因缺权重失败。
   rembg 模型下载受限登记为环境限制，不写成「已实现」。
2. **两条入口别合并**：新建页「AI 切割（框手动）」= 本地、图片不上传；
   向导 `ai_generate.json` 的 `remote(/ai/candidates)` = 会发图，必须显式
   同意、只发原图、日志不打 URL / 路径 / 原图名；默认 local 或手动。
   向导 AI 抠图：fake 端到端已验证；真实 rembg 下载受限登记为环境限制，
   不阻塞手动。
3. **AI 输出必须 clamp 到七态 + allowed**：`joy/sleepy` 是封闭覆盖层候选；
   模型吐任意状态名 → 归 `normal`；不注册新 STATES、不碰
   `expr_state.py`、不喂 `derive_allowed()`。**「七态不再增加」指保持
   `7531ff3` 的七态字节，不是退回六态。** `smile → joy` / `half → sleepy`
   只是渲染层取图键（有 `<stem>_joy.png` 才叠），不是 `ExprBank.apply()`
   的字符串别名；不重建别名表，`smile` 不因覆盖层被吞掉。断言：
   `test_overlay_keys_do_not_rebuild_alias`。memory 内容同理，不进词表。
4. **默认零勾选 + 整体否决优先**：动作参数逐项勾选默认一只不选；被否决项
   不进 `commit_changes()`；与 B-1 同款。
5. **失败矩阵 + 单一写盘**：缺 key / 断网 / 429 / 乱码 / 框尺寸不符 /
   全透明 / 路径越界 / 写盘失败 / 用户否决 → 整体回手动、原子回滚、
   不留半份 SKU。源图走 `launch.save_cut_source()`（手动/AI 共用，只写
   `<stem>.png` + `<stem>_mask.png`）；manifest patch / 覆盖层走
   `commit_changes()`；两层不是第二条状态源。断言：
   `test_ai_cut_source_path_is_shared_and_commit_free`。
6. **批量只出 report**：批量处理只产候选报告，逐 SKU 人工点选后才落盘；
   不做批量 commit，不撞已否决的「批量补 socket」。

### 8.2 验收与 push 门槛（Go / No-Go）

- 保护基线 `7531ff3`：`anim.py / arbiter.py / expr_state.py /
  manifest_io.py / manifest.schema.json / effect_table.json /
  .github/workflows/` 七项零 diff；tag `v0.1`、`docs/images/`、workflow 不动。
- 真实 API / 本地模型不进 CI：fake provider + monkeypatch；干净 venv 五包
  `MPLBACKEND=Agg`；`--collect-only -q` 能核到 `test_aig_*` / `test_agent_*`。
- 测试数与 hash 以 `pytest -q` / `git log` 当前结果为准，不写死。
- 五项全绿才允许推：① 本地 + 干净 venv 全绿；② fake 端到端
  「候选 → 点选 → ChangeSet → validate_manifest → 读回」；
  ③ AI 进程/模型不可用时桌宠回 idle 且不黑——W0~W5 标 **N/A（Agent
  未接线）**，W6 由 `test_agent_*` 闭环，不补临时测试、不阻塞其余四项；
  ④ 保护文件 diff 为空、无权重无新依赖；⑤ `origin/main` 仍 `7531ff3`、
  工作树仅 `?? docs/images/`。
- `tag v0.1` = annotated object `fe5d76f` → commit `b03cdb7`；不重打、不移动。
- 本地多 commit（feat / test / docs 分开），全绿后一次性
  `git push origin main`；在此之前不 push。

### 8.3 文档同步

- README 旧表述（长期记忆、旧测试数、bias / mouth_dir 旧快照）按当前实现改准。
- `CHANGELOG.md` / `HANDOFF_V011.md` 分项行 + 底部总览同步；三档
  「已实现 / 已接线 / 未接线（或待拍板）」写清。
- 向导 AI 抠图明确标「未端到端验证」。
- `docs/HANDOFF_AI_GENERATE.md` 保持：默认手动、AI 只出候选、同一闸门、
  上传默认关、权重不回仓、失败整体回手动、两条入口分层、基线 `7531ff3`。

### 8.4 两条已接线的边界

- **独立 Agent**：独立进程 + 窄协议（JSONL / stdio）+ 桌宠侧薄 adapter；
  Agent 是触发源不是状态源，输出统一走 `arbiter.request("interaction", ...)`；
  不加 `"agent": 2`；不塞 Qt 主线程；崩了桌宠回 idle。
  `mood_hint` / 模式化主动陪伴归这里。已落地 `agent_bridge.py`，
  `agent.json` 的 `agent` 段默认关闭。
- **按 SKU memory**：`~/.config/desktop_pet/memory/<sku>.json`，用户层、
  gitignore、不走 `commit_changes()`；自己原子写 `tmp + os.replace`；
  显式触发词才写、结构化条目、不存聊天全文；sku 文件名过
  `^[A-Za-z0-9_-]+$` 守卫；warn 按 sku 去重；不喂 `derive_allowed()`；
  同 SKU 多窗口共享是特性。已落地 `memory_store.py`。

### 8.5 未来建议的约束括号

1. **文本引导切割**：文本进模型 = 用户输入进请求，隐私口径同步；返回仍是
   候选不是最终掩膜，人确认 + 同一闸门；手动画框 + 补点兜底保留、默认手动
   不删；权重 / CUDA 前提按 8.1-1。
2. **AI 自动标注**：走四段式 propose + ChangeSet + validate_manifest；
   草案不自动写 STATES；面积前二疑似挂件靠人挡，不自动判真眼。
3. **更多表情 / 整帧重绘**：`angry/sad/shy` 已作为封闭覆盖层键落地
   （`angry→surprise`、`sad→half`、`shy→smile`）；新增状态名仍是 B-2 同款
   （动状态表 → 单独边界文档 + 显式授权）。整帧重绘 / 中间帧未做，
   别和 PachiPakuGen / Anime2.5DRig 混。
4. **切割质量增强**：只读质检已落地（`cut_quality.py`：白边 / 阴影 /
   软边 / 覆盖率 / 毛刺）；自动重切保留「结果可否决」，打分不自动覆盖
   人工选择。
5. **对话与语音**：memory + Agent 已接线（`memory_store.py` /
   `agent_bridge.py`）；TTS 默认关、ASR 只有函数；新依赖仍单独一轮、
   不进 CI 五包。
6. **工程增强**：多模型路由沿用 `key_env` 占位、零具体模型 ID；流式 / 重试
   默认关且「已吐字不再重试」；token/费用只打类别不打 URL/key；系统钥匙串
   可选，0600 文件 + 环境变量优先先保持。

### 8.6 已否决 7 项（不得从未来建议里捡回）

`partial_text` 二次绘制跳过 · `arbiter` 加 `"agent": 2` · 首期同步
`PetPreview` · 权重回仓 · 预防性改 workflow · 批量补 socket ·
P1-8 修订号纳入 `_pet_key`。
