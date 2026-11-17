# 摆动功能交接说明（给接手的 AI）

项目：`Desktop-Pet-Cutter-Generator`（PyQt6 + OpenCV，白底手办照片 → 透明 2D 桌宠）。
本文只覆盖「摆动」相关：编辑器的飘动区域标注/逐区域参数、数据落盘链路、运行时桌宠的摆动，以及当前已知问题。
请先读「三、数据链路」和「六、已知问题」再动代码。

---

## 一、涉及的两个「摆动方向」，不要混

同名但是两套东西：

| 名称 | 位置 | 含义 | 落盘 |
| --- | --- | --- | --- |
| 运行时·摆动方向 | 桌宠右键「选择摆动方向…」九宫格 | **整只桌宠**沿箭头方向往返平移；原左右旋转按 cosθ 投影 | `~/.config/desktop_pet/motion.json` 的 `dir` |
| 编辑器·摆动方向 | 右侧「摆动」列表每一行的下拉 | **单块头发/衣物**的摆动轴；`默认` = 沿发丝法线 | `<stem>_hair.json` / `<stem>_cloth.json` 的 `dir` |

两者互不读取。给用户解释或做新功能前，先确认说的是哪一个。

---

## 二、功能清单（当前已实现）

### 1. 编辑器（`desktop_pet/ui_app.py`）

- 右侧竖条从「眼睛 / 头发 / 衣服」合并为「眼睛 / 摆动」，共 6 个按钮；「摆动」图标是两条同向流动曲线（`_rail_icon` 的 `kind="sway"`）。
- 「摆动」页（约 L3309-3375）：
  - 顶部「类型」下拉：`头发（发缕）` / `衣服 / 小物件`，标注与列表都作用于当前类型；
  - 一个按钮「标注飘动区域…（多边形）」→ `_annotate_sway()`（L5225）按类型进入 `_hair_flow` / `_cloth_flow`；
  - 统一列表 `tb_sway` 5 列：`类型 | 区域 | 方向 | 幅度 | 末端 (x, y)`；
  - 「删除选中 / 清空」对两类都生效，按行内 `UserRole=(kind, index)` 定位。
- 逐行参数：
  - 方向：`ClickComboBox`，选项 `默认、→、↗、↑、↖、←、↙、↓、↘`（`_DIR_CHOICES`，L4228 附近）；屏幕角度：0=右、90=上、180=左、270=下。
  - 幅度：`ClickSlider`（不响应滚轮）+ 数值标签，0~3.0×，`_amp_cell()`（L4286）；拖动只更新内存并排队预览，停手 600ms 才落盘（`_queue_sway_save`，L4367）。

### 2. 运行时（`desktop_pet/pet_window.py`）

- 右键「摆动幅度」子菜单：静止 / 小 / 标准 / 大 / 更大 / 夸张 = 0 / 0.5 / 1 / 1.5 / 2 / 3 倍；
  `_apply_motion_scale()`（L738）只改窗口实例，不改共享 `asset.motion`，不写 manifest。
- 右键「选择摆动方向…」：`_DirPad`（L119）九宫格，中间「默认」= 不额外整体移动；
  `_set_motion_dir()`（L753）写入配置；`_sway_angle()`（L759）把旋转角按 cosθ 投影；`_sway_offset()`（L768）产生整体往返位移（幅度 = 高度 1.5% × 幅度档位）。
- 幅度与方向都是**每个窗口实例**独立，但共用一个配置文件，改一只不会实时同步到已打开的其他只。
- 头发/衣物逐区域参数在 `HairField` 里生效：`lim = angle_max * field.amp * wisp.gain`（`step()` L2218 附近）。

### 3. 性能相关的近期修改

- 拖动幅度时不再重建渲染引擎：
  - `HairField.apply_params()`（`anim.py` L2248）：就地改 `gain` / `normal`，权重、距离场、包围盒全部复用；
  - `PetPreview.set_sway_params()`（`ui_app.py` L1205）；
  - 轻量路径 `_apply_sway_preview()`（L4348）：更新缓存引擎后调用 `canvas.refresh_pet()`（L1608）只重画一帧；引擎不存在时才回退完整重建；
  - 预览合并 80ms（`_queue_sway_preview` L4328），落盘合并 600ms。
- 实测：512²、3 块区域，`HairField` 构建 **11.5ms**，`apply_params` **约 0.001ms/次**。
- `swing_all()`（`anim.py` L2278）此前已做过合并重采样与角度缓存：512² ≈ 11ms/帧、1024² ≈ 44ms/帧。

