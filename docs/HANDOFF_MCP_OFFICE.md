# HANDOFF：MCP / 办公事件桥（边界文档）

> **v0.2.2 已实现并随本轮 push。** 本文是边界真源：Q2 / Q4 / Q4a 已拍板，
> Q1a 降级为「启动参数 + 配置差异」，不再阻塞开工；实测记录见 §10。
> 实现前基线 = `v0.2.1` / `55ca8fe`。

## 0. 范围与三档

想做的：IDE / 外部任务事件 → 桌宠气泡 + 表情 → 人工确认。
不做的：自动执行办公动作、审批自动通过、公网常驻、多 Agent 讨论、
外部 Agent 直接改 manifest。

| 档 | 形态 | 排期 | 显式授权 |
|---|---|---|---|
| A | 本地 stdio MCP + 只读/propose 工具，默认关 | 可收成 `v0.2.2` 小轮 | ❌（比 B-2 轻，不动状态表） |
| B | `127.0.0.1` HTTP 适配层 | 同上，但多一个授权点 | ⚠️ 默认关 + 只绑 loopback |
| C | 公网 / 云 API、自动执行、多 Agent 讨论 | `v0.3` | ✅ 单独边界文档 → 审 → 授权 |

结论落点只有两种：**候选** 或 **校验门**。不能是第二份状态源。

## 1. 读码事实（基线 `55ca8fe`）

| # | 事实 | 位置 |
|---|---|---|
| F1 | Agent 默认关：`enabled is True` 且 `command` 非空才起子进程 | `agent_bridge.py:31-33` |
| F2 | 一次请求 = 一行 JSON 进、一行 JSON 出；stdout 上限 64KB | `agent_bridge.py:49-53` |
| F3 | 回复字段只收 `text/emotion/action/mood_hint/remember`，其余丢弃 | `agent_bridge.py:56-77` |
| F4 | 失败统一抛 `AgentError`，调用侧静默回 idle | `agent_bridge.py:80-98` |
| F5 | `surface == "agent_stdio"` 时 `stream_chat()` 不走 HTTP | `chat_provider.py:83-93` |
| F6 | 回复落点唯一：`arbiter.request("interaction", "expr"/"bubble", ...)` | `pet_window.py:_chat_on_done` |
| F7 | 优先级 `timeline(3) > interaction(2) > idle(1)`；通道白名单 = `blink/expr/gaze/body/bubble`；未知来源/通道直接拒 | `arbiter.py:21-22, 120-143` |
| F8 | 每帧唯一状态源是 `arbiter.resolve()`；`_render_rgba` / `self.base` 只在重建时烘焙 | `pet_window.py:_update_state / _pick_frame` |
| F9 | memory 只由显式触发写入，按 SKU、tmp + `os.replace`、0600、不喂词表 | `memory_store.py` |
| F10 | TTS/ASR 只调外部命令，默认关，不进 CI 五包；TTS/ASR 已走后台线程 | `voice_io.py` / `chat_worker.py` |

**结论**：W6 已经把「独立进程 + 窄协议 + 触发源」做完；MCP 不是从零做，
只是把 stdin/stdout 这一层换成 JSON-RPC，并把工具面收窄。

## 2. Q1 协议形态（待拍板）

推荐：**本地 stdio MCP，默认关**；`127.0.0.1` HTTP 需显式授权且只绑
loopback；不监听 `0.0.0.0`，不做公网 / 云转发。

### Q1a 方向必须先定

同样是 stdio，方向不同，代码落点完全不同：

| 子形态 | 谁拉起谁 | 现有可复用 |
|---|---|---|
| A1 桌宠作为 MCP Client | 桌宠按 `agent.command` 拉起外部进程（现状 `agent_stdio`） | `agent_bridge.respond()` 整套 |
| A2 桌宠作为 MCP Server | TRAE 拉起桌宠侧 MCP 进程，桌宠被动收请求 | 需要新的 stdio 循环 + 生命周期 |

**未定项**：本机 TRAE 实际走 MCP 还是插件 HTTP、由谁发起连接，
以源码 / 实测为准；本文不先替它选。

验收口径（二选一都适用）：

- 默认关时零子进程、零 socket；
- 开启后 `ss -ltn` 无新增监听（stdio 形态必须无）；
- HTTP 形态只出现 `127.0.0.1`，不得 `0.0.0.0` / `::`。

**已拍板（Q1a 降级）**：协议层按方向无关的 JSONL 写成，收到 `tools/list`
回白名单、收到 `tools/call` 只处理只读 + propose；TRAE 是拉起桌宠还是被
桌宠接入，只影响启动参数与配置示例，不影响 adapter。本机实测并入
`v0.2.2` 的 offscreen 冒烟（§10），不再单列一轮。

## 3. Q2 工具白名单（已拍板）

只暴露「只读 + 候选」，不暴露落盘与执行：

| 工具 | 入参 | 出参 | 副作用 |
|---|---|---|---|
| `get_health` | 无 | 开关状态 / SKU / 版本 | 无 |
| `get_allowed` | 无 | `derive_allowed()` 当前结果 | 无 |
| `get_manifest_item` | `stem`（可选） | 单个 item 的白名单字段 | 只读、路径锁 SKU 目录 |
| `propose_candidate` | 有限字段 | 候选摘要 + `validate_manifest` 结果 | **不落盘**、默认零勾选 |
| `notify_bubble` | `text` / `emotion` / `mood_hint` | 是否被 arbiter 接受 | 只写 `bubble` / `expr` 通道 |

