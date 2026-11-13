# v0.1 — 单一状态源收敛版

> 发版口径 B：带已知限制发布。发布前需 CI 首次实跑全绿（71 条）。

## 本次做了什么

- 收敛为一份带 `schema_version` 的 manifest：`parts[].eye_index` 指回原始 `eyes`，
  过滤只改「谁参与」不改索引；组号、socket 对位、派生字段全部单一真源。
- 新增仲裁器：时间轴 > 用户交互 > idle，低优先级不得打断高优先级。
- 眨眼帧序列明确为 `frames[0]`（0.55 上睑遮挡过渡）→ `frames[-1]`（全闭）；
  `half:true` 可选插入遮挡式半眼。`dt == hold/2` 归全闭。
- 多眼 / 装饰眼不再被误并进半区；缺 mask 的 part 不进任何通道且可见提示。
- `pytest` 71 条全绿；新增 GitHub Actions（numpy + opencv-headless + pytest，offscreen）。

## 已知限制（v0.1 显式接受，非缺陷）

1. **`3b3d…` / `d37e…` 待人工标注眼眶 mask**。v0.1 发布的 manifest 是 **v1**，
   过滤尚未生效：眨眼按全部检测到的眼运行（3b3d 6 只、d37e 4 只），视线也为
   全部 6/4 只建了几何，因此**挂件/饰品上的眼会跟着眨眼、跟视线**；表情贴片内部
   已按连通域忽略饰品（日志会打「忽略 N 个（疑似配饰）」）。加载时有
   `[manifest] … 请先补 mask 再迁移` 提示，不会静默。
   **迁移到 v2 后若仍未补 mask，这两张的眨眼 / 视线 / 表情会整体关闭。**
2. 三张新补 mask 的素材（`3c1e…` / `9991…` / `da90…_preview_pet`）使用椭圆 socket
   （`rx+1, ry+1`），窄侧位移幅度待人工看图抽查；若虹膜压到上睑线，逐 SKU 调
   `amp`，不动全局公式。
3. 右键单眼 `wink_l/r` 与编辑器预览 blink 贴片，与运行时 `frames[-1]` 存在已知差异，
   已在 `docs/CHANGELOG.md`「已知差异」记录并显式接受（C5b 已关闭）。
4. CI 覆盖**纯逻辑 / manifest / 几何层**；窗口层（帧循环、QPixmap、真实 tick）
   需本地 `QT_QPA_PLATFORM=offscreen` 抽查。**CI 全绿不等于全链路已验证。**
5. 单眼素材 `9991…` 的 `wink_l/r` 退化，用逐素材 `effects.enabled` 关闭即可。
6. `da90…_preview_pet` 的坐标基于 509×508 重裁版；若换回 3.3MB 原图，
   `eyes` 与 mask 需整套重标。

## 升级 / 迁移

- v1 manifest 可直接读（打 warning）；转 v2 用 `tools/migrate_manifest_v1_v2.py`，
  会生成 `.v1.bak` 并打印分组表。`--check` 为纯只读。
- **迁移前请先给 3b3d / d37e 补好 mask**，否则迁移后这两张会彻底停止眨眼。

## 验证

`python3 -m pytest tests -q` → 71 passed；`py_compile` 全绿。
