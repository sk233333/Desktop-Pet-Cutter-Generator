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
- `anim.py / arbiter.py / manifest_io.py / manifest.schema.json /
  effect_table.json / .github/workflows` 零 diff。
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
python3 -m py_compile desktop_pet/*.py
# 干净 venv 五包 + MPLBACKEND=Agg + import PyQt6 → ModuleNotFoundError
QT_QPA_PLATFORM=offscreen python3 desktop_pet/ui_app.py
git diff --name-only 7531ff3..HEAD -- <保护文件 7 项>   # 期望空
git status --porcelain                                # 期望仅 ?? docs/images/
git rev-parse --short origin/main                     # 期望仍 7531ff3（未 push）
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
| W0 三处收口 + 3 个本地 commit | 进行中 |
| W1~W7 | 未开始 |
| W8 Go/No-Go → push → v0.2 | 未开始 |
| A 档人工 / C-1 / 已否决 7 项 | 并行 / 不动 / 不做 |
