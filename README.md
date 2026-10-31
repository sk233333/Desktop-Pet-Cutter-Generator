# Little-figurine-rating — 手办照片转 2D 桌宠素材

把白底手办照片自动抠成**带透明通道的桌宠素材**（PNG + 无损 WebP + manifest），
供 PyQt / Electron / 网页桌宠直接使用。

> 原「瑕疵检测」工程已整体移除；本仓库现在只保留抠图工具、30 张 OK 抠图源与已生成的桌宠素材。

---

## 一、目录

```
Little-figurine-rating/
├── desktop_pet/
│   ├── launch.py              一站式：选图 -> 框取 -> 生成素材 -> 启动桌宠（默认图形界面，--cli 走命令行）
│   ├── ui_app.py              PyQt6 单窗口操作界面（框取/补点/预览/框眼睛全在一个窗口里）
│   ├── cutout_to_pet.py       白底图 + mask -> 透明素材包
│   ├── anim.py                通用动作层：呼吸/摇摆参数、自动找眼、生成眨眼帧
│   ├── pet_window.py          PyQt6 透明置顶窗口：拖拽 + 呼吸摇摆 + 随机眨眼
│   ├── source/<sku>/          抠图源：<stem>.png + <stem>_mask.png + 可选 .json / _eyes.json
│   └── assets/<sku>/          生成产物：<stem>.png / .webp / _preview.png / manifest.json
└── Punctuation_selection_of_images/
    ├── image_assist.py        批量抠图/白底生成工具（SAM + YOLO）
    ├── image_assist_ans/      30 张 OK 白底图
    └── sam2.1_hiera_small.pt  SAM 权重
```

`desktop_pet/assets/` 是生成产物，已加入 `.gitignore`；`source/` 建议入库存源图与 mask。

---

## 二、快速开始

```bash
# 依赖：opencv-python、numpy、Pillow；可选 pymatting（边缘更干净）
pip install opencv-python numpy Pillow pymatting

# 生成全部素材
python3 desktop_pet/cutout_to_pet.py --sku bgy

# 只处理一张，并输出棋盘格预览
python3 desktop_pet/cutout_to_pet.py --sku bgy \
  --stem bgy_001_white --size 512 --preview

# 换尺寸 / 换输出目录 / 不用 matting
python3 desktop_pet/cutout_to_pet.py --sku bgy --size 768
python3 desktop_pet/cutout_to_pet.py --sku bgy --out-dir /tmp/pet_assets
python3 desktop_pet/cutout_to_pet.py --sku bgy --no-matting
```

新增 SKU：把白底图和对应 `_mask.png` 放进 `desktop_pet/source/<sku>/`，再跑 `--sku <sku>`。
没有 mask 时脚本会退回「非白像素」估计，但边缘质量会下降。

### 一条命令：选图 → 框取 → 选点 → 预览 → 定位左/右眼 → 桌宠

```bash
pip install PyQt6

python3 desktop_pet/launch.py                    # 弹文件选择框，随后四步交互
python3 desktop_pet/launch.py --image photo.jpg
python3 desktop_pet/launch.py --image photo.jpg --box 120,80,900,780 --no-points --no-preview --no-eyes
python3 desktop_pet/launch.py --image photo.jpg \
  --eyes-boxes "150,200,320,300;400,210,560,310"   # 原图坐标，直接跳过左右眼定位
```

每个窗口上方都标出「第 x / 4 步 · 这一步要做什么」：

1. **框取手办**：拖拽出框或左键点两个对角（点第一个角会显示黄点）；`r` 重框、`u` 撤销、`f` 整张图；回车继续
2. **选点**：左键 = 前景(红)、右键 = 背景(蓝)，帮 SAM 纠正；`r` 全部清空、`u` 撤销、`s` 不补点；回车分割
3. **预览分割**：四联预览（原图+框 / Mask / 绿色叠加 / 白底图）；回车采用，`a` 返回补点重算，`ESC` 取消
4. **定位左眼、右眼**：分成两个独立步骤，逐只框住整个眼窝（含眼白），写入 `<stem>_eyes.json`；
   每步都可以 `s` 跳过这只眼，`r` 重框、`ESC` 取消

眼睛是逐只生效的：只框左眼就只眨左眼；两只都不框就不眨眼也不跟随视线；只有加
`--no-eyes` 时才完全不写标注、交给启动时自动检测。白底图有补点时用
「非白阈值 + GrabCut」微调，所以右键排除会真正生效（不会再被 SAM 的整幅掩膜吃掉）。