---

## 三、数据链路（改字段时四处都要动）

```
编辑器标注/调参（_cut["hair"] / _cut["cloth"]，整图坐标）
  → launch.save_hair_polys()        source/<sku>/<stem>_hair.json、<stem>_cloth.json + mask PNG
  → cutout_to_pet.load_hair_sidecar()   读 sidecar（裁切坐标）
  → cutout_to_pet.map_hair_wisps()      搬进生成图坐标
  → assets/<sku>/manifest.json 的 hair / cloth 数组
  → anim.hair_from_dicts() → HairWisp(amp, dir) → HairField
```

关键位置：

- `launch.save_hair_polys()`：`desktop_pet/launch.py` L698（写 seed：`{"stem","image_size","wisps":[{mask,pivot,tip,amp?,dir?}]}`；**每次保存都会重写该类型全部 mask PNG**，并删掉多余的旧 PNG）。
- sidecar 读取：`desktop_pet/cutout_to_pet.py` L356。
- manifest 映射：同文件 L420。
- 运行时解析：`desktop_pet/anim.py` L55（`HairWisp`）、L72（`hair_from_dicts`）。
- 编辑器加载 sidecar：`ui_app._edit_wisps`，把 `amp` / `dir` 一并读回字典。

字段语义：

- `amp`：该区域自己的幅度倍率，0~3，缺省 1.0；写盘时若等于 1.0 会省略；
- `dir`：屏幕角度（0=右、90=上、180=左、270=下），缺省/`null` = 沿发丝法线（原行为）；写盘时省略 `null`；
- `bias`：角度偏置（度，-45~45），`angle = A·sin(ωt) + bias`；0 时省略写盘，UI 暂不暴露；
- 运行时最终摆角上限 = `angle_max × HairField.amp（全局） × wisp.gain（逐区域）`，
  最后再受 `MAX_SWAY_SHIFT_PX = 14` 的像素位移上限约束。

兼容性：旧 JSON/manifest 没有这些字段时按 1.0 / 默认方向 / 0 处理；schema 已声明 `$defs.swayWisp`。

覆盖层关系（易误读，单独钉住）：`~/.config/desktop_pet/motion.json` 是**用户调参覆盖层**，
只存运行时整体漂移的 `scale` / `dir`。读时覆盖 manifest / `asset.motion` 的默认值，
仅在用户主动调幅度或方向时写，**不反写 manifest**。两者都有值时运行时以 `motion.json` 为准；
逐区域 `amp` / `dir` / `bias` 仍以 manifest 的 `hair` / `cloth` 数组为唯一真源。

---

## 四、逐区域方向在渲染里的真实含义（重要）

- `HairField.__init__` 用 `tip - pivot` 得到发丝方向 `(ux, uy)`，原法线 `normal = (-uy, ux)`；
  若 `w.dir` 非空，位移轴由 `sway_axis()` 解析，并且 `custom = True`。
- 与丝长夹角 <25° 时，`sway_axis()` 取用户方向在**垂直丝长方向**上的分量（`projected=True`），
  编辑器该行显示 `⊥`；写回的 `dir` 仍是用户所选值，不在数据层改参数。
- 位移仍由二阶弹簧的 `angle` 正弦驱动，`swing_all()` 里按 `normal × angle × 权重` 重采样；
  因此 `dir` 定义的是**往返轴**，不是单向偏置：选「→」= 沿左右轴来回摆，松手时停在哪一侧由相位决定。
- 权重是 `pivot → tip` 方向的 `t**1.4` 渐变。`dir` 与发丝方向越平行，remap 越像沿丝长方向拉长/压缩，而不是弯折。
- `custom = True` 时不再整段跳过内摆保护：`inward_factor()` 用 `normal` 在身体内侧方向上的
  投影重算符号，向内目标摆幅仍为外摆的 60%（外缘 `free` 发丝保持原例外）。

---

## 五、实测与验证（当前基线）

- `python3 -m py_compile desktop_pet/ui_app.py desktop_pet/anim.py desktop_pet/launch.py desktop_pet/cutout_to_pet.py desktop_pet/pet_window.py` 通过。
- `python3 -m pytest -q` → **93 passed**（`tests/test_sway_fields.py` 19 条 + `tests/test_swing_batch.py` 3 条，覆盖 axis 投影 / bias golden /
  内摆 60% / 14px 上限 / sidecar 往返 / manifest 映射与校验 / schema 一致性 / masks_dirty /
  按 SKU 配置）。
