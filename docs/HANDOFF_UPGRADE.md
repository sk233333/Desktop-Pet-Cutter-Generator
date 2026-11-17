# 升级向导与能力包 · 交接（实现边界 / 补丁顺序 / 断言落点）

状态：**已实现并提交**（向导首期）。本文保留为交接记录；实现以仓库代码为准。
基线：实现前 `origin/main = 1d78d15`，`tag v0.1` = tag 对象 `fe5d76f` → commit `b03cdb7`（未动）；
`pytest -q` 实现前 **93 passed**（pre-wizard 快照），实现后 **108 passed**；以 `pytest -q` 当前结果为准。

实现落地：`desktop_pet/completers/{base,registry,mask_ellipse,mask_domain_hint,mouth_open}.py`、
`desktop_pet/upgrade_core.py`、`desktop_pet/upgrade_wizard.py`、`desktop_pet/ui_app.py`（唯一入口）、
`tests/test_upgrade_wizard.py`（15 条，Qt-free）。最终验收编号以
《升级向导首期交接：裁定记录与补丁边界》的 **C-U1~C-U15** 为准。

---

## 0. 已拍板口径（实现时以本节为准）

| 项 | 最终口径 |
| --- | --- |
| Step 0 | 扫 `desktop_pet/assets/*/manifest.json`，读顶层 `sku` 与 `schema_version`，以**目录名**为 SKU 主键；不臆造 `skus` 键 |
| 目录名 ≠ 文件内 `sku` | propose 阶段列为待修项并**判失败**；不静默取一、不自动纠正 |
| `schema_version` ≠ v2 | 只列「待迁移」告警，不阻塞 L1/L2 只读体检 |
| C-U5 | 向导在 propose 阶段**只输出 `allowed` 提案文本**；`derive_allowed()` 未实现/未拍板前，任何写运行时词表的路径**判失败**。断言保留、编号不动 |
| C-U10 | 「`effects.enabled` 与 `action_bridge` 配置不受影响」；做 commit 前后值快照逐项比对；向导**不得新增任何顶层配置键**；不存在的 `chat.enabled` 从规范删除 |
| `smile` | Step 1 保留体检项，标 `v0.1.1 / 待状态拍板 / pending`，**不列为本轮可补**；`STATES` 六态、`smile → half` 别名、状态常量本轮一律不动 |
| Q1 | 首期 = L1/L2 + `mask.from_ellipse` / `mask.domain_hint` / `mouth.open`；`expr.smile_geometry`、`calib.*`、L3 排 v0.1.1。三者各自独立走四段式，不打包 |
| Q2 | 独立 `upgrade_wizard.py`（QDialog）+ 单入口唤起 + 注入 `_pet_engine_cache` / `PetPreview`；向导不持有引擎所有权，close 不 dispose、不重建；竖条页布局零改动 |
| Q3 | L3 默认关闭；每次开启用**整份模块清单单次确认**后再加载（不逐项弹窗） |
| Q4 | 保留 Issue #1/#2；向导只做纯展示 + 可选跳转，不写回状态、不调关单接口 |
| Q5 | 本文即交接文档；代码由实现方按本文落地 |

---

## 1. 实现前必须先答的阻塞项

实现方遇到以下任一项，**停在原地提问**，不要自行设计：

### B1（硬阻塞）`mouth.open` 没有运行时接线

事实：

- `desktop_pet/manifest.schema.json` 顶层只有 `schema_version / schema / sku / generated_at / items`，**没有任何 mouth 字段**；
- `desktop_pet/anim.py::build_expr_bank()` 调用 `ExprBank.build(L, sock, report=report)`，**没有传 `mouth_dir`**；
- `ExprBank.build()` 本身支持 `mouth_dir` 参数，但当前主链路没有传。