`--no-preview` 可在批量/无头场景跳过第 3 步预览；`--no-points` 跳过选点，`--no-eyes` 跳过左右眼定位。

掩膜引擎：背景接近白色直接走非白阈值（不加载 SAM）；否则优先 SAM（`--engine sam`，
权重缺失或失败回退 `--engine grabcut`，`--device cpu` 可强制 CPU）。
生成后合成白底图存到 `source/<sku>/`，透明素材写到 `assets/<sku>/`，
最后自动弹出桌宠窗口（`--no-launch` 只生成）。

### 图形界面（单窗口）

```bash
python3 desktop_pet/launch.py        # 不带参数默认就是图形界面
python3 desktop_pet/launch.py --cli  # 要原来的命令行多窗口流程
```

界面里所有交互都收进一个窗口：

- 左侧是画布：滚轮缩放、中键拖动平移；右 / 下侧是参数面板与实时日志
- 框取 / 定位眼睛：拖拽出框或左键点两个对角（首点显示黄点），回车确认，`r` 重框、`u` 撤销、`s` 跳过、`ESC` 取消
- 选点：左键 = 前景(红叉)、右键 = 背景(蓝叉)，回车分割，`r` 清空、`u` 撤销、`s` 跳过
- **补点只统计绿框内的点击**，框外点击会被忽略并提示（因为掩膜最终按框裁剪）；想让某块背景被排除，先把框盖住它再打蓝点
- 有补点时以「点是否被满足」挑 SAM 候选：框画得很大（超过半幅图）会自动改成只用点提示、框只做裁剪，避免 SAM 把整幅图当成一个物体
- 预览：上方单选按钮可切换 叠加 / 掩膜 / 白底 / 原图+框 四种视图，回车采用、`a` 返回补点、`ESC` 取消
- 日志会打印实际走的引擎（SAM / GrabCut）、补点数量、候选分数和掩膜覆盖率，点没起作用时一眼能看出原因
- 面板可设 SKU、素材名、边长、显示高度、引擎与设备，也可关掉补点 / 预览 / 定位左右眼 / 启动桌宠
- 生成完成后「打开素材目录」可用，勾选「完成后启动桌宠」会直接弹出桌宠窗口

### 跑起来（桌宠）

```bash
pip install PyQt6          # 窗口与动画

# 启动桌宠（按 manifest 取素材与显示高度）
python3 desktop_pet/pet_window.py --sku bgy --stem bgy_001_white

# 直接指定素材 / 换显示高度 / 关掉眨眼或动画
python3 desktop_pet/pet_window.py --asset desktop_pet/assets/bgy/bgy_001_white.png
python3 desktop_pet/pet_window.py --sku bgy --height 320
python3 desktop_pet/pet_window.py --sku bgy --no-blink --no-anim

# 关掉交互增强：视线跟随 / 落地回弹
python3 desktop_pet/pet_window.py --sku bgy --no-gaze --no-land

# 只看眨眼帧的离线预览（不依赖 Qt）
python3 desktop_pet/anim.py --asset desktop_pet/assets/bgy/bgy_001_white.png \
  --preview /tmp/blink_preview.png
```

操作：左键拖动，滚轮缩放（脚底位置保持不动），双击暂停/继续，
右键菜单可暂停、开关眨眼/视线/落地、放大/缩小/复位、退出。
`--sku` 不带 `--stem` 时使用 manifest 里的第一条素材并打印提示。

交互增强（都需要眼睛位置，自动检测不可靠时会自动跳过）：

- **视线跟随**：把「虹膜 + 瞳孔」当成一个整体（眼球）在固定的眼眶窗口里平移，眼眶 / 眼睑 / 睫毛轮廓不动。
  眼白的宽窄变化是平移的自然结果，不另画：眼球往左 → 左边眼白变窄、右边变宽；往下 → 下边眼白变窄。
  高光钉在原位，不跟视线走（跟的是光源）；两只眼各自朝鼻侧微收（位移的 20%，上限 2px）。
  偏移随鼠标距离做 smoothstep 缓入，再按 0.18s 时间常数逐帧缓动，不会一帧跳到位
  实现为图层化：眼眶内自动找虹膜椭圆 → 眼白底层（把虹膜连同 2px 软边用最近的眼白像素填平，
  眼球挪开后原位置不会残留一圈暗线）→ 平移「虹膜 + 瞳孔」（软 alpha 一起搬移，并裁在眼眶内）
  → 高光原样贴回；高光按「低饱和 + 够亮」找块（只按亮度会漏掉手办被阴影压暗的白色高光）
