# 第三方组件与许可声明

本仓库的 **MIT 许可只覆盖本项目自己写的源码**（`desktop_pet/`、`tools/`、`tests/`、`Punctuation_selection_of_images/*.py` 等）。下面三类内容不适用 MIT。

## 一、运行时依赖（由使用者自行安装，遵守各自条款）

| 组件 | 许可 | 说明 |
|---|---|---|
| PyQt6 | GPL-3.0 或商业许可（Riverbank） | 若你的分发方式与 GPL 不兼容，需向 Riverbank 购买商业许可 |
| OpenCV（`opencv-python`） | Apache-2.0 | |
| NumPy | BSD-3-Clause | |
| Pillow | MIT-CMU（HPND） | |
| matplotlib | PSF 系许可 | 仅 `launch.py` 标注流程使用 |
| pytest | MIT | 仅开发/CI |

这些组件不随本仓库分发，安装即表示你接受其许可；把它们与本项目一起打包分发时，需同时满足各自的署名与再分发要求。

## 二、可选链路与模型权重（不随仓库分发）

以下内容属于**可选**能力，仓库不打包，需要时自行下载并遵守上游许可：

- **SAM 2.1 权重**（`sam2.1_hiera_small.pt`）：以 Meta 上游仓库许可为准，使用前请自行核对是否允许你的使用场景。
- **YOLOv8 权重**（`yolov8n.pt`）：Ultralytics，**AGPL-3.0 或商业许可**。AGPL 具有传染性，若你把包含该权重的程序对外提供服务或分发，需遵守 AGPL 或购买商业许可。
- **RIFE / 中间帧模型、词表等**：如后续接入，同样遵循上游许可，本项目不做转授权。

`Punctuation_selection_of_images/image_assist.py` 在使用时会尝试下载缺失的权重；是否运行该链路由使用者决定。

> 历史说明：tag `v0.1` 的树里仍包含 `sam2.1_hiera_small.pt` 与 `yolov8n.pt`；之后的提交已将其移出索引。它们同样**不受 MIT 约束**，使用须遵守上表条款。

## 三、图像素材（用户自备，用户自负版权）

仓库不含手办 / 盲盒 / 3D 渲染素材，包括源图、mask、眼眶标注与生成产物。clone 后相关目录为空，需自行创建并放入素材，见 [docs/ASSETS.md](ASSETS.md)。

`desktop_pet/ui_assets/chevron_down.png`（24×24，8KB）是本项目 UI 图标；如你的副本来自其他图标库，请按其许可署名或替换。

## 四、上游参考

项目设计与部分约定参考了 Anime2.5DRig、Petra、PachiPakuGen 等项目的公开思路，这些项目多为 MIT 系许可；本仓库未复制其代码。