也就是说：`mouth.open` 产出的档位图**目前无人消费**。落地裁定：**选 C**——
只产独立覆盖层 PNG + `mouth_dir` / `mouth_level` 数据槽，不改 `anim.py` 调用方、
不改 `ExprBank.STATES` / 别名表。接线（消费 `mouth_dir`）排 v0.1.1，**需显式放行**。
为避免下一个人误判成 bug，README / CHANGELOG 均已写明「当前为数据槽，运行时未消费」。

### B2（硬前置）`derive_allowed()` 属于 v0.1.1 阻塞项，不是排期项

仓库不存在该函数；`ExprBank.STATES` 也没有 `smile`。本轮 C-U5 只验证「提案文本存在 + 无词表写入」，
**不实现** 词表推导。`derive_allowed()` 与 `expr.smile_geometry` 共用一个前置：AI 范围页拍板。

### B3（已定，无需再问）「逐次列出」= 单次清单确认

每次开启 L3，先列出当次将加载的全部模块名，用户确认一次后统一加载；不逐项弹窗。

### B4（已选 A）`ui_app.py` 当前没有 `QMenuBar`

落地：右侧面板加唯一按钮 `能力补全向导…`，懒加载 `UpgradeWizard` 并注入现有
`_pet_engine_cache`；竖条页布局零改动，不再扩展入口。未新增 `QMenuBar`。

---

## 2. 文件边界

### 2.1 新增（全部纯新增，不改既有签名）

| 文件 | 职责 | 依赖约束 |
| --- | --- | --- |
| `desktop_pet/completers/base.py` | `Candidate` / `ChangeSet` / `Report` 数据类；四段式协议常量；路径守卫（SKU 目录内、拒绝 `..` 与绝对路径） | 仅标准库 + numpy |
| `desktop_pet/completers/registry.py` | `id → 模块 / 适用域 / 风险 / 默认参数`；内置项显式 import；L3 扫描仅在开启时用 `importlib` 查四函数，**不 `exec`** | 仅标准库 |
| `desktop_pet/completers/mask_ellipse.py` | 由 `items[].eyes` 椭圆生成 socket（`rx+1, ry+1`），复用 `cutout_to_pet.save_eye_masks()` 的编码规则；`commit` 返回 `ChangeSet`（PNG 字节 + `parts[].mask` diff），不写盘 | numpy / cv2 |
| `desktop_pet/completers/mask_domain_hint.py` | 连通域 / y 离群候选框；**只 propose / preview**，`commit` 返回空并声明不可提交 | numpy / cv2 |
| `desktop_pet/completers/mouth_open.py` | 2~3 档 mouth 候选；**P4 受 B1 阻塞** | numpy / cv2 |
| `desktop_pet/upgrade_core.py` | **Qt-free**：扫 SKU、目录名/`sku` 一致性、快照、`validate_manifest` 闸门、落盘/回滚、`allowed` 提案文本、L3 清单确认 | 标准库 + `manifest_io` |
| `desktop_pet/upgrade_wizard.py` | PyQt6 六步 QDialog；只做 UI 与流程编排；引擎由外部注入 | PyQt6 + `upgrade_core` |
| `tests/test_completers.py` | 三个补全器的 propose/preview/commit/verify；不含 Qt | numpy / cv2 / pytest |
| `tests/test_upgrade_core.py` | SKU 扫描、一致性失败、快照、路径守卫、回滚、C-U5/C-U10 | 标准库 / pytest |
| `tests/test_expr_states_invariant.py` | 六态表与 `smile → half` 不变式 | `expr_state` |

> CI 只装 5 个包（numpy / opencv-headless / pillow / matplotlib / pytest），**没有 PyQt6**。
> 因此所有新增测试必须 Qt-free；`upgrade_wizard.py` 只做人工 offscreen 冒烟，不进 pytest。

### 2.2 修改（一处一界）

