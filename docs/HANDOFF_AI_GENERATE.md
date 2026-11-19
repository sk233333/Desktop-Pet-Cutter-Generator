# AI 生成入口 · 边界与实施（本地开发，未 push）

> 状态：AI-G0~G9 已本地实现并验证。**origin/main 冻结在 `7531ff3`**；
> 在 G9 全绿前禁止 `git push`。测试数 / hash 以 `pytest -q` / `git log`
> 当前结果为准，本文不写死。硬约束与 Go/No-Go 见
> `HANDOFF_AI_MOTION.md` §8。

## 1. 目标与红线

- 「AI 一键」= **一次请求出全部候选**，不等于一次点击自动写盘。
- 新建 SKU 第一步：**手动切割 / AI 生成（默认手动）** → AI 出候选 → 并排预览
  叠真实图 → 逐项人工点选 / 整体否决 → ChangeSet → `validate_manifest` →
  原子写 + 失败回滚 → 后续仍走现有补全 / 摆动 / 嘴型 / 表情链路。
- **唯一落盘闸门**：手动与 AI 都产出同一种 `ChangeSet`，都调用
  `upgrade_core.commit_changes()`；不新增第二条写盘路径、第二份状态源、
  第二个配置真源。
- 默认 **零网络**；上传默认关，显式开启后才发原图；日志只打状态类别，
  不打 URL / 路径 / key / 原图名。
- 不引新依赖、不进 PyQt6 到 CI、不打包权重、不改 `.github/workflows/`。

## 2. G1 读码事实（作为实现依据）

| 位置 | 事实 |
|---|---|
| `desktop_pet/upgrade_wizard.py` | 六步 QDialog；`propose → preview → commit → verify` 四段式；候选可整体否决；只持有引擎引用 |
| `desktop_pet/upgrade_core.py` | `commit_changes()` 统一迁移 / 合并 / `validate_manifest` / 原子写 / 回滚；当前**要求 manifest 已存在**（新 SKU 需加内存 fallback） |
| `desktop_pet/completers/base.py` | `Candidate / ChangeSet / OverlayImage / guard_paths / apply_patch / encode_png`；`apply_patch` 对 `items` 是整表替换 |
| `desktop_pet/completers/registry.py` | `Entry(id, module, domain, risk, summary)`；补全器四段式接口 |
| `desktop_pet/manifest_io.py` | `validate_manifest` 不拒绝未知 item 字段；`v1_item_to_v2` 用 `dict(item)` 保留未知键 |
| `desktop_pet/manifest.schema.json` | item 无 `additionalProperties: false`；B-1 已用可选字段先例 |
| `desktop_pet/pet_window.py` | 嘴型 / smile 覆盖层都在素材分辨率合成一次；四路取帧共用 `_render_rgba` |
| `desktop_pet/chat_provider.py` | 标准库 urllib + SSE；`chat.enabled=false` 时零网络；不重试 |
| `desktop_pet/agent.json.example` | 占位式模板：不写模型 ID / host / key；`key_env` 指环境变量 |
| `desktop_pet/ui_app.py` | 目前只有「能力补全向导…」入口；手动切割走 `cutout_to_pet.build_one` |
| `.gitignore` | 已有 `*.local.json` / `.env` / `agent.json` / `assets/` / 权重规则 |

**AI 产物落层（决定）**：新 SKU 的 base 由 AI soft alpha 生成，但**不直写**；
作为 `ChangeSet.files` 交给 `commit_changes()`。已有 base 一律拒绝覆盖。
manifest 只加可选字段 `items[].ai_provenance`（provider 类别 / confidence /
warnings / 是否上传），不升 schema；预览 PNG 也是 `ChangeSet.files`。

## 3. G2 配置模板

新增 `desktop_pet/ai_generate.json.example`：

```
enabled: false
transport: remote            # remote = 远端服务；local = 本地分割（零网络）
provider: <placeholder>
base_url: <placeholder>
model: <placeholder>
key_env: <ENV_VAR_NAME>
timeout_ms: 15000
max_image_bytes: 8388608
upload_consent: false
cache_dir: <user-local-cache>
thinking: false
```

- `transport=local` 时不要求 `upload_consent` / `key_env` / `base_url`；
  本地推理不上传、不读 key，模型名可空（默认 `isnet-general-use`）。
