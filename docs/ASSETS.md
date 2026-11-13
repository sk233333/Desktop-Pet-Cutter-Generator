# 素材（assets）说明

仓库**不包含**手办 / 盲盒 / 3D 渲染素材。clone 下来只能跑通流程，看不到具体桌宠，这是有意为之。

## 为什么不入库

- **版权**：素材不是本项目自有美术，入库等于打包再分发，与 MIT 许可无关。
- **体积**：单张主图可达 3.3MB，入库会明显拖慢 clone，直接损害「三条命令即跑」。

## 目录约定

```
desktop_pet/source/custom/<hash>_pet.png        原始立绘
desktop_pet/source/custom/<hash>_pet_mask.png    SAM2 抠图 mask
desktop_pet/source/custom/<hash>_pet_eyes.json   眼眶标注（椭圆 + 通道）
desktop_pet/assets/custom/<hash>_pet_*.png       生成产物（_eyes.png 等），已被 ignore
```

`desktop_pet/source/custom/` 与 `desktop_pet/assets/` 都在 `.gitignore` 里。仓库内仍保留少量早期跟踪的素材用于历史比对，新增素材不会再被 `git add` 意外带进来。

## 自备素材

1. 用 `desktop_pet/launch.py` 走五步交互（框取 → 选点 → 预览 → 眼眶 → 发缕）。
2. 眼眶标注写成 `<hash>_pet_eyes.json`；`--cli` 只写 JSON，不落 `_eyes.png`，需要的话用 `cutout_to_pet.save_eye_masks()` 生成 R/G 双通道 socket。
3. 生成产物落在 `desktop_pet/assets/custom/`，结构见 `docs/HANDOFF_PROJECT.md`。

## 回归测试

测试**不读真实素材**：`tests/conftest.py` 用合成眼睛（已知 socket / lash / 虹膜结构）验证形态学断言。需要真实素材的回归请走外部 fixtures，不要把素材提交进仓库。

## 已知限制

- 缺 `_eyes.png` 的素材在 manifest v1 下仍按全部检测到的眼做眨眼/视线；迁移到 v2 后会被过滤掉，见 `docs/RELEASE_v0.1.md`。
- 装饰眼 / 挂件眼需要人工确认后单独标注，不要按「前 N 只」批量补 socket。
