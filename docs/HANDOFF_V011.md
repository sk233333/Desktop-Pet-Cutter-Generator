# v0.1.1 清单（排期文档，不产代码）

> 本文是排期与约束，**不产代码**：只做排期与边界登记，不改状态表、不升 schema。
> 起草基线：`origin/main` 以 `git log` 为准；测试数以 `pytest -q` 为准（本文不写死）。
> `tag v0.1` = `fe5d76f` → commit `b03cdb7`，不动。
> 前置：② 嘴型 overlay T-M1 ~ T-M6 已结案，见 `docs/HANDOFF_MOUTH_OVERLAY.md`。

## 0. 三类性质，不平铺成同级任务

| 档 | 内容性质 | 是否占 v0.1.1 实现位 |
|---|---|---|
| A 人工作业 | 真素材 + 人眼 | 否，与代码并行 |
| B 先定边界 | 涉及状态表 / 新链路，先出边界文档与断言 | 否，先定边界再谈实现 |
| C 工程维护 | 小、可批量、每项单 commit | 是 |
| D 全程约束 | 「不许破」，不是任务 | — |
| 已否决 | 不做，也不要被下一个人捡回来 | — |
| 外部配额（可选） | 平台侧，非发版阻塞 | 否 |

## 1. A 档：人工作业（零代码）

| # | 事项 | 完成标准 | 状态 |
|---|---|---|---|
| A-1 | Issue #1：`3b3d…` / `d37e…` 逐张点清角色真眼，再补 socket | `parts[].eye_index` 全指向角色真眼；`active_eyes` 不再为 0；挂件眼不参与眨眼/视线。**禁止按「前 4 只」批量** | 待人工 |
| A-2 | Issue #2：三张新补 mask 的窄侧位移看图（`3c1e…` / `9991…` / `da90…_preview_pet`） | 「往窄侧移到上限时虹膜不压到上睑线」；不通过则**逐 SKU 降 `amp`**，不改全局 | 待人工 |
| A-3 | 25° 阈值标定：`SWAY_AXIS_PARALLEL_DEG` | 用垂发 / 衣摆各一张真实素材标定，记录素材与结论；结论进 `docs/HANDOFF_SWAY.md` | 待人工 |
| A-4 | `desktop_pet/ui_assets/chevron_down.png` 自绘替换（24×24） | 去掉 `THIRD_PARTY_NOTICES.md` 中「如来自其他图标库」的不确定项 | 待人工 |

## 2. B 档：先定边界、不立刻开代码

> 每条都必须单独立边界文档、单独立断言；**不按收尾估工**。
> B-1 与 B-2 必须拆开：前者产数据，后者动状态表。

| # | 事项 | 先定的边界 | 状态 |
|---|---|---|---|
| B-1 | 配置期补微笑（产贴片 / 产数据） | 只产数据，**不改 `ExprBank.STATES`、不加 `smile` 状态**；产出格式与存放位置先定；不改变运行时词表（C-M13 同口径） | 待拍板 |
| B-2 | `smile` 独立状态 | 动 `STATES` + `smile → half` 别名表，直接撞 C-A8 / C-M13；需先单独出边界文档并显式授权，方可改动 `ExprBank.STATES` 与 `smile → half` 别名表；在此之前一律不动状态表，**不并入 B-1** | 待拍板 |
| B-3 | 生成式候选 provider（配置期） | 默认零网络；候选只在配置期产出，不进运行时取帧；写 manifest 前必须过 `validate_manifest` | 待拍板 |
| B-4 | 说话驱动嘴型切档（接 A1 `chunk`） | 静态常驻 → 动态切档是另一条功能；只消费 A1 `chunk` 信号，不改 `arbiter` 通道矩阵；失败回静态 | 待拍板 |

## 3. C 档：工程维护（小、可批量，每项单 commit）