明确不暴露：`commit_changes`、任意文件读写、`exec` / shell、动作分发、
memory 写入、审批通过。L3 用户脚本边界（只认四函数、不 exec 顶层、
路径锁死、LLM 生成的 Python 不进链）不因 MCP 放开。

字段规则：

- `text` 截断 200 / 气泡显示 30；非字符串丢弃；
- `emotion` 只接受当前词表内的值，越界归 `normal`（复用 `clamp()`）；
- `mood_hint` 只接受封闭覆盖层键，未知丢弃；
- 路径类参数一律整体过现有路径守卫，不在拼接处绕过；
- 未知工具名 / 未知字段 → 拒绝或忽略，不回落成写操作。

## 4. Q3 输出落点（不可协商）

- 唯一入口：`arbiter.request("interaction", "bubble"/"expr", ...)`；
- **不加** `"agent": 2`（已否决）；不新增状态源；不改
  `_render_rgba` / `self.base`；不写 manifest；
- 不把聊天全文 / 任务内容写进 memory（`remember` 也不经 MCP 写入）；
- 不可用 / 超时 / 乱码 / 被 kill → 桌宠不黑、回 idle（复用 W6 三节点）。

## 5. Q4 办公最小闭环（推荐版）

```text
外部任务事件 → notify_bubble(text, emotion?) → 气泡 + 可选表情
             → 用户在桌宠上点「知道了 / 确认」→ 关闭气泡
```

第一版只做**单向播报 + 本地确认**，不阻塞外部、不回传审批结果。
**Q4a 已拍板：第一版不回传。** 一回传就会长出「外部按确认结果再发一条」
的反馈回路，直接逼近多 Agent 讨论的 v0.3 线；单向即可，不留半截回路。

不做：自动执行、审批自动通过、后台静默轮询（心跳默认否）、
把任务内容写进长期记忆。默认零勾选 / 否决优先延伸到 MCP。

一个可验收例子：IDE 里任务跑完 → MCP 发 notify → 桌宠气泡
「构建过了 / 3 个测试挂了」+ 对应表情 → 用户点一下才继续。

## 6. 断言清单（v0.2.2 已落地）

Qt-free（CI 五包真跑）：

1. 默认关：零子进程、零 socket、零网络；
2. 工具越界：未知工具 / 未知字段 / 写盘类工具 → 拒绝；
3. 未知 `emotion` → `normal`；未知 `mood_hint` → 丢弃；
4. 恶意字段（`..` / 绝对路径 / 超长 / 非字符串）→ 丢弃且不写盘；
5. 进程被 kill / 超时 → 回 idle 且不黑（W6 三节点不回退）；
6. notify 只产生 `bubble` / `expr` 请求；manifest 字节 + mtime 不变；
7. `_render_rgba` 逐比特不变；保护 7 项自 `55ca8fe` 零 diff；
8. 无新 Python 依赖；`--collect-only -q` 能核到新节点；skip 仍 1。

Qt 冒烟（CI skip）：气泡 + 表情 + 确认按钮实际出现。

## 7. 实施顺序（v0.2.2 已按此执行）

1. 方向无关的 stdio 循环：`mcp_stdio.py`（白名单 + JSONL + 默认关，无网络 socket）；
2. 桌宠侧 adapter：`mcp_office.OfficeHost` 只翻译协议，注入 notify；
3. Qt-free 测试 `test_mcp_*` + GUI 冒烟（同一 GUI 文件，skip 仍 1）；
4. 本机实测并入 offscreen 冒烟（§10），不单列一轮；
5. 本地 feat / test / docs 分开 commit → 一次 push → annotated `v0.2.2`。

`v0.1` / `v0.2` / `v0.2.1` 均不动、不重打。

## 8. 停线

只有要求动 `expr_state.py` / STATES / 别名表 / `anim.py` / `arbiter.py` /
`manifest_io.py` / schema / workflow / tag，或公网常驻、默认联网、
第二份状态源、把已否决 7 项捡回来时，才停下先列冲突。

## 9. 本机实测记录（offscreen，2026-11-20）

启动方式：

```bash
printf '{"id":1,"method":"tools/list"}\n' \
  | QT_QPA_PLATFORM=offscreen python3 desktop_pet/pet_window.py \
      --asset desktop_pet/assets/custom/bgy_001_pet.png --mcp-stdio
```

- `tools/list` 回五个白名单工具；`get_health` 回 `protocol` / `sku` /
  开关状态；`notify_bubble` 回 `delivered: true`；
- `stdout` 只有协议行；`[manifest]` / `[mcp]` 等日志全在 `stderr`；
- 无监听端口、无网络 socket（Qt 事件循环自用的未命名 Unix socketpair 不计）；
  协议层无第三方依赖，干净 venv 五包可收集；
- 方向差异未在本机验证（TRAE 本地未接入），只确认消息体与方向无关。
- 进程级真实启动 / stdout 纯净 / 无监听端口 / UI 未执行的完整记录见
  `docs/HANDOFF_TRAE_MCP_SMOKE.md`。

## 10. 状态

| 项 | 状态 |
|---|---|
| 本文（边界文档） | ✅ `8287c53`，随 v0.2.2 一起 push |
| Q1 / Q1a 协议形态 | ✅ 方向无关 stdio；Q1a 降级为启动参数差异，实测见 §9 |
| Q2 工具白名单 | ✅ 五个，只读 + propose，无 commit / 写盘 / exec |
| Q3 输出落点 | ✅ `arbiter.request("interaction", ...)` 唯一入口 |
| Q4 最小闭环 / Q4a 回传 | ✅ 单向播报 + 本地收起；第一版不回传 |
| `v0.2.2` 实现 | ✅ `9981be8`，tag `v0.2.2` = `05ccc20` |