- 离屏冒烟（`QT_QPA_PLATFORM=offscreen`）：
  - 合并面板 2 个页签 / 6 个竖条按钮；表格正确显示头发、衣服两行；
  - 拖动条 → `_cut` 字典 → sidecar JSON → `load_hair_sidecar` → `map_hair_wisps` → `hair_from_dicts` 全链路 `amp=2.2` 可传递；
  - `dir=90°` 时 `HairField` 的 `normal=(0,-1)`、`custom=True`；
  - `_apply_sway_preview()` 后仍是同一个引擎实例，`gain=2.5` 立即生效（不再重建）。
- 性能回归（同机同图、3 块区域、`swing_all` 取 7 次中位数，T-S1 之后）：
  512² 构建 12.6ms / 单帧 5.8ms；1024² 构建 22.7ms / 单帧 18.1ms；
  `apply_params` 仍在 0.01ms 以下。
  口径：单帧数值是**渲染内核耗时**，本轮内核未改；HANDOFF 旧记录的
  512² ≈ 11ms / 1024² ≈ 44ms 为 T-S1 之前的数字，非同一脚本复测。
  拖动不卡来自 `apply_params` 就地更新 + 预览 80ms 合并，不是内核提速。

---

## 六、已知问题（按优先级）

> **本轮状态总览（2026-11-17）**
>
> | 条目 | 状态 |
> | --- | --- |
> | P0-1 方向是往返轴 | 文案已收敛（→「摆动轴」/「整体漂移方向」）；`bias` 已落数据与渲染，单向偏置 UI 未暴露 |
> | P0-2 与丝长平行导致拉伸 | **已修**：夹角 <25° 自动取垂直分量，行内 `⊥` 提示，写回 `dir` 不变 |
> | P0-3 自定义轴丢失内摆保护 | **已修**：向内目标摆幅仍为外摆 60%（`inward_factor`） |
> | P0-4 两个「摆动方向」同名 | 已改文案 + tooltip |
> | P1-5 结构性修改整体重建 | 未解决（纯调幅度/方向已不重建） |
> | P1-6 大图逐帧成本 | 未解决（1024² swing ≈ 14~44ms） |
> | P1-7 保存重写全部 mask | **已修**：`masks_dirty=False` 只写 JSON |
> | P1-8 缓存键不含 amp/dir | 未解决（统一入口 `set_sway_params`，风险仍在） |
> | P1-9 行内控件不选中行 | 未解决 |
> | P1-10 3×3 叠加到 9× | **已修**：发梢位移上限 14px |
> | P2-11 无 amp/dir 测试 | **已修**：`tests/test_sway_fields.py`（19 条）+ `test_swing_batch.py`（2 条） |
> | P2-12 schema 未声明字段 | **已修**：`$defs.swayWisp` + validator + 一致性测试 |
> | P2-13 只有 8 档 / 无逐区域风力 | 未做 |
> | P2-14 运行时配置全局单文件 | **已修**：按 SKU；旧格式只读降级 |

### P0：会影响观感或可能穿帮

1. **方向是「往返轴」，不是「往哪边飘」**
   现象：选「→」并不会让发丝偏向右侧，只是沿左右轴来回摆，具体先往哪边取决于正弦相位。
   根因：`angle` 由风的正弦驱动，`dir` 只替换了位移轴。
   建议：若要单向偏置，需要额外引入 `bias`（常值位移或角度偏置），并把 UI 文案从「方向」改成「摆动轴 / 往返轴」，或拆成两个参数。

2. **方向与丝长方向平行时会出现拉长/压缩**
   现象：垂发选「↓/↑」、衣摆选其长度方向时，看起来像变长变短而不是摆动。
   根因：权重沿 `pivot → tip` 渐变，位移也沿同一轴，remap 变成纵向缩放。
   建议：方向接近丝长方向时改用垂直于丝长的轴，或在 UI 上提示/禁用；也可改成旋转式弯曲（位移垂直于丝长，强度由角度控制）。

3. **自定义方向关闭了「往内收」保护**
   现象：发丝向脸/身体内侧摆时可能穿进五官/躯干。
   根因：`custom=True` 跳过 `out_dir` 的半幅启发式（`anim.py` `step()`）。
   现状：已改由 `inward_factor()` 对自定义轴按 `normal` 在身体内侧方向上的投影
   重算符号，向内目标摆幅仍为外摆的 60%；**不提供「允许内摆」开关**。

