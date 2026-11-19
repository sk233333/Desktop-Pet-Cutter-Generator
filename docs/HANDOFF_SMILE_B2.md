# B-2 `smile` 独立状态 · 边界文档（仅文档，零代码）

> 基线：`origin/main = 5303995`；`tag v0.1 = fe5d76f` → commit `b03cdb7`（不动）。
> 基线数字：本地 198 passed；干净 venv 194 passed, 1 skipped；工作树仅 `?? docs/images/`。
> **R2 状态：已实现 / 已接线**（授权按 §7 单独发生；本文保留边界原文）。
> **本文不构成授权**。
>
> R2 落地偏离登记：
> - `mouth_open.verify` / `smile_geometry.verify` 的六态 golden 同步为七态，
>   否则向导自检会在新状态表下误报；属必要连带。
> - `tests/test_half_and_effects.py::test_build_call_forms` 改为
>   `set(patches) == set(STATES) - {"smile"}`：smile 不烘焙贴片是设计而非遗漏。
> - GUI 运行时用例加在既有 `tests/test_pet_window_mouth.py`，skip 仍是 1 个模块。
> - 复用 `MouthOverlay` 加载器；`[mouth]` 前缀的 warn 文案对 smile 路径同样生效，
>   未为一个前缀改 `mouth_overlay.py`。

## 0. 结论摘要（待审）

- **推荐落法**：分两段、同轮不同 commit。
  - **R2-A 状态层**：`STATES` 追加 `smile`（放末尾，不挪旧下标）；删除 `'smile': 'half'` 别名；
    `derive_allowed()` **暂不**放 `smile` 进词表，用显式「已登记 / 未接线」常量继续钉住旧口径。
  - **R2-B 消费层**：在 `PetWindow` 侧复用 B-1 的独立覆盖层渲染 `smile`；
    同 commit 把 `smile` 加进 `derive_allowed()`，旧词表断言同步翻转。
- **不碰**：`anim.py`、`arbiter.py`、`.github/workflows`、`manifest_io.py`、`manifest.schema.json`。
  若实现需要改 `anim.build_expr_bank()`（`anim.py:2048-2095`）或 `ExprBank.build()` 内部烘焙
  `smile` 贴片，**停在原地列冲突**，不在本授权草案内。
- **头号风险**：旧断言不能红着、也不能悄悄放宽。逐节点处置见 §1。

## 1. Q1：C-A8 / C-M13 在新状态表下怎么重订

### 1.1 C-M13：继续钉死，不重订

C-M13 原文（`docs/HANDOFF_MOUTH_OVERLAY.md:165`）：
「有 / 无 `mouth_dir` 时 `derive_allowed()` 输出逐项相同；不得因补嘴型把 `smile` 加进词表」。
它的第一句与 `smile` 是否独立状态无关——嘴型覆盖层不得成为词表输入，这条**原样保留**。
第二句的「因补嘴型」也要保留：B-2 若把 `smile` 加进词表，理由必须是「独立状态已接线」，
不能是「补了嘴型」；实现注释与 commit message 都要写清这个因果。

落点：`tests/test_mouth_contract.py:16-22`（有 / 无 `mouth_dir` 的对比与
「不读 manifest item」断言）不动；R2 只在其上补一条「`smile` 状态变化不改变
`mouth_dir` 有无的等价性」。

### 1.2 C-A8：仓库无原文，按现有落点等价重述

全仓只有 `docs/HANDOFF_V011.md:36` 引用 `C-A8`，没有原文与编号清单。按现有测试，
C-A8 的**实际落点**是：

- `tests/test_chat_a1.py:286-290`：`derive_allowed()` 排除 `blink` / `smile`；
- `tests/test_mouth_contract.py:16-22`：词表只从 `STATES` / registry / effects 反推。

因此本文把 C-A8 重述为一句可执行判据：
**「词表只从真实可渲染产物反推；不靠 `apply()` 的别名把不存在的状态伪装成已支持。」**

新状态表下：
- `smile` 成为真实状态且已接线 → 它应进词表；
- `blink` 仍不是对话 emotion → 仍排除；
- 别名删除后，任何「不存在的状态」仍不允许借别名混进词表。

### 1.3 旧断言逐节点处置（R2 必须同轮改，不允许红着）