| 文件 | 允许 | 禁止 |
| --- | --- | --- |
| `desktop_pet/ui_app.py` | 仅加 1 个入口（B4 选 A 或 B）、注入现有引擎实例、close 不回写 | 改竖条页/摆动页布局；持有/重建引擎；新增依赖 |
| `desktop_pet/manifest.schema.json` | 仅在 B1 选 B 时，按 `$defs` 加可选字段 | 改 v2 结构、删 `parts/eye_index`、改 `active_eye_parts` 语义 |
| `desktop_pet/cutout_to_pet.py` | 调用 `save_eye_masks()` | 改其签名/编码；顶层加依赖 |
| `desktop_pet/manifest_io.py` | 复用 `read_manifest` / `validate_manifest` | 改校验口径、改派生字段优先级 |
| `.github/workflows/tests.yml` | 不动 | 新增包、改矩阵、动 `pipefail` / `tee` / `$GITHUB_STEP_SUMMARY` |

### 2.3 本轮禁止触碰

`desktop_pet/arbiter.py`、`render_frame`、`desktop_pet/anim.py`（除非 B1 选 B）、
`desktop_pet/motion_cfg.py`、`desktop_pet/expr_state.py`（六态与别名表）、
摆动线全部文件、`tag v0.1`、任何 `.pt/.pth` / `source/custom` / `assets/custom` 入库。

---

## 3. 补丁顺序（每步独立可绿，不做「代码先行、测试后补」）

| 步 | 内容 | 预期验证 |
| --- | --- | --- |
| **P0** | 规范文字层：Step 0 / C-U5 / C-U10 / Step 1 pending —— 本文即落地，**零代码 diff** | 无 |
| **P1** | `completers/base.py` + `registry.py` + `upgrade_core.py` 的扫描/快照/闸门骨架 + `test_upgrade_core.py` 骨架 | `pytest -q` ≥ pre-wizard 93（当前 108） |
| **P2** | `mask.from_ellipse` 四段式 + 测试 | ≥ P1 计数 |
| **P3** | `mask.domain_hint` 四段式（只读补全器）+ 测试 | ≥ P2 计数 |
| **P4** | `mouth.open` 四段式 + 测试 —— **仅 B1 答复后启动** | ≥ P3 计数 |
| **P5** | `upgrade_wizard.py` 骨架：注入 → 6 步 → 单入口 → Issue 纯展示 | 离屏人工冒烟；`pytest` 不回退 |
| **P6** | C-U 断言补齐 + `test_expr_states_invariant.py` + 五包 CI 复核 | `pytest -q` ≥ pre-wizard 93（当前 108），CI 绿 |

落地状态：P1~P6 已全部完成（P4 按 B1 裁定以数据槽形态落地）；测试统一放在
`tests/test_upgrade_wizard.py`，不新增 Qt 依赖。

规则：

- 每个补全器**单独一个 commit**，四段式一次走完；不合并成「一批补全器」。
- 任何一步失败，回滚该步；不得把红状态留给下一步。
- 不做 rebase、不强推；后续修正单独 commit（沿用本轮 `e0dd184 → 1d78d15` 的处理方式）。

---

## 4. C-U 断言落点