4. **两个「摆动方向」同名易混淆**
   运行时方向盘控制整只桌宠位移；编辑器列表控制单块区域摆动轴。
   建议：改文案区分（如「整体位移方向」vs「摆动轴」），或在文档/提示里写清楚。

### P1：性能与交互

5. **结构性修改仍会整体重建**
   增删区域、改眼眶、改眨眼时间轴会触发 `_pet_engine` 重建：眨眼贴片（每组 2 个 cover）、视线几何、表情 bank、所有 `HairField` 权重全部重算；大图下是一次明显顿挫。纯调幅度/方向已不再重建。
   建议：如需继续优化，可把「眨眼贴片/表情 bank」按需懒加载，或把 `HairField` 权重缓存到磁盘/内存。

6. **大图逐帧成本偏高**
   `swing_all` 在各区域并集包围盒上做一次 `cv2.remap`：本机 1024² 约 18ms/帧（3 块区域，
   随区域数量 / 面积线性增长）；画布 50ms 定时器 + 拖动时额外 `refresh_pet()`，大图仍可能轻微掉帧。拖动反馈合并为 80ms，视觉上约 12Hz。
   建议：编辑器预览先按显示高度缩放再算位移场，或拖动时降低预览分辨率。

7. **保存元数据会重写全部 mask PNG**
   `save_hair_polys()` 每次都会对每个区域 `cv2.imwrite`，即使只改了 `amp`/`dir`；区域多或裁切图大时，600ms 后的那次保存有一次性顿挫，也会改动文件 mtime。
   建议：拆成「只写 JSON」与「写 mask」两条路径，调参只走 JSON。

8. **`_pet_key` 不含 `amp`/`dir`（有意为之）**
   预览更新完全依赖 `set_sway_params()` 就地改；任何绕过它的代码路径（直接改 `_cut` 后不调预览）都会让画布停在旧参数。
   建议：给参数加一个轻量修订号并纳入 key，或提供统一的 `apply_sway_to_preview()` 入口。

9. **行内控件不触发行选中**
   点拖动条或方向下拉不会选中该行；`_picked_masks()` 依赖 `selectedIndexes()`，所以「删除选中」要先点类型/区域/末端单元格。
   建议：在 cell widget 的鼠标事件里同步 `selectRow(r)`。

10. **参数上限叠加**
    逐区域 0~3× 与运行时「夸张 3×」相乘可达 9×；未做整体钳制，极端参数可能出现明显拉伸/越界。
    建议：运行时或 `HairField` 里对最终位移量做一次上限合并。

### P2：工程/文档

11. **没有自动化测试覆盖 `amp`/`dir`**
    目前只有手动离屏冒烟。建议补：
    - sidecar JSON 往返（含 amp 省略、dir 省略/null）；
    - sidecar → `map_hair_wisps` → manifest 字段保留；
    - `HairField.apply_params()` 对 `gain`、`custom`、`normal` 的就地更新与复位。

12. **manifest schema 未声明 `amp`/`dir`**
    `desktop_pet/manifest.schema.json` 的 hair 项是宽松对象；外部工具看不到字段约束。建议补上属性说明（保持向后兼容，非必填）。

13. **方向只有 8 档**
    没有连续角度输入；也没有逐区域的「风力/相位」设置（`wind_speed` / `amp` / `stiffness` / `damping` 仍是整类共用）。

14. **运行时配置是全局单文件**
    `~/.config/desktop_pet/motion.json` 由最后修改的窗口写入；已打开的其他桌宠不会实时跟随，重启后才统一。

---

## 七、接手建议的下一步（按收益排序）

1. 先定「方向」语义：往返轴 or 单向偏置，并把 UI 文案同步（P0-1）。
2. 处理方向与丝长平行时的拉伸观感（P0-2），必要时在列表里对不合适的组合给出提示。
3. 恢复自定义方向的「内摆半幅」保护（P0-3）。
4. 把 `save_hair_polys` 拆出「只写元数据」路径（P1-7），并补 3 条单测（P2-11）。
5. 大图预览降采样或降低拖动预览频率（P1-6）。

---

## 八、不要做的事

- 不要把逐区域的 `amp` / `dir` 写回 `asset.motion`（那是共享对象，会串到其他桌宠）。
- 不要为了调幅度去清 `_pet_key` 重建引擎（这是上一轮卡顿的根因，已改成 `apply_params`）。
- 不要在拖动过程中同步写盘/mask；会卡 UI。
- 不要改 `swing_all` 的「包围盒外逐比特不变」保证（已有测试覆盖）。