- `.gitignore` 增 `desktop_pet/ai_generate.json` 与 AI 缓存目录。
- 两个开关独立：`chat.enabled` 管聊天；`ai_generate.enabled` 管配置期候选。

## 4. G3 provider 契约（Qt-free）

- `desktop_pet/ai_generate_contract.py`：`AIGenConfig` / `AIGenRequest` /
  `AIGenCandidate`（soft alpha、boxes、confidence、warnings、provenance）/
  `AIGenError` / `load_config()` / `validate_config()`。
- `desktop_pet/ai_generate_provider.py`：`AIProvider` 协议 +
  `FakeProvider`（测试用）+ `HttpAIProvider`（urllib，复用 A1 的键位/超时思路）。
  `propose()` 只出候选、不写盘；缺 key / 超时 / 429 / 5xx / 非 JSON 一律抛
  `AIGenError`，由入口整体回手动。
- `commit(selected) -> ChangeSet` 由 adapter 负责；`verify()` 不联网、不写盘。

## 5. G4 adapter

`desktop_pet/ai_generate_adapter.py`：

1. soft alpha × 原图 RGB → 候选 base RGBA；只进 `ChangeSet.files`。
2. 生成 `<stem>_ai_preview.png`（独立覆盖层）与 `items[].ai_provenance`。
3. 复用现有补全器字段（socket / wisp / 表情差分），不重造。
4. 路径守卫：拒绝 `..`、绝对路径、跳出 SKU 目录、直写 `manifest.json`。
5. 不写 mask PNG、不改已有 base。

**新 SKU 闸门 fallback**：`commit_changes()` 在 `manifest_path` 不存在且
`info.manifest` 是 dict 时，以内存 v2 骨架为起点；这是唯一新增的闸门行为，
仍只有一条写盘路径。失败时回滚并清理本次新建的空目录。

## 6. G5 入口

新增 `desktop_pet/new_sku_wizard.py`（QDialog）：

- 模式单选：**手动切割（默认） / AI 生成**。
- AI 模式：选图 → 勾选「允许上传原图」→「生成候选」。
- 候选默认全不选；并排预览叠真实图；逐项勾选 + 整体否决。
- 否决优先：被否决候选不进 `commit_changes`。
- 失败整体回手动，不弹半份 SKU。
- 手动模式指向现有 `cutout_to_pet` 流程，不复制一套切割逻辑。

## 7. G6 失败回退矩阵

| 情况 | 行为 |
|---|---|
| `enabled=false` | 零网络，走手动 |
| 缺 key | 请求前失败，零网络 |
| 断网 / 超时 / 429 / 5xx | 整体回手动，不留半成品 |
| 非 JSON / 乱码 | 整体回手动 |
| 候选尺寸不符 / 全透明 / 损坏 | 跳过该候选；全部无效则整体回手动 |
| 路径越界 | 拒绝 + warn |
| 用户否决 | 无 `commit_changes` 调用 |
| 写盘失败 | 原子回滚，字节 / mtime 不变 |

判据：失败后 `git status --porcelain` 仅 `?? docs/images/`，`assets/` 无临时 SKU。

## 8. G7 测试（Qt-free 优先，前缀 `test_aig_`）

`tests/test_ai_generate.py` + `tests/test_ai_generate_adapter.py`：

- `test_aig_default_manual_zero_network`
- `test_aig_missing_key_fails_before_network`
- `test_aig_example_has_no_model_id_host_or_key`
- `test_aig_fake_provider_returns_candidates`
- `test_aig_commit_is_changeset_only`
- `test_aig_same_gate_as_manual`
- `test_aig_path_guard_rejects_parent_absolute_manifest`
- `test_aig_selected_candidates_validate_and_readback`
- `test_aig_v1_sku_migrates_and_keeps_fields`
- `test_aig_failure_leaves_no_half_sku`
- `test_aig_state_table_and_protected_files_unchanged`（基线 `7531ff3`）
- `test_aig_no_weights_no_new_deps`
- `test_aig_skip_policy_unchanged`

真实 API 不进 CI；fake provider / monkeypatch 截断网络。

## 9. G8 本地 commit（不 push）

1. `feat(ai-generate): 新建 SKU 手动/AI 二选一与候选预览 UI + 入口`
2. `feat(ai-generate): provider 适配层与 ChangeSet 接线`
3. `test(ai-generate): fake provider 与回退矩阵`
4. `docs: AI 生成入口边界、隐私、失败回退与可行性验证`

