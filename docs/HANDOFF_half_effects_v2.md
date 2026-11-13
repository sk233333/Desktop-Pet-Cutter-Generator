# 交接文档增量修订 v2（半眼 + 待机效果）

> 本文只是对 `桌宠_半眼与效果扩展_交接说明.md` 的**增量修订**，未重申的部分以原文档为准。
> 修订范围：3.4 / 3.6 / 第 7 节 MUST NOT / 第 8 节验收 / 第 9 节待确认项。

---

## 1. 3.4 契约段增补

```python
class ExprBank:
    @classmethod
    def build(cls, L, socket, mouth_dir=None,
              half_drop=0.30, half_sag=0.10) -> ExprBank
```

- **没有 `bgr` 参数**：肤色过滤只用 HSV 的 `S/V`，两者与 RGB/BGR 通道顺序无关，
  调用方不需要关心通道序。`build(L, s)` / `build(L, s, path)` /
  `build(L, s, mouth_dir=path, half_drop=0.2)` 三种写法都有回归覆盖。
- `half_drop` ∈ 0.24~0.34（**硬上限 0.35**），`half_sag` ∈ 0.06~0.14。
- 半眼 = `_lid_shutter()` 遮挡：`ball_rgba` **零缩放**，`L['lash']` **只平移不缩放**。

### 1.1 新增内部函数 `_iris_zoom(L, region, s=1.10)`

`surprise` 不再缩放整只眼球，改为**只放大 `iris` 贴片**：

```
disk（眼白盘，原样） → iris（以眶中心为原点 ×1.10） → spec（高光，原样） → lash（原样）
```

- 眼白盘宽度、高光形状、上眼睑位置在 `surprise` 下逐像素不变（B12）。
- 放大后的虹膜用 `region` 裁回本眼，不会溢出到眼睑或另一只眼。

### 1.2 闭眼线宽自适应

`_closed_line()` 的线宽不再固定 2px：

```python
lash_m = (L['lash'][..., 3] > 128) & region & (socket > 0)   # 只统计眶内
thick  = lash_m[:, cx-2:cx+3].any(axis=1).sum()              # 中列上的 lash 厚度
lw     = clip(round(thick * 0.8), 2, 12)
```

- 统计先 `& (socket > 0)`，排除眉毛、眼影等眶外深色块把线宽带粗。
- `lash` 完全缺失时退回固定 2px。

---

## 2. 3.6 三处遗留 → 已落地

| 原遗留 | 现状 |
|---|---|
| `surprise` 仍缩放整只眼球 | **已改**：`_iris_zoom` 只放大虹膜，`lash`/眼白/高光不动（B12） |
| `blink` 弧线固定 2px | **已改**：线宽按 `lash` 厚度自适应，且只统计眶内（B11） |
| `half` 是否移出 `idle_pool` | **保留**。遮挡式半眼已不产生失真，移出反而减少变化 |

---

## 3. 第 7 节 MUST NOT 新增

- ❌ `surprise` 禁止缩放整只眼球（`disk/spec/lash` 必须原地不动，只允许放大 `iris`）
- ❌ 闭眼线宽统计禁止包含眶外像素（必须 `& (socket > 0)`）

---

## 4. 第 8 节验收新增 B9–B12

| 编号 | 断言 | 期望 |
|---|---|---|
| B9 | `pytest tests -q` | 全绿；合成眼 fixture 在 `tests/conftest.py`，CI 可收集 |
| B10 | `map_eye_masks(mask, crop, (w,h))` 的 bbox | 与「裁边 + 缩放」换算一致（防素材坐标偏移） |
| B11 | `blink` 闭眼线在中列的厚度 | 4~12px（跟随 `lash`），且眶外眉毛不计入 |
| B12 | `surprise` 下 `lash` 色块与 normal | 逐像素相同；眼白盘宽度不变 |
| B13 | 3 连通域（含配饰装饰眼）下，`blink/half/surprise/wink_l/wink_r` 在装饰眼坐标 | 像素与原图**逐像素相同**；`report` 里必须有记录 |