| # | 事项 | 验收 / 说明 | 状态 |
|---|---|---|---|
| C-1 | CI 固定 `ubuntu-24.04` + actions 升版 | **仅在确证是 runner 镜像问题后做**；未确证前不动 workflow（见 §7） | 待确证 |
| C-2 | matplotlib 懒加载 | 起草时 `desktop_pet/launch.py` 有 9 处引用、3 处模块级 import；改前重新 grep 按实际值 | 未实现 |
| C-3 | payload 组装后小写 `json` 断言 | A1 已知增补；只补断言，不改 provider 行为 | 未实现 |
| C-4 | `dir: null` 显式写 vs 省略 key 等价断言 | 摆动参数 sidecar / manifest 各一条 | 未实现 |
| C-5 | `bias` UI 暴露 | 当前只到数据 + 渲染；补 UI 不改变默认值 | 未实现 |
| C-6 | `test_pet_window_mouth.py` 唯一 skip 的处置 | 评估无 PyQt6 的 headless 拆分；默认接受该 skip，**不为测试新增 CI 依赖** | ✅ 已完成（仅补 skip 理由） |

> **C-1 确证条件**：某次 push 的 run 变红、且同 commit 干净 venv 五包全绿 → 判为 runner 镜像/依赖，再动 C-1；否则只记录 annotation，不改 workflow。

## 4. D 档：全程约束（「不许破」，不是任务）

- 默认零网络、零权重；clone 体积维持 40M 量级。
- `tag v0.1` 不动。
- `docs/images/` 是唯一未跟踪目录，不入库。
- CI 以干净 venv 五包 + `MPLBACKEND=Agg` 复现。
- `pytest | tee` 的 `pipefail` 与 `$GITHUB_STEP_SUMMARY` 不回退。
- `arbiter.py`、`anim.py`、`ExprBank.STATES`、`smile → half` 别名表零字节。
- 每步单独可回滚；纯 docs 与 feat 分开推。

## 5. 已否决（不做，不要捡回来）

| 事项 | 原因 |
|---|---|
| `partial_text` 二次绘制跳过 | 上轮明确「别为此加状态」；否决项，不是待办 |
| 给 `arbiter` 加 `agent: 2` 源 | 状态源与矩阵已收敛，不新增通道/源 |
| 首期同步 `ui_app.PetPreview` | Q4 已显式接受差异，不写「已统一」 |
| 把模型权重请回仓库 | 历史已改写；权重外置 |
| 未确证镜像问题前预防性改 workflow | 先排查，不预改 |
| 按「前 4 只」批量补 socket | 会把挂件眼当真眼，必须逐张点清 |

## 6. 外部配额（可选）

- GitHub 端 182MB LFS 孤对象：外部配额问题，找 support 清理；**非发版阻塞**，不作为 v0.1.1 任务。单独记录，避免有人反复「清一次」。

## 7. 口径

- 测试数：以 `pytest -q` 的实际输出为准，不写死。
- commit hash：以 `git log` 为准，不写死。
- 编号断言核对：`pytest -q` 通过时只打点，请用 `pytest --collect-only -q` 点名。
- `4e10451` 页面 `failure` 与 run #16 `Success` 的差：按「页面缓存/加载错误 → runner 镜像 → 依赖边界」排查；两条 annotation（Node 20 被 Node 24 强跑、`ubuntu-latest` 迁 Ubuntu 26）照实记录。
- C-1 的 `ubuntu-24.04` 等，确证是镜像问题再做。

## 8. 状态总览

| 项 | 状态 |
|---|---|
| A-1 ~ A-4 人工作业 | 待人工 |
| B-1 ~ B-4 边界 | 待拍板 / 未实现 |
| C-1 镜像相关 | 待确证 |
| C-2 ~ C-6 工程维护 | 未实现 |
| D 全程约束 | 生效 |
| 已否决 | 保持不做 |
| 外部配额 | 可选，非阻塞 |