- **单击反应**：原地弹跳（先挤压再拉伸的衰减振荡）并连眨两下
- **拖拽甩动**：拖动时按每帧位移估速，松手后按该速度惯性滑行，水平阻尼衰减
- **落地回弹**：松手后受重力（1500 px/s²）落向任务栏上沿，触地反弹系数 0.45，
  落地瞬间带一次轻微挤压；撞到屏幕左右边缘同样反弹
- 虹膜位移：窗口侧上限约素材短边的 0.7%（封顶 2.5px），同时裁剪在 0.6 倍虹膜半径内，
  避免顶到眼眶边缘出现斗鸡眼/翻白眼
- 自动检测把大块头发当成眼睛（`rx` 超过素材宽度 12%）时，视线跟随自动停用；
  眯着的那只眼（`ry < 0.45·rx`）不参与视线

### 眼睛位置不准时（手工框选）

自动检测靠暗色眼线找一对眼睛，遇到浅色瞳孔或刘海遮挡会偏。可以手工框选：

```bash
# 1. 对着源图逐张框选双眼（不加载 SAM），写回 <stem>_eyes.json
python3 Punctuation_selection_of_images/image_assist.py --eyes-only \
  --image-dir desktop_pet/source/bgy

# 2. 重新生成素材：坐标会跟着裁边/缩放写进 manifest 的 eyes 字段
python3 desktop_pet/cutout_to_pet.py --sku bgy

# 3. 启动时窗口优先用手工坐标，找不到才回退自动检测
python3 desktop_pet/pet_window.py --sku bgy --stem bgy_001_white
```

框选操作：左键点每只眼的两个对角，回车保存；`r` 重画、`u` 撤销、`ESC` 跳过；
加 `--skip-done` 只处理还没有 `_eyes.json` 的图。

---

## 三、脚本做了什么

1. **掩膜清理**：去掉小碎片，保留主体与足够大的配件
2. **alpha 估计**：优先 `pymatting`；缺依赖时用「腐蚀/膨胀带 + 高斯羽化」
3. **去白边**：按 `C = a·F + (1-a)·白` 反解前景色，扣掉白底混进边缘的白
4. **裁边**：裁到内容边界并保留 `--margin` 透明边距
5. **预乘缩放**：在预乘 alpha 空间缩放到 `--size`，避免半透明边缘被背景污染
6. **导出**：透明 PNG、无损 WebP、可选棋盘格预览、`manifest.json`

`manifest.json` 每个条目包含：`width/height`、`scale`、`anchor`（脚底中心，左上角原点）、
`coverage`、`alpha_mode`、`mm_per_px_canon`（如有）；若源目录里有 `<stem>_eyes.json`，
眼睛坐标会随裁边与缩放换算后写入 `eyes`，窗口优先使用手工坐标。

---

## 四、已知限制

- 单张照片没有背面/被遮挡部分的像素，大幅动作需要 inpainting 或分层绑定
- 白底边缘的半透明发丝、耳尖在无 `pymatting` 时会略硬，装了会更自然
- mask 若把投影也包含进去，会带出一片暗边；需要重新分割或单独做阴影层
- 素材本身是静态图；呼吸与摇摆由实时几何变换生成，眨眼靠自动找到的一对眼睛局部覆盖肤色
- 眨眼检测依赖暗色眼线：浅色瞳孔、被刘海遮住或侧脸时检测不到；此时用 `--eyes-only` 手工框选，或窗口自动跳过眨眼
- 窗口是矩形命中区，完全透明的角落也会响应拖动；Wayland 会话下无法自由定位与置顶，建议 X11 或 `QT_QPA_PLATFORM=xcb`
- `launch.py` 默认用 SAM；权重缺失或手动 `--engine grabcut` 时适合纯色/简单背景，复杂背景、发丝与半透明边缘仍建议先用 `image_assist.py` 抠图再转素材

---

## 五、下一步

1. 程序化变形烘 8~12 帧循环，替代实时 `QTransform`（低配机器更省 CPU）
2. 弯曲 / 点头等参数化动作，以及多素材随机切换
3. 打包 PyInstaller，做开机自启、系统托盘与多屏跟随