> B1–B8 继续沿用原文档。B9–B12 已在 `tests/test_half_and_effects.py` 落地。

---

## 5. 装饰眼（第三连通域）处理

症状：socket 里混进配饰上的「装饰眼」后，所有非 normal 表情都会在配饰位置
铺一块浅色圆斑（`opaque()` 先按整个 socket 铺 cover 皮肤底）。

修复分三层：

1. **数据层**：清掉多余 mask 通道里的那只眼，manifest 的 `eyes` / `eye_level` /
   `eyes_mask` 同步删条目。本仓库没有离线烘焙的 `expr.png` / `layers`，
   贴片是运行时由 `_eyes.png` 现算的，所以清标注即可，无需重跑烘焙。
2. **代码层（防御）**：`_halves()` 只保留**面积最大的两个**域当左右眼；
   另外若两者 `dy > max(0.8*dx + 2, 0.5*眼高)`（歪头允许 7~8px 高差，
   但差出一只眼的高度就是配饰），丢掉面积小的那个并写进 `report`。
   `cover` / wink 的底图都只铺在 `halves` 并集上，不再按整个 socket 铺。
3. **烘焙校验**：`build_one()` 统计每只 mask 的连通域数，非 1 时打印
   `[warn] ... 眼眶连通域`，并写进 manifest 的 `eye_domains`。

回归：`tests/test_half_and_effects.py::test_third_socket_is_ignored`（B13）。



| # | 问题 | 结论 |
|---|---|---|
| 1 | `surprise` 改成不形变？ | 是，只放大虹膜（`_iris_zoom`）；没有按 `shaded` 关闭 |
| 2 | `blink` 线宽自适应？ | 是，按 `lash` 厚度，clip 到 2~12px |
| 3 | `half` 移出 `idle_pool`？ | 否，保留（遮挡式已可用） |
| 4 | `EffectDirector` 落盘？表挂哪层？ | 已落盘：`desktop_pet/effect_director.py` + `effect_table.json`（全局）；逐素材用 manifest 的 `effects.{enabled[], mood}` 覆盖，缺省回落全局 |
| 5 | 有无 `body.apply()` / `gaze_driver()` / `bubble.show()`？ | 没有；本轮只接已有通道——表情 `ExprState.set()`、视线 `_update_gaze()` 的偏移、气泡 `PetWindow._bubble()`，不新造物理层 |
| 6 | 有无 Qt 帧循环与鼠标换算？ | 有（`PetWindow.tick()` 30fps，`_gaze_target()` 已做屏幕→素材像素换算） |
| 7 | 目标素材是手办还是插画？ | 两者都只做「零素材」档；`shaded` 素材不做星星眼/爱心等装饰贴片 |

---

## 7. 效果扩展落地范围（A 档 3.5 / 6）

已落地（零新素材、零新依赖）：

1. mood 权重抽取（`calm / curious / sleepy`，11 个效果 id）
2. 视线四模式中的两种：`glance`（瞥一眼）/ 归零，其余仍走鼠标跟随
3. 气泡符号通道（`♪ / ? / ! / … / Zzz`）
4. 组合约束：同一效果不连播；交互后 3s 内不打断；mood 切换 20~40s 冷却，且刚交互过不换 mood
5. 逐素材开关（`effects.enabled` / `effects.mood`）

未实现（保持原方案，不假装完成）：

- 呼吸 / 摇摆的周期与幅度随机化
- 4 种眨眼时序（连眨 / 慢眨 / 三连抖 / 正常）
- 组合规则里的 `wink→gx 偏左`、`surprise→呼吸停一拍`（依赖尚不存在的身体通道）

`idle_ms` 未缩短，最短仍是 2.5s。