## 10. G9 Go / No-Go

- 本地与干净 venv 全绿；skip 仍为 1；`--collect-only -q | grep test_aig_` 可见。
- fake provider 走完：候选 → 点选 → ChangeSet → `validate_manifest` → 临时 SKU 读回。
- 保护文件自 `7531ff3` 零 diff；`origin/main` 仍 `7531ff3`；tag 不动。
- 4 张真实素材小评测（垂发 / 衣摆 / 窄侧 / 装饰眼）：至少 3 张人工点选后可用；
  不自动写盘、不默认上传；质量不达标则保留为实验开关，手动仍默认。
- 全绿后才允许一次 `git push origin main`。

## 11. 不做

- 完整表情贴片烘焙、新建 STATES、自动注册 smile/wink。
- 端到端自动写 manifest、权重回仓、AI 进 CI、B-4 说话驱动切档。

## 12. 实现落点（G2~G8 已完成，本地未 push）

> 主页「AI 识别抠图」已改为 API 视觉模型给框 + 本地 GrabCut/SAM 精修，
> 配置复用 `ai_motion.json`（齿轮旁 **AI** 按钮）。本文件的
> `transport=local` / rembg / `/ai/candidates` 分支保留给「新建 SKU」向导与
> 实验入口，不再是主页默认路径。

| 文件 | 职责 |
|---|---|
| `desktop_pet/ai_generate.json.example` | 配置模板：`enabled=false`、`transport=remote`、占位 host/模型、`upload_consent=false`、无 key |
| `desktop_pet/ai_generate_contract.py` | `AIGenConfig / AIGenRequest / AIGenCandidate / AIGenError`；`load_config`、`validate_config`、`propose_candidates`（按 transport 分支校验） |
| `desktop_pet/ai_generate_provider.py` | `FakeProvider`（CI/POC）+ `HttpAIProvider`（远端，urllib）+ `LocalMattingProvider`（本地 rembg，惰性导入） |
| `desktop_pet/ai_generate_worker.py` | 本地推理 QThread；UI 线程只等结果，不冻结 |
| `desktop_pet/ai_generate_adapter.py` | soft alpha → base RGBA + 独立预览 PNG + `items[].ai_provenance`；`commit_selected()` 委托 `upgrade_core.commit_changes()` |
| `desktop_pet/new_sku_wizard.py` | 新建 SKU：手动切割（默认）/ AI 生成；按 transport 选 provider；候选默认不选；整体否决；失败回手动 |
| `desktop_pet/ui_app.py` | 图片组「抠图方式：手动框选（默认）/ AI 识别抠图」；AI 走 `ai_motion.json` 的视觉模型，只替换框取，像素掩膜仍由本地 GrabCut/SAM 完成；之后同一份 `self._cut`；新增「新建 SKU…」入口 |
| `desktop_pet/ai_cut_box.py` | 视觉模型请求 / 归一化框解析 / 像素框与留边；图片缩到最长边 1024 再发，不打印图片内容 |
| `desktop_pet/upgrade_core.py` | 仅加「manifest 不存在但内存有 v2 骨架」的 fallback + 失败时清理本次新建空目录；写盘路径仍唯一 |
| `tests/test_ai_generate.py` / `tests/test_ai_generate_adapter.py` | `test_aig_*`，fake provider / 注入 opener，真实 API 不进 CI |

### 验收事实

- `pytest -q`：以当前结果为准；`test_aig_*` 节点数用 `--collect-only -q` 核。
- 干净 venv 五包 + `MPLBACKEND=Agg`：以当前结果为准；`import PyQt6`
  仍 `ModuleNotFoundError`。
- `--collect-only -q | grep test_aig_` 可见 25 个节点。
- fake provider 全链路：候选 → 点选 → ChangeSet → `validate_manifest` →
  临时 SKU 读回 `ai_provenance` / base RGBA / 预览 PNG。
- 保护文件自 `7531ff3` 零 diff；tag `v0.1` 仍 `b03cdb7`；工作树仅
  `?? docs/images/` 与本次新增文件。
- 无 `.pt/.pth`、无新依赖、`.github/workflows/` 未动。
- 4 张真实素材小评测（垂发 / 衣摆 / 窄侧 / 装饰眼）留作人工验证项：
  不伪造结果；不达标时保留 AI 为实验开关，手动仍默认。
