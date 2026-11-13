# CHANGELOG

按时间倒序。只记会影响观感或数据格式的行为变更；内部重构不列。

## 收敛轮（单一状态源 / 仲裁 / manifest v2）

### 行为变更

- **眨眼默认观感保持不变**：`half:false`（默认）前半段仍是 `make_blink_frames`
  的 0.55 遮挡过渡帧，后半段 cover=1.0 全闭。0.55 帧是**遮挡式**（把上眼睑
  压下来的部分填成肤色），眼球几何零形变，不是压缩式。
- **`half:true` 是新增可选**：只在时间轴 item 显式写 `"half": true` 时，前半段
  改用 `ExprBank.apply(base, "half")`（按真实 socket 遮挡、睑缘复用 lash 层）。
- **全闭眼统一**：运行时时间轴眨眼与 idle 随机眨眼都取 `make_blink_frames`
  的 `frames[-1]`，不再出现两套全闭实现。
- **表情 / 视线 / 气泡统一由 `arbiter` 仲裁**：`timeline(3) > interaction(2) >
  idle(1)`；blink 通道只允许时间轴写；交互后 3 秒内 idle 效果不打断。
- **多眼素材**：`active_eye_parts` 过滤后，`mask=null` 的眼不参与眨眼/视线/表情
  （之前会跟着闭眼）。过滤只改「哪些索引参与」，不改索引本身。

### 数据格式

- manifest 支持 `schema_version: 2`：`parts[]`（`id / eye_index / group /
  eye_level / mask / channel`）与 `timeline.items[]`（`group / t / hold / half`）。
- `eyes / eye_level / eyes_mask / blink` 在 v2 下是**派生视图**，由 parts/timeline
  反推；旧渲染路径不用改，值永远与 parts 一致。
- 组号统一为字符串 `g{n}`；读取层只归一化一次，下游不再判类型。
- 眼球通道：`file_idx = i // 2`，`channel = R / G`（B 恒为高光）。
- 迁移工具：`tools/migrate_manifest_v1_v2.py`（`--check` 纯只读）。

### 已知差异（显式接受，非缺陷，C5b 已关闭）

- 右键「单眼眨一下」的 `wink_l/wink_r` 仍走 `ExprBank` 单眼贴片；它与整帧
  `frames[-1]` 的全闭观感不完全一致（单眼手势本来就不等于全闭）。
- 编辑器预览的 blink 仍用 `ExprBank` 的 blink 贴片，运行时用 `frames[-1]`。
- 单眼素材（如 9991）的 `wink_l/wink_r` 会退化成同一边，需用逐 SKU
  `effects.enabled` 关掉对应效果。

### 测试与 CI 范围

- `pytest tests` 覆盖纯逻辑 / manifest / 几何层（arbiter、manifest_io、anim 的
  纯函数），不依赖 PyQt6。
- 窗口层（帧循环、QPixmap 转换、真实 tick）**不在 pytest 里**，需要本地
  `QT_QPA_PLATFORM=offscreen` 跑真实素材抽查；CI 全绿不代表全链路已验。
- CI：`.github/workflows/tests.yml`（numpy + opencv-headless + pytest）。

### 数据待办

- `3b3d…`（6 只检测眼，其中 2 只在脚下挂件上）与 `d37e…`（4 只，其中 2 只在
  龙头挂件上）：先用标注工具点清真眼再补 mask；补之前 active=0，不眨眼/不跟视线。
  迁移 report 会打 `[pending 需补 mask]`。
- 三张新补 mask 的素材（`3c1e…` / `9991…` / `da90…_preview_pet`）用的是由
  `eyes` 椭圆生成的 socket（rx+1, ry+1），偏宽松；待抽查「往窄侧移到上限时
  虹膜不压到上睑线」，不通过就逐 SKU 降 amp。
- `da90…_preview_pet` 的坐标基于 509×508 重裁版；换回 3.3MB 原图时必须整套
  重标 eyes + mask。