| 旧节点 | 现断言 | R2 处置 |
|---|---|---|
| `tests/test_mouth_runtime_ci.py:224-230` `test_cm7_state_table_alias_signature_unchanged` | 六态 + `'smile': 'half'` + `apply` 签名 | 改为七态（`smile` 末尾）+ **断言源码不再含** `'smile': 'half'`；`ExprBank.apply` 签名断言保留 |
| `tests/test_sway_fields.py:535-542` `test_c5_state_table_and_alias_unchanged` | 六态 + 别名 + `apply_params` 签名 | 同上去六态/别名；`HairField.apply_params` 签名断言保留 |
| `tests/test_smile_geometry.py` `test_b1_vocabulary_and_state_table_unchanged` | `"smile" not in STATES` + 别名存在 | 改为「B-1 数据槽不写词表」；把状态表断言移到 B-2 节点，不再在 B-1 测试里钉六态 |
| `tests/test_chat_a1.py:286-290` `test_derive_allowed_excludes_blink_and_smile` | 同时排除 blink / smile | 拆两条：`..._excludes_blink`（永远）；`..._includes_smile_when_wired`（R2-B 接线后） |
| `tests/test_mouth_contract.py:16-22` | 词表不读 mouth_dir | **保留**，补 `smile` 状态变化不影响 mouth_dir 等价性 |
| `tests/test_upgrade_wizard.py:203-212` `test_allowed_is_proposal_only_and_excludes_smile` | 向导提案不含 smile | **继续钉死**：向导提案是 proposal-only，不是运行时词表；见 §1.4 |
| `tests/test_upgrade_wizard.py:255-257` | `STATES` 六态 + `from_agent("smile") == "half"` | 改为七态 + `from_agent("smile") == "smile"`；该断言的意图是「mouth.open 不动状态表」，需改写成「mouth.open 不新增状态」 |
| `tests/test_mouth_overlay.py:158-163` | `mouth_level == "smile"` | **不动**：这是嘴型贴片档位，和 `ExprBank` 状态无关，别混改 |

### 1.4 向导 `allowed` 提案：继续排除 smile

`desktop_pet/upgrade_core.py:157` 的 `_EXCLUDED_STATES = ("smile", "blink")` 与
`:180` 的过滤在 R2 **保持不变**。理由：该提案 `runtime_consumer: None`（`:183-185`），
是体检/提案文本，不是运行时词表；B-1 的 C-U5 边界也钉死了「提案不写词表」。
文档必须同时写两句，避免两套话：
- 运行时词表（`derive_allowed`）在 R2-B 后**包含** `smile`；
- 向导提案（`allowed_proposal`）**继续不含**，二者不是一个东西。

R2-A 阶段（状态已登记、消费未接线）`derive_allowed` 也继续排除 `smile`，但理由换成
显式常量（如 `_UNWIRED_STATES = ("smile",)`），不再叫「别名会错位」。这是「按新状态表重订理由」，
不是「悄悄放宽」。

## 2. Q2：`smile` 由谁消费

### 2.1 候选路径

| 路径 | 改动面 | 判定 |
|---|---|---|
| A. `ExprBank.build()` 烘焙 `smile` 贴片 | `expr_state.py:230-319` 内部渲染 + 需要 `anim.build_expr_bank()` 透传数据（`anim.py:2048-2095`） | **红线冲突**：撞 `anim.py`，停下列冲突，不在本草案 |
| B. `PetWindow` 侧独立覆盖层 | `pet_window.py` 新增加载与 `_expr_frame` 分支；不碰 `anim.py` / `ExprBank.build` | **推荐**；仍属生产行为，需授权 |
| C. 不接线，只登记状态 | 只改 `STATES` / 别名 / 词表 | 会让模型输出 `smile` 却渲染成原帧（静默降级），不可接受 |

### 2.2 推荐路径 B 的读写链

1. `PetWindow.__init__` 已有嘴型覆盖层合成点（`pet_window.py:191-193`）。R2-B 在同处
   读取 `item["smile_geometry"]["overlay"]`，路径守卫与尺寸/损坏/缺失回退沿用
   `_load_mouth_overlay`（`pet_window.py:788` 附近的同类规则，实现时按现有函数复用）。
2. `_expr_frame(state)`（`pet_window.py:612-629`）：`state == "smile"` 且覆盖层可用时，
   在 `_render_rgba` 上合成该覆盖层；否则回退 `_render_rgba`（原帧，不抛）。
3. `_expr_active()`（`:606-610`）仍要求 `expr_bank()` 存在；**不为 smile 放宽**。
   没有眼眶 bank 的素材，smile 覆盖层也不启用——回退是原帧，不是半眼。
4. 不把 `smile_geometry` 透传给 `build_expr_bank()`；`ExprBank.apply("smile")` 因无 patch
   原样返回帧（`expr_state.py:339-346`），不会重影。
