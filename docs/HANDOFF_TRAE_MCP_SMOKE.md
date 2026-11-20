# HANDOFF：TRAE 本机 MCP 联调记录

> 环境项记录，不改协议层。日期：2026-11-20。
> 结论先说：**进程级 stdio 会话已验证；TRAE UI 内的注册/点击联调未执行**
> （本机没有可脚本化的 MCP 客户端入口，且未擅自改 IDE 配置）。
> 不写成「TRAE 已接入」。

## 1. 本机环境

| 项 | 值 |
|---|---|
| 系统 | Ubuntu 22.04（`/usr/share/appdata/trae-cn.appdata.xml`） |
| IDE | Trae CN 3.4.15（进程 `/usr/share/trae-cn/trae-cn`） |
| CLI | `/usr/share/trae-cn/bin/trae-cn`，只有 diff / merge / goto / window 等参数，**无 MCP 子命令** |
| Python | 3.10.12，本地 PyQt6 可用 |
| 显示 | 探针用 `QT_QPA_PLATFORM=offscreen`，并清掉 `DISPLAY` |
| 工作区 MCP 配置 | 仓库内无 `.trae/mcp.json`；`~/.trae-cn/installed-plugins.json` 只有插件 MCP，没有用户自定义 server |

## 2. 真实启动方式（与 TRAE 配置同一命令）

```bash
python3 desktop_pet/pet_window.py \
  --asset desktop_pet/assets/custom/bgy_001_pet.png \
  --mcp-stdio
```

探针以 `subprocess.Popen` 拉起该命令，用 stdin/stdout 发 JSONL，
等价于 MCP 客户端拉起 server 的那一侧；没有改用测试内的 fake host。

## 3. 会话结果

| 请求 | 回包摘要 |
|---|---|
| `tools/list` | 五个白名单工具：`get_health` / `get_allowed` / `get_manifest_item` / `propose_candidate` / `notify_bubble` |
| `tools/call get_manifest_item` | `stem=bgy_001_pet`，`files=[bgy_001_pet.png, bgy_001_pet.webp]`，`mouth_level=null`（真实 v1 manifest，只回 basename） |
| `tools/call notify_bubble` | `{"delivered": true}`（投递确认，不含用户是否点击） |

- `stdout` 只有三行 JSON，无日志混入；
- `[manifest]` / `[mcp]` / Qt 警告全在 `stderr`；
- 关闭 stdin 后协议线程结束、GUI 继续运行（符合桌宠形态）；客户端
  `terminate()` 后进程正常退出。

## 4. 网络面核对

对运行中的子进程做 `/proc/<pid>/fd` + `/proc/net/{unix,tcp,tcp6}` 核对：

- 1 个**未命名 Unix socketpair**（Qt 事件循环自用），不是监听端口；
- `/proc/net/tcp`、`/proc/net/tcp6` 无该进程条目；
- 没有 `0.0.0.0` / `::` 监听，也没有出站连接。

口径修正：此前文档写「无 socket」，准确说法是**无网络 socket、无监听**；
Qt 内部 socketpair 不算协议通道。

## 5. 未执行项（如实登记）

- 未在 TRAE UI 里添加 `.trae/mcp.json` / 触发一次点击式工具调用；
- 未验证 TRAE 是「拉起桌宠」还是「连接已运行的桌宠」——本机 CLI 无此入口；
- 因此本记录只能证明：**协议层 + 真实进程启动路径可用**，不能证明
  「TRAE 联调完成」。

若要在本机补最后一步（需人工操作，不改仓库代码）：

```json
{
  "mcpServers": {
    "desktop-pet": {
      "command": "python3",
      "args": [
        "/home/wxx/my_project/Desktop-Pet-Cutter-Generator/desktop_pet/pet_window.py",
        "--asset",
        "/home/wxx/my_project/Desktop-Pet-Cutter-Generator/desktop_pet/assets/custom/bgy_001_pet.png",
        "--mcp-stdio"
      ]
    }
  }
}
```

注意：真实联调不要设 `QT_QPA_PLATFORM=offscreen`，否则桌宠窗口不可见。

## 6. 结果

| 项 | 状态 |
|---|---|
| 真实进程启动 + stdio 会话 | ✅ |
| stdout 纯净 / 日志走 stderr | ✅ |
| 无 TCP/TCP6 / 无监听端口 | ✅ |
| 工具白名单与只读字段 | ✅ |
| TRAE UI 注册与点击联调 | ⬜ 未执行（环境限制，非代码缺陷） |
| 协议层 / 代码改动 | 无 |