| 编号 | 断言 | 落点 |
| --- | --- | --- |
| C-U1 | `propose()` 前后工作区文件 mtime / 字节全不变 | `test_completers.py::test_propose_is_pure`（每补全器各一条） |
| C-U2 | 整体否决后 manifest 与所有层逐字节等于 before | `test_upgrade_core.py::test_reject_restores_bytes` |
| C-U3 | 落盘后 `validate_manifest` 通过，`eye_level` / `eyes` 索引不左移（复用 C8c） | `test_upgrade_core.py` |
| C-U4 | 原图 `base` 逐像素等于 before；包围盒外逐比特（默认法线 / custom 轴各一条） | `test_completers.py` + 复用 `test_sway_fields.py` |
| C-U5 | 只产 `allowed` 提案文本；任何写运行时词表路径判失败 | `test_upgrade_core.py::test_allowed_is_proposal_only` |
| C-U6 | `mask=null` 的 part 不因任何补全器进入 active（复用 C8b/C8d） | `test_upgrade_core.py` |
| C-U7 | L3 返回含 `..` / 绝对路径的 `ChangeSet` → 拒绝 + 可读错误，不写盘 | `test_upgrade_core.py::test_rejects_parent_or_absolute_paths` |
| C-U8 | 落盘失败 → 整体回滚，`pytest -q` 不低于上一基线（当前 108） | `test_upgrade_core.py::test_rollback_on_write_failure` |
| C-U9 | 调参类只写 JSON，mask PNG mtime 不变（复用 T-S2） | 复用 `test_sway_fields.py`，不新增 |
| C-U10 | `effects.enabled` / `action_bridge` 前后值快照逐项相等；无新增顶层配置键 | `test_upgrade_core.py::test_config_snapshot_unchanged` |
| 新增 A | 目录名 ≠ manifest `sku` → 判失败且不纠偏 | `test_upgrade_core.py::test_sku_dir_mismatch_fails_without_fixup` |
| 新增 B | `STATES == ('normal','blink','half','surprise','wink_l','wink_r')` 且 `apply('smile')` 解析到 `half` | `test_expr_states_invariant.py` |

> C-U1~C-U10 不删不并；新增 A/B 只加编号，不改旧断言。
> 交付验收以裁定文档的 C-U1~C-U15 为准；本表是早期编号，与前者不冲突。

---

## 5. 落盘与安全闸门（实现细节，统一入口）

1. 补全器 `commit()` 只返回 `ChangeSet`：`{相对路径: bytes}` + `{manifest 字段路径: 新值}`；
2. `upgrade_core` 统一校验：目标路径必须落在 `desktop_pet/assets/<sku>/` 内，拒绝 `..`、绝对路径、符号链接逃逸；
3. 合并后的 manifest 先走 `read_manifest` 语义 + `validate_manifest`，**通过才写**；
4. 写盘顺序：先写覆盖层 PNG（临时文件 + `os.replace`），再写 manifest；任一步失败按快照回滚；
5. 原图 `base` 与现有 mask 不动；不覆盖、不改像素；
6. L3 加载前列出模块清单并单次确认；开启状态不持久化为「永久允许」。

---

## 6. 回归步骤

```bash
cd /home/wxx/my_project/Desktop-Pet-Cutter-Generator
python3 -m pytest -q                     # pre-wizard 93 是下限；当前 108，以实际输出为准
QT_QPA_PLATFORM=offscreen python3 desktop_pet/ui_app.py   # 手动：入口 → Step0~5
git status --porcelain                   # 无权重 / source·assets/custom / mask 产物
```

人工冒烟必看：

1. Step 1 清单里 `smile` 显示 `v0.1.1 / pending`，且不可勾选；
2. 任一补全器「整体否决」后，`git status` 与文件 mtime 均无变化；
3. 落盘后 `manifest` 字节 diff 仅包含预期字段，`base` 像素不变；
4. L3 关闭时，放一个 `user_*.py` 不会被加载；
5. 关闭向导后，主窗口引擎仍在（未被 dispose / 重建）。

---

## 7. MUST NOT（与规范一致，照抄执行）

1. 不做版本更新器、不联网、不拉权重；`.pt/.pth` 不回仓；
2. 不进 CI / 桌宠运行时依赖（仍 5 包）；
3. 不覆盖原图、不改 `base`；
4. 不自动拍板、不批量自动落盘；
5. 不绕过 `validate_manifest`、不新增第二份状态源；
6. 不替用户判定「哪只是真眼」；
7. 不让模型生成的可执行代码进入 L3；
8. 不碰 `arbiter` / `render_frame` / 摆动线 / `tag v0.1`；
9. 不把配置期 AI 候选混进本轮；
10. 产物版权随原图，遵守 `docs/ASSETS.md`。