5. 与 `mouth_dir` 重叠：B-1 覆盖层含嘴角弧，若素材已有 `mouth_dir` 会双嘴。R2-B 必须先定
   一条规则，二选一（推荐前者）：
   - **生成侧**：`smile_geometry` 生成时若 `has_mouth`，覆盖层只画眼睑 + 腮红，
     `mouth_arc` 置 `null`；已有覆盖层需重跑一次向导。
   - 运行侧：有 `mouth_dir` 时跳过整张 smile 覆盖层（眼睑/腮红也丢失，不推荐）。
6. 编辑器 `ui_app.PetPreview` **首期不同步**（沿用嘴型 overlay 的 Q4 裁定），R2 不改。

## 3. Q3：别名拆除后的失效回退（不抛、不静默变半眼）

| 输入 / 调用 | 现状 | B-2 后行为 |
|---|---|---|
| 模型输出 `emotion: "smile"` | 别名 → `half` | R2-A：不在词表 → `clamp()` 归一为 `normal`（`chat_contract.py:145-146`）；R2-B：在词表 → 进入 `smile` |
| 旧 `agent.json` / 旧 manifest `effects.enabled` 含 `"smile"` | 字符串被忽略或原样保留 | 不崩溃；`_enabled_states()`（`chat_contract.py:152-158`）原样透传，`derive_allowed` 按当前词表裁剪 |
| `ExprState.set("smile")` | 未知 → `normal`（`expr_state.py:382-385`） | 登记后合法；拼写错误仍 → `normal` |
| `ExprState.from_agent("smile")` | 别名 → `half`（`:403-408`，别名在 `:405`） | 删除该条别名后原样返回 `"smile"`；`jito/shy/angry/sad` 仍 → `half` |
| `ExprBank.apply(frame, "smile")` | 无 patch → 原样返回（`:339-346`） | 不变；R2-B 的覆盖层在 `PetWindow` 层合成，不依赖 `ExprBank` patch |
| 向导 `allowed` 提案含 / 不含 smile | 不含 | 继续不含（§1.4），不写运行时词表 |
| `mouth_level == "smile"` | 嘴型贴片档位 | **不动**；与状态表无关，别顺手改名 |
| 旧六态图集（`ExprBank.save`） | `states=list(STATES)`（`:332`） | 变七态、空 tile；仓库内无 `save()` 调用方，外部产物需重生成，CHANGELOG 记一句 |

## 4. Q4：读写路径清单（按当前源码行号）

| 位置 | 当前职责 | R2 动作 |
|---|---|---|
| `expr_state.py:17` `STATES` | 六态元组 | 末尾追加 `"smile"`；不挪旧下标 |
| `expr_state.py:230-319` `ExprBank.build()` | 生成 `normal/blink/wink/half/surprise` 贴片 | **不改**；不生成 `smile` patch |
| `expr_state.py:321-336` `ExprBank.save()` | 图集按 `STATES` 排布（`cols=3`、`:332` meta） | 代码不改；7 项时空 tile，文档登记 |
| `expr_state.py:339-356` `ExprBank.apply()` | 无 patch / None → 原样返回 | 代码不改；作为回退依据 |
| `expr_state.py:382-392` `ExprState.set()` | 未知状态 → `normal` | 代码不改；`smile` 变合法 |
| `expr_state.py:403-408` `ExprState.from_agent()` | `{'smile': 'half', ...}` 别名 | **删除** `'smile': 'half'`，其余别名保留 |
| `anim.py:2048-2095` `build_expr_bank()` | `ExprBank.build(L, sock, report=...)`，docstring 写「6 个」 | **零字节**；docstring 的「6 个」允许保持旧值，不为改注释破红线，CHANGELOG 记一句 |
| `anim.py:608` | 构建期缓存 `_expr_bank` | 不改 |
| `pet_window.py:191-193` | 嘴型覆盖层合成 `_render_rgba` | R2-B 同处加载 smile 覆盖层（新函数） |
| `pet_window.py:572-603` `_pick_frame()` | 点击眨眼 → 分组眨眼 → expr → gaze → base | R2-B 由 `_expr_frame` 分支承载，不新增更高优先级路由 |
| `pet_window.py:606-610` `_expr_active()` | 要求 `expr_bank() is not None` | 不改；无 bank → smile 不启用 |
| `pet_window.py:612-629` `_expr_frame()` | `blink` 特判，其余走 `expr_bank.apply` | 新增 `smile` 覆盖层分支；失败回退 `_render_rgba` |
| `pet_window.py:294-300` | 启动时 `derive_allowed(self.sku, STATES, ...)` | 代码不改；`STATES` 变七态后行为随词表变 |
| `chat_contract.py:162-190` `derive_allowed()` | `:182` 排除 `blink/smile/normal` | R2-A 保留 `smile` 排除并改理由常量；R2-B 删除 `smile` 排除 |
| `upgrade_core.py:157,180` | 向导提案排除 `smile/blink` | **不改**（§1.4） |
| `manifest_io.py:39-58` `read_manifest()` | 未知 item 字段原样保留 | 不改；`smile_geometry` 继续透传 |
| `manifest.schema.json:98-147` `item` | 未设 `additionalProperties: false` | 不改 |

## 5. Q5：B-1 口径同步

- R2-A 完成、消费未接线时：B-1 仍是 `runtime_consumer: null` / 「已实现 / 未接线」，
  `docs/HANDOFF_V011.md` 与 `CHANGELOG` 不得提前写「已接线」。
- R2-B 接线同轮 docs commit：`smile_geometry.runtime_consumer` 改为 `"pet_window"`，
  B-1 状态改「已实现 / 已接线」；HANDOFF_V011 的 B-2 行、CHANGELOG 的 B-1 节、
  README 的补全器表**同一 commit**改完，避免三处两套话。
- 若 R2 只做 A 不做 B，则 B-1 口径**一律不动**，并在回执里明说「B-2 消费未接线」。
  （R2 实际已同时完成 A/B；B-1 口径已改为 `"pet_window"`，本节保留推导过程。）

## 6. Q6：断言与 commit 拆分

### 6.1 新增 / 改写断言（Qt-free）

| 断言 | 落点 |
|---|---|
| 七态、无 `'smile': 'half'`、`apply` 签名不变 | 改写 cm7 / c5 / b1 三个旧节点（§1.3） |
| `smile` 未接线时 `derive_allowed` 不含；接线后含 | `test_b2_smile_vocab_follows_consumer`（参数化） |
| 无 patch 的 `apply("smile")` 原样返回 | `test_b2_apply_smile_without_patch_is_identity` |
| 旧 effects / 旧 agent 字符串不崩溃、不半眼 | `test_b2_legacy_smile_strings_fall_back` |
| 有 / 无 `mouth_dir` 的词表等价（C-M13） | 复用 `test_mouth_contract.py`，补一条 |
| 向导提案仍不含 `smile` | 保留 `test_allowed_is_proposal_only_and_excludes_smile` |
| `mouth_level == "smile"` 不受影响 | 保留 `test_mouth_overlay.py:158-163` |
| 有 `mouth_dir` 时不双嘴（R2-B 生成侧规则） | `test_b2_smile_overlay_omits_arc_when_mouth` |
| 保护文件 | `anim.py` / `arbiter.py` / `manifest_io.py` / `manifest.schema.json` / `effect_table.json` / workflow 哈希不变；**`expr_state.py` 哈希按授权范围更新**（只允许 STATES 行与别名行变化，diff 需人工核） |

### 6.2 数字 / commit / push

- 数字该变：以 `pytest -q` 实际输出为准；skip 保持 **1**（不新开 GUI 文件）。
- `pytest --collect-only -q | grep b2` 逐条核到。
- commit 建议 3~4 个：`feat(state)` → `feat(consumer)` → `test` → `docs`；
  纯 docs 与 feat 分开；最后一次 `git push origin main`。
- 回归顺序：`git log` / `pytest -q` / 工作树 / tag / skip → 再核断言与哈希。

## 7. R2 授权范围草案（待你批）

**允许改**：
- `desktop_pet/expr_state.py`：仅 `STATES` 元组（`:17`）与 `from_agent` 别名（`:405`）；
  **不**改 `build()` / `save()` / `apply()` / `set()` 的行为。
- `desktop_pet/chat_contract.py`：仅 `derive_allowed()` 的排除常量（`:182`）。
- `desktop_pet/pet_window.py`：仅 smile 覆盖层加载与 `_expr_frame` 分支。
- `desktop_pet/completers/smile_geometry.py`：仅 `has_mouth` 时嘴角弧规则（若选生成侧）。
- `tests/`、`docs/`。

**不允许**：`anim.py`、`arbiter.py`、`.github/workflows`、`manifest_io.py`、
`manifest.schema.json`、`tag v0.1`、任何权重 / LFS 对象。

**验收红线**：授权后只有 `expr_state.py` 的哈希允许变，且 diff 仅限上述两处；
其余 6 项保护文件零字节。若实现发现必须动 `anim.py` 或 `ExprBank.build()`，
**停下先列冲突**，不要自行扩权。

## 8. 与总表的对照

- R1（本文）：只出文档；`desktop_pet/` 与 `.github/` 零 diff；未动 `STATES` / 别名表。
- R2：须显式授权后，按 §7 范围落地；未授权前状态表保持六态。
- B-3 / B-4、A 档、C-1、D 档、已否决 7 项：不在本文范围。
