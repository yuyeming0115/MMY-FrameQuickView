# M45：动画导出 GIF（含主角多部件）与核心接口拆分

> 日期：2026-11-26
> 状态：📝 方案待评审（未动代码）
> 建议分支：阶段 A `feature-m45-compose-render-api`；阶段 B/C `feature-m45-export-anim`
> 关联：`M44-UI布局重构方案.md`（阶段 3 的 ViewState / WearSlot 与本方案阶段 A 是同一件事，合并做）
> 涉及模块：新增 `src/core/compose.py`、`src/core/render.py`、`src/core/export/`、`src/ui/export_dialog.py`；
> 改造 `src/app.py`、`src/ui/worker.py`；`src/core/clipboard_util.py` 挪到 `src/ui/`

---

## 一、需求背景

美术出完主角/伙伴/怪物的序列帧后，经常要把**叠好层的完整角色动画**发给策划、外包或贴进文档看效果。
现在只能在工具里看，导出要另开 PS / 其他软件手动一层层叠，主角的多部件（影子、身体、头发、武器、翅膀、特效）尤其麻烦。

目标：**工具里预览成什么样，就能一键导出成什么样**，支持单个动画、整个角色、展示合集三种范围。

---

## 二、现状评估：模块化做到了哪一步

### 2.1 已经做好的（可直接复用）

| 层 | 文件 | 说明 |
|----|------|------|
| 数据：扫描、模板、匹配表、统计 | `core/scanner.py`、`template.py`、`namemap.py`、`stats.py`、`atlas.py`、`blankcheck.py` | 不依赖 Qt，可以脱离界面单独用 |
| 单帧合成 | `core/imageops.composite_layers` | 纯函数：给定一帧的各层图片 + 对齐参数 → 合成图 |
| 图片编码 | Pillow 12.3（`.venv` 已装） | 自带 GIF / APNG / WebP 动图写入，**不需要新增依赖** |

### 2.2 没拆出来的（导出卡在这里）

| 缺口 | 现在在哪 | 为什么挡路 |
|------|---------|-----------|
| **叠层配方**：这个角色、这个方向×动作要叠哪些层 | `app._layers_for_current` + `_display_parts` + `_dressed_look_entries` + `_append_dressed_*` 等 | 通过 `self.matrix.current()` 读**按钮当前选中的方向动作**，读 `self._group` 等界面状态。批量导出 SE/run 不可能先去点按钮 |
| **序列渲染**：整串帧怎么对齐（画布对齐、防抖锚点、特效居中） | `ui/worker.py` 的 `DecodeWorker.run`（约 100 行） | 锁在 Qt 后台线程里，结果用信号一帧帧发给界面；导出只能抄一份 → 预览和导出迟早不一致 |
| 穿戴状态 | `app.py` 里 5 套 `_load_ / _save_ / _dressed_*` | 和界面、QSettings 混在一起 |
| `clipboard_util.py` | 放在 `core/` | 却依赖 QtGui/QtWidgets，分层错位 |

**结论**：数据层和单帧合成是干净的，中间两层（叠层配方、序列渲染）没有拆出来。
只要把这两层做成纯函数接口，导出 GIF 就是在上面加一个薄薄的「写文件」层。

---

## 三、目标分层

```
┌──────────────── ui/（界面，只负责显示和收集用户选择）────────────────┐
│  舞台预览 / 胶片条        导出弹窗 export_dialog        （以后）对比视图 │
└──────────────┬──────────────────────┬──────────────────────────────┘
               │ 调用                   │ 调用
┌──────────────▼──────────────────────▼──────────────────────────────┐
│ core/compose.py   build_stack()   叠层配方：谁、哪个方向×动作、穿什么 → LayerStack │
│ core/render.py    render_frames() 序列渲染：LayerStack → 一串对齐好的 PIL 图       │
│ core/export/      gif / apng / webp / png_seq   把帧写成文件                    │
├────────────────────────────────────────────────────────────────────┤
│ core/scanner · template · namemap · imageops · atlas（已有，不动）            │
└────────────────────────────────────────────────────────────────────┘
```

规则：**`core/` 里不再 import 任何 PySide6**（`cleanup.py` 的 `QFile.moveToTrash` 例外，已通过 `trash_fn` 注入隔离）。

---

## 四、接口设计

> 下面是接口示意，函数体留空；实现时把现有代码**原样搬过去**，只改「从哪拿输入」。

### 4.1 `core/compose.py`：叠层配方

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LookConfig:
    """一个角色的「穿搭快照」。

    以前这些值散在 MainWindow 的各个属性和 QSettings 里；
    现在打包成一个对象传进来，compose 就不用去读界面了。
    """
    hidden_parts: set[str] = field(default_factory=set)        # 显示层里被关掉的部件 key
    dressed: dict[str, str] = field(default_factory=dict)      # 各槽位穿的资源 key
                                                               # 例：{"weapon": "50103101", "wings": "...", "fx": "..."}
                                                               # 值为 "" = 用户显式脱下；没有这个键 = 走自动推断（M42）
    fx_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)  # 特效微调偏移 {资源名: (dx, dy)}


@dataclass
class ComposeContext:
    """compose 需要的「全局资料」，加载目录后建一次，之后反复用。"""
    result: "ScanResult"            # 扫描结果（所有组，用于形象组合 M37、穿戴库 M38）
    tpl: "Template"                 # 规则模板（layer_order 层序、look_types 类型词）
    namemap: "NameMap | None"       # 匹配表（形象组合、武器标签推断要用中文名）
    fx_library: dict[str, "PartData"]    # 全局特效库 M26
    wing_library: dict[str, "PartData"]  # 全局翅膀库 M28


@dataclass
class LayerStack:
    """一个方向×动作的叠层配方：从下到上的每一层。字段和现在 DecodeWorker 的参数一一对应。"""
    layers: list[list[Path]]                 # 每层的帧文件列表，下标 0 = 最底层（影子）
    flat_mask: list[bool]                    # True = 特效层（按层自身并集框居中）
    fx_offsets: dict[int, tuple[int, int]]   # 层下标 → 微调偏移
    part_keys: list[str]                     # 每层的名字，和 layers 严格同序
    fx_layer_indices: set[int]               # 可 Ctrl+方向键微调的层（排除穿戴翅膀，M28）


def build_stack(target, direction: str, action: str,
                look: LookConfig, ctx: ComposeContext) -> LayerStack:
    """回答：target 在 direction×action 下要叠哪些层。

    target 可以是一个部件（PartData）或一个组（IdGroup，含套装 M23 / worldboss 变体 M36）。
    内部依次处理（顺序必须和现在完全一致，否则预览会变）：
      1. 组内参与渲染的部件（排除 worldboss，M36）
      2. 同形象的跨 ID 部件（M37）
      3. 穿戴的时装/头发/武器（M38，武器没选时按名字里的「剑」「斧」自动推断，M42）
         ⚠ 这些层 flat_mask 必须是 False：它们和身体出自同一张渲染画布，
           按原始画布坐标叠才对得上手的位置（2026-09-29 实测踩过坑）
      4. 去掉 hidden_parts 里被关掉的层
      5. 按 layer_order 排序（所有影子最底 → body → hair → weapon → 组内特效）
      6. 追加穿戴翅膀（M28）→ 追加穿戴特效（M26，置顶）
    """
    ...


def resolve_dressed(slot: str, group, look: LookConfig, ctx: ComposeContext) -> str:
    """某个槽位实际生效的穿戴 key：显式选择优先，失效则回退自动推断（搬自 _effective_dressed_look）。"""
    ...
```

### 4.2 `core/render.py`：序列渲染

```python
from typing import Iterator
from PIL import Image


def render_frames(stack: "LayerStack", canvas_align: bool = True,
                  should_stop=None) -> Iterator[tuple[Image.Image, str]]:
    """把一个叠层配方渲染成一串对齐好的帧，逐帧产出 (合成图, 帧号标签)。

    - 算法从 DecodeWorker.run 原样搬来：预计算 bbox 缓存 → 画布对齐 / 并集 bbox
      → 跨帧统一锚点防抖 → 特效层按层并集居中 → 逐帧 composite_layers
    - 各层帧数不同时，短的取模循环（和现在一致）
    - should_stop：可选的「要不要停」函数，批量导出点取消时用；
      纯函数里不认识 Qt，由调用方把 QThread.isInterruptionRequested 传进来
    - 用 yield 逐帧返回：数百帧的序列不会一次性占满内存
    """
    ...


def is_all_blank(stack: "LayerStack") -> bool:
    """整串帧是否全透明（M36 空帧），搬自 DecodeWorker.all_blank。"""
    ...
```

### 4.3 改造后的调用关系

```python
# ui/worker.py —— 改造后只剩「在后台线程里调 core，把结果发给界面」
class DecodeWorker(QThread):
    frame_ready = Signal(int, object, str)
    finished = Signal(int)

    def __init__(self, stack, canvas_align):
        super().__init__()
        self._stack, self._canvas_align = stack, canvas_align

    def run(self):
        n = 0
        for i, (img, label) in enumerate(render_frames(
                self._stack, self._canvas_align,
                should_stop=self.isInterruptionRequested)):   # 切换资源时可中断（沿用 M41）
            self.frame_ready.emit(i, img, label)              # 交给主线程转成 QPixmap 显示
            n += 1
        self.finished.emit(n)


# app.py —— _layers_for_current 变成三行：收集界面状态 → 调 compose
def _current_stack(self):
    d, a = self.matrix.current()                 # 界面上选中的方向/动作
    return build_stack(self._target(), d, a, self._current_look(), self._ctx)
```

导出、以后的对比视图、命令行都调用同样的 `build_stack` + `render_frames`，**不经过任何界面控件**。

---

## 五、导出功能设计

### 5.1 导出范围

| 范围 | 输出 | 典型用途 |
|------|------|---------|
| 当前动画 | 1 个文件：当前方向×动作 | 快速发一个动作给人看 |
| 当前方向全部动作 | 每个动作 1 个文件 | 检查一个朝向的整套动作 |
| 整个角色 | 每个方向×动作 1 个文件 | 归档、交付 |
| 展示合集 | 1 个文件：按顺序串起来（默认 idle → run → attack → skill → hurt → block → dead） | 发给策划/外包一眼看完 |

- 「整个角色 / 展示合集」只导出**有资源的组合**；缺失、不适用（如 worldboss 非 SE）的组合跳过，并列在完成报告里。
- 空帧（M36 全透明占位）默认跳过，可勾选「也导出空帧」。

### 5.2 主角多部件：完全沿用预览配方

- 导出时用的 `LookConfig` = 当前界面的穿搭快照：套装组 M23、形象组合 M37、穿戴时装/头发/武器 M38/M42、翅膀 M28、特效 M26，
  以及显示层里关掉的部件和特效偏移。
- 导出弹窗顶部显示一行**本次叠层清单**（如「影子×2 · 身体 · 头发 · 武器(自动推断：剑) · 翅膀」），导之前能确认没漏没多。
- 部件之间帧数不一致时（M32 HUD 会标 ⚠），按最长的序列循环对齐（和预览一致），并在弹窗里提示。

### 5.3 对齐与尺寸

导出统一用这条流程，保证**方向之间、动作之间角色位置不跳**：

```
1. 一律用「画布对齐」模式渲染（M34）：所有帧都在原始渲染画布坐标里，跨动作/跨方向天然对齐
2. 对本次要导出的「全部帧」算一个 alpha 并集框（展示合集 = 所有动作一起算）
3. 按这个框裁剪 + 四周留白（默认 8px），去掉画布对齐带来的大片空白
4. 可选放大 1× / 2× / 3×（最近邻插值，像素边缘不糊）
```

- 选项「与预览一致」：跳过第 2、3 步，按预览当前模式原样输出。
- 单个文件的帧尺寸在一个文件内必须一致（GIF/APNG 格式要求），上面流程天然满足。

### 5.4 格式

| 格式 | 透明 | 颜色 | 兼容性 | 建议用途 |
|------|------|------|--------|---------|
| **GIF** | 只有全透明/不透明两档 | 每帧 ≤256 色 | 所有地方都能播（聊天软件、网页、文档） | 默认，发给人看 |
| **APNG** | 完整半透明 | 真彩色 | 浏览器、新版聊天软件；部分软件只显示第一帧 | 要保留影子/发光的半透明 |
| **WebP 动图** | 完整半透明 | 真彩色（可无损） | 浏览器、Android；体积最小 | 网页、体积敏感 |
| **PNG 序列 + 预合成** | 完整 | 真彩色 | 任何软件 | 给后期/引擎二次加工（等于把叠好的层「拍平」） |

### 5.5 GIF 格式的坑与对策

| 坑 | 后果 | 对策 |
|----|------|------|
| 透明只有两档 | 影子、发光的半透明边缘出锯齿或黑边 | 默认铺纯色底（深色 `#1E2023` / 白色 / 自选）；选「透明」时用 alpha 阈值（默认 128）并提示可能有毛边，推荐改用 APNG/WebP |
| 每帧 ≤256 色 | 多部件 + 特效出现色带、帧间闪色 | 全部帧**共用一个调色板**（先把所有帧拼起来量化一次，再逐帧套用）；抖动可选，默认关（像素风更干净） |
| 帧时长以 10ms 为单位 | 12fps = 83.3ms，只能存 80 或 90 | 按目标 FPS 取整，弹窗里显示「实际 ≈ 12.5 fps」；也可用 80/90 交替逼近 |
| 帧时长 < 20ms 时浏览器按 100ms 播 | 50/60fps 的 GIF 反而变慢 | FPS 上限提示：GIF 建议 ≤ 50fps |
| 帧之间残影 | 透明底时上一帧内容留在画面上 | 每帧设为「播完清空」（disposal = 2） |
| 循环次数 | — | 默认无限循环；可设播放 N 次 |

### 5.6 帧率

- 默认取预览当前 FPS（B 区滑杆值）。
- 预留：模板可按动作配置帧率（如 `"fps": {"idle": 8, "attack": 15}`），本期不做，见待确认 Q4。

### 5.7 输出位置与命名

- ⚠ **默认输出目录不能放在正在扫描的目录里**：M24 文件监听会触发重扫，导出的文件还可能被当成资源扫进来。
  默认 = 扫描根目录的同级 `{根目录名}_导出/`，可自选；如果用户选到被扫描目录内部，给出警告。
- 命名：`{ID}_{中文名}/{方向}_{动作}.gif`，展示合集为 `{ID}_{中文名}_{方向}_展示.gif`。
  例：`501521005_女侠客(双刀)/SE_attack.gif`。
- 文件名里的 `\ / : * ? " < > |` 替换为 `_`；同名文件默认覆盖前询问，可选「自动加序号」。

### 5.8 批量与性能

- 导出在后台线程（`ExportWorker`）里跑，主界面不卡；进度条显示「第 12 / 45 个 · SE_attack」，可取消。
- 开始前预估：组合数、总帧数、预计文件大小量级（按首个动画实测外推），超过 50MB 时提醒。
- 完成后弹报告：成功 N 个、跳过 N 个（列原因：缺失 / 不适用 / 空帧）、失败 N 个（列错误），带「打开文件夹」按钮。

### 5.9 界面入口（对齐 M44 新布局）

| 入口 | 行为 |
|------|------|
| 舞台标题行「⤓ 导出」按钮 | 打开导出弹窗，范围默认「当前动画」 |
| 舞台右键「导出当前动画」 | 用上次的设置直接导出，不弹窗 |
| 左栏右键「导出该角色全部动画…」 | 打开弹窗，范围默认「整个角色」 |
| 快捷键 Ctrl+E | 同「⤓ 导出」 |

M44 落地前，先把「⤓ 导出」放在 B 区底栏 HUD 按钮旁边。

导出弹窗（`ui/export_dialog.py`）：

```
┌ 导出动画 ─────────────────────────────────────────────┐
│ 503122 · 宝箱怪                                         │
│ 本次叠层：整体（1 层）                                    │
│                                                         │
│ 范围   ◉ 当前动画（SE · idle）  ○ 当前方向全部动作         │
│        ○ 整个角色（12 个组合）  ○ 展示合集（SE）           │
│ 格式   [GIF ▾]        背景  [深色 ▾]  （GIF 透明会有毛边）   │
│ 帧率   [12] fps（实际 ≈ 12.5）   循环 [无限 ▾]             │
│ 尺寸   ◉ 裁剪留白 [8] px   ○ 与预览一致     放大 [1× ▾]      │
│ 输出   E:\...\角色输出图_版署_导出\        [浏览…]          │
│                                                         │
│ 预计：1 个文件 · 8 帧 · 约 60 KB                           │
│                                   [取消]  [导出]          │
└─────────────────────────────────────────────────────────┘
```

所有选项记入 QSettings `export/*`，下次打开沿用。

### 5.10 `core/export/` 接口

```python
# core/export/__init__.py
from dataclasses import dataclass


@dataclass
class ExportOptions:
    """导出选项：弹窗里的每一项都对应这里一个字段。"""
    fmt: str = "gif"                  # "gif" / "apng" / "webp" / "png_seq"
    fps: int = 12
    loop: int = 0                     # 0 = 无限循环
    background: str | None = "#1E2023"  # None = 透明
    alpha_threshold: int = 128        # GIF 透明模式：alpha 低于它算透明
    dither: bool = False              # GIF 量化时是否抖动
    crop_padding: int | None = 8      # None = 与预览一致（不重新裁剪）
    scale: int = 1                    # 1 / 2 / 3，最近邻放大


def export_frames(frames: list, out_path, opts: ExportOptions) -> None:
    """把一串 PIL 帧写成文件。按 opts.fmt 分发到 gif.py / apng.py / webp.py / png_seq.py。

    每种格式一个文件，以后加「精灵图 / 视频」只需新增一个文件并在这里登记，
    不用改任何界面代码。
    """
    ...


def plan_export(target, scope: str, look, ctx, tpl) -> list[tuple[str, str]]:
    """列出本次要导出的 (方向, 动作) 组合；跳过缺失/不适用/空帧，并返回跳过原因。"""
    ...
```

---

## 六、分阶段实施

### 阶段 A：抽接口（功能零变化，先做）

1. 新增 `core/compose.py`，把 `_layers_for_current` 及其依赖（`_render_parts`、`_display_parts`、`_look_split`、
   `_dressed_look_parts`、`_dressed_look_entries`、`_effective_dressed_look`、`_auto_weapon_key`、`_append_dressed_*`）搬进去，
   界面状态改成参数传入。
2. 新增 `core/render.py`，把 `DecodeWorker.run` 的算法搬进去；`DecodeWorker` 改成调用它。
3. `app.py` 新增 `_current_look()`（从现有属性组装 `LookConfig`）和 `_ctx`（加载目录 / 重扫后重建）。
4. `clipboard_util.py` 挪到 `ui/`，修正 import。

**验收（逐像素对比）**——脚本已就绪：`scripts/golden_frames.py`（2026-11-26）
- 改造**前**跑 `record`：对真实目录按类型抽样（单部件、同 ID 多部件、形象组合、套装、worldboss 变体、扁平特效、翅膀、坐骑、
  自动配武器、单部件视图、手动穿戴翅膀+特效；每类默认 2 个、每个最多 10 个方向×动作组合），
  每个组合按「画布对齐」「并集 bbox」两种模式各渲染一遍，把每帧画面指纹 + 叠层配方指纹存到 `tests/golden/m45_frames.json`。
- 改造**后**跑 `check`：按标准答案里录过的同一批组合重渲染，指纹必须 100% 一致；再跑全量回归测试。
- 差异分两类：「配方不同」= 叠哪些层搬错了（compose）；「画面不同」= 渲染对齐搬错了（render）。
- 只读保证：QSettings 隔离到临时 ini；关闭匹配表「新 ID 自动登记」；不碰任何资源文件。
- 图集资源（M40）在另一个目录（`E:\XYJProject\美术资源\动画序列帧\local-role`，由角色输出图打包而来），录制时用 `--root` 多加一次该目录。
- 套装组（M23）/ 套装内特效（M25）：真实目录里主角套装已拆成单独文件夹，不会触发套装合并；脚本默认自动生成
  `tests/golden/fixture_outfit/`（2 个套装：5 部件 + 扁平特效、3 部件纯中文名；E/SE × idle/attack，像素固定，
  各部件位置/透明度/逐帧位移不同），覆盖这两段代码。该目录已加入 `.gitignore`。加 `--no-fixture` 可关闭。
- 脚本通过 `current_recipe(win)` / `render(...)` 两个适配函数调用现有代码；重构后入口若改名，只改这两个函数。

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/golden_frames.py record   # 改代码前
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/golden_frames.py check    # 改代码后
```

### 阶段 B：最小导出

- `core/export/gif.py`、`apng.py`；`ExportWorker`；导出弹窗（只开放「当前动画」）；B 区底栏「⤓ 导出」按钮。
- 验收：宝箱怪 SE/idle 导出 GIF，帧数 8、时长与 FPS 对应、无限循环、无残影；主角套装 + 穿戴武器导出后与预览目测一致。

### 阶段 C：批量与扩展

- 范围「当前方向全部动作 / 整个角色 / 展示合集」、预估与完成报告、`webp.py`、`png_seq.py`、左栏右键入口、Ctrl+E。

### 阶段 D（可选）：命令行

- `python -m src.cli export <目录> --id 503122 --scope all --fmt gif`：不开界面直接导出，便于以后接进美术出图脚本。
  阶段 A 做完后这一步只是几十行参数解析。

### 测试

| 文件 | 内容 |
|------|------|
| `tests/test_m45_compose_render.py` | golden 哈希对比；`build_stack` 各类目标的层序、flat_mask、隐藏层、穿戴回退 |
| `tests/test_m45_export_gif.py` | 帧数、每帧时长、循环、disposal、共用调色板、透明阈值、背景色、放大倍数 |
| `tests/test_m45_export_batch.py` | 范围规划（跳过缺失/不适用/空帧）、命名清洗、输出目录在扫描目录内的警告、取消 |

按 MEMORY 约定**隔离 QSettings**（临时 ini 放在被扫描目录之外）；导出测试写到系统临时目录。

---

## 七、风险与注意

- **搬代码时顺序不能变**：叠层排序、穿戴追加顺序、flat_mask 取值任何一点不同，预览就会变。所以阶段 A 必须先有 golden 哈希再动手。
- **穿戴层 flat_mask = False** 是踩过坑的约束（武器脱手），接口文档和测试里都要钉死。
- **内存**：「整个角色」可能有几十个组合、上千帧；`render_frames` 逐帧产出，导出器每个文件写完就释放，不把全部帧留在内存里。
  GIF 共用调色板需要先看完一个文件的全部帧：按单个文件为单位做，不跨文件。
- **文件监听**：输出目录放在扫描目录外（见 5.7）；即使用户坚持放在里面，导出期间也要暂停 M24 监听，结束后恢复（参考 M43 清理时的做法）。
- **图集资源（M40）**：`AtlasFrame` 已经能被 `composite_layers` 处理，导出无需特殊处理，但 golden 样本里要包含图集。
- **和 M44 的顺序**：阶段 A 不改界面，可以先于 M44 阶段 2 做；做完后 M44 阶段 3 的 ViewState / WearSlot 直接基于 `LookConfig`。

---

## 八、待确认问题

| # | 问题 | 建议 |
|---|------|------|
| Q1 | GIF 默认背景：深色、白色还是透明？ | 深色 `#1E2023`（和工具里看到的一致）；发文档的人可改白色 |
| Q2 | 默认格式 GIF 还是 APNG？ | GIF（兼容性最好）；弹窗里对半透明资源给出「建议 APNG」提示 |
| Q3 | 展示合集的动作顺序固定用模板 `actions` 顺序，还是允许拖动排序？ | 本期用模板顺序，可勾选包含哪些动作 |
| Q4 | 是否需要按动作配置不同帧率？ | 本期统一 FPS；如有需要在模板加 `fps` 字段，另开模块 |
| Q5 | 展示合集是否在每个动作开头加文字标签（如「attack」）？ | 可选，默认关；开启时在左上角画小字 |
| Q6 | 导出默认放大倍数？ | 1×；小怪可手动选 2× |

---

## 九、变更记录

| 日期 | 内容 |
|------|------|
| 2026-11-26 | 建档：模块化现状评估、compose / render / export 接口设计、GIF 导出方案、分阶段计划 |
| 2026-11-26 | 阶段 A 第一步：新增 `scripts/golden_frames.py`（record / check 逐帧指纹比对，只读） |
| 2026-11-26 | 首次 record：角色输出图 148 组合 / 2376 帧，未改代码 check 148/148 一致（可重复）；补充自动生成套装目录 + local-role 图集目录 |
| 2026-11-26 | 修复 golden 脚本组合编号冲突：local-role 由角色输出图打包，两边 ID 相同，后录的覆盖先录的（148→36）；组合编号加目录名前缀；record 附带比对「同一资源图集 vs 散帧」像素是否一致（只提示） |
| 2026-11-26 | **阶段 A-1 渲染拆分**：新增 `src/core/render.py`（`SequenceRender` 类 + `render_frames()` 简便函数，无 Qt 依赖），算法从 `DecodeWorker.run` 原样搬入（逐行比对：仅结构调整——局部函数改为模块级/方法、emit 改 yield、新增可选 `should_stop`）；`ui/worker.py` 改为薄壳，对外接口不变。待 golden check 308/308 确认 |
| 2026-11-26 | A-1 本机验证：golden check 308/308 一致；test_m34/m36/m44/smoke_gui 通过；已提交 `0a50e80` |
| 2026-11-26 | **阶段 A-2 叠层拆分**：新增 `src/core/compose.py`（`ComposeContext` / `LookConfig` / `LayerStack` + `build_stack()` 等纯函数，无 Qt 依赖），从 `app.py` 原样搬入 `_layers_for_current`、`_display_parts`、`_look_split`、`_render_parts`、`_toggle_key`、`_is_modular_look`、穿戴时装/头发/武器（`resolve_dressed` / `auto_weapon_key` / `dressed_look_parts`）、穿戴翅膀/特效（`_append_dressed_part`）。与方案的差异：① `LookConfig.fx_offsets` 改为 `fx_offset_of` 函数（偏移仍按需从 QSettings 读，与原行为一致）；② 时装/头发/武器用「槽位是否存在」区分「没选过（走自动推断）」与「选了无（""）」；③ `ComposeContext` 每次调用现打包，避免重扫/换模板后拿到旧数据。`app.py` 同名方法保留为一两行的转发（测试与 golden 脚本无需改），删除 `_dressed_look_entries` / `_append_dressed_*`；`app.py` 1761 → 1546 行。新增 `tests/test_m45_compose_render.py`（不启动界面，验证 core 不带入 PySide6、套装层序、隐藏层、穿戴武器、非模块化不叠、单部件、渲染与 should_stop）。待 golden check 308/308 确认 |
| 2026-11-26 | A-2 本机验证通过并提交 `0b9f991`；**阶段 A 完成** |
| 2026-11-26 | **阶段 B 最小导出**：新增 `src/core/export/`（`__init__.py`：`ExportOptions`、`frame_ms`/`actual_fps`、`prepare_frames` 按内容并集裁剪留白 + 最近邻放大、`flatten` 铺背景、`safe_name`、`default_output_dir`、`is_inside`、`export_frames` 总入口；`gif.py`：全部帧共用调色板、背景模式 disposal=1、透明模式按阈值 + 第 255 格透明 + disposal=2、`optimize=False`；`apng.py`：真彩 + 完整 alpha，OP_BACKGROUND/OP_SOURCE）；`src/ui/export_worker.py`（后台线程，可中途停止）；`src/ui/export_dialog.py`（格式/背景/帧率/循环/尺寸/放大/输出目录，选项记 `export/*`，GIF+透明提示毛边、显示 GIF 实际帧率）。入口：B 区标题行「⤓ 导出」+ Ctrl+E（底栏已满，按 M44 规划放标题行）。输出 `{扫描目录}_导出/{ID}_{中文名}/{方向}_{动作}.gif`；输出在扫描目录内时二次确认、已存在时确认覆盖；完成弹窗可「打开文件夹」。核心已在云端实跑：背景色/角色色精确、透明无残影、APNG 半透明 100 原样保留、裁剪尺寸正确。新增 `tests/test_m45_export.py`；`test_m44` 快捷键清单加 Ctrl+E。注意：Pillow 会把**连续完全相同的帧合并**（时长相加，播放效果不变，但读回的帧数会变少）。待本机测试确认 |
| 2026-11-26 | B 阶段本机验证通过并提交 `3de092d` |
| 2026-11-26 | **阶段 C 批量导出**：新增 `core/export/batch.py`（`plan_combos` 按模板顺序列出方向×动作——有普通部件时特效不单独算组合；`run_batch` 逐个「叠层→渲染→写文件」，单项失败/空帧记进 `BatchReport` 不拖累后面，取消在每项开始前和渲染后各判断一次，保证不留半截文件；`item_path` / `showcase_path` / `planned_paths`）、`core/export/webp.py`（无损 + 完整 alpha，`quality=100` 表示最小体积）。界面：导出弹窗新增「范围」四选一（当前动画 / 当前方向全部动作 / 整个角色 / 展示合集，没意义的灰掉）与 WebP；`BatchExportWorker`（进度 + 取消）；主窗口 `_start_batch_export`（输出在扫描目录内确认、已存在文件确认覆盖、进度条可取消、完成报告含成功/跳过/失败 + 明细 + 打开文件夹）；左栏右键「⤓ 导出该资源全部动画…」（先选中再按「整个角色」）。批量线程读不了主线程的 QSettings，所以 `_look_snapshot` 先把所有特效偏移读成字典再传给线程。已在云端实跑核心：整个角色 5 组合→成功 4/跳过 1（空帧）；展示合集 10 帧；WebP；4 个不同位置取消均无半截文件。新增 `tests/test_m45_batch.py`。与方案差异：展示合集目前固定「当前方向、全部动作、模板顺序」，自选动作/加文字标签留待后续（Q3、Q5）；PNG 序列未做。待本机测试确认 |
| 2026-11-27 | 新增 `scripts/verify.sh` 一键验证（quick / golden / all / full），首次由 Codex 在本机运行：42 个测试 39 通过、2 个已知遗留失败、1 个失败；画面比对 288/308 |
| 2026-11-27 | 修复 `test_m45_batch`：对未 `start()` 的 QThread 调 `requestInterruption()` 会被 Qt 忽略，测试改为直接让 `isInterruptionRequested` 返回 True（程序本身无问题） |
| 2026-11-27 | **修复批量导出进度条 bug**：`_on_batch_progress` 里模态 `QProgressDialog.setValue()` 会处理排队事件，若导出恰好此刻结束，`_on_batch_done` 先执行把 `self._export_progress` 置 None，随后 `setLabelText` 报 `AttributeError`（被 Qt 吞掉，测试仍通过）。改为先取局部变量、先改文字最后 `setValue`。Codex 重跑确认无 Traceback |
| 2026-11-27 | 画面比对 20 个不一致**不是代码问题**：美术当天把翅膀 `50104101_wings/_shadow` 改为 `50154101_Wings/_shadow`（父目录 11:55 修改；local-role 11:27 新增 `50154101_Wings_*`）。10 个「缺少」= 旧翅膀组不存在；10 个「配方不同」= 手动穿戴的翅膀按资源名记忆，旧名失效后少一层。大写 `Wings` 不影响识别（`50154101_Wings` 已进翅膀库）。**待资源稳定后重录 golden**。附带影响：用户以前手动穿戴 `50104101` 的选择已失效，需重选 |
| 2026-11-27 | **golden 脚本区分「资源已变」**（format 2）：record 时为每个组合记下用到的帧文件（散帧 PNG；图集记 PNG + PLIST），写入 `meta.files` 总表（[路径, 大小, 修改时间]；fixture_outfit 每次重新生成，改用内容哈希），组合里只存编号；另记匹配表/模板指纹 `meta.env`。check 时不一致的组合若「当时用到的文件现在不存在或变了」→ ⚠ 资源已变（不算失败，退出码 0）；文件都没变画面却变 → ✗ 不同（失败）。匹配表/模板变化只提示不排除；资源目录里新增的文件不算资源变化。旧格式（format 1）标准答案仍可 check，所有差异按失败算并提示重录。新增 `selftest` 模式（临时目录生成小资源，验证「不改→一致」「改一个像素→不同」「删一帧→资源已变」三种判定）。`verify.sh` 的 golden 结果会列出资源已变的对象和提示。现有 `tests/golden/m45_frames.json` 仍是旧格式，资源稳定后重录即升级 |
| 2026-11-27 | golden 改进由 Codex 验证：`bash -n verify.sh` 通过；`selftest` 三项判定全部正确（不改→12/12 一致；改 1 像素→12 个不同、exit=1；删 1 帧→1 个资源已变、exit=0）；旧格式标准答案仍可 check（288/308，20 个翅膀差异按失败算并提示重录）。**阶段 C 提交并合并入 main**（golden 暂不重录：美术资源还在改，资源稳定后重录并单独提交） |
| 2026-11-27 | **修复关窗崩溃（旧问题）**：`MainWindow.closeEvent` 以前不停 A/B 区解码线程和空帧抽样线程，关窗时若仍在解码，程序退出会报 `QThread: Destroyed while thread is still running` 并强制崩溃（Codex 跑 `verify.sh all` 时 `test_layertoggle_part_visibility`、`test_m10_1_map_btn` 断言全过但退出码非 0，偶发、取决于时机）。改为关窗时先对两个 DecodeWorker `requestInterruption()`，再调用已有的 `_stop_all_workers_and_caches()`。`test_m10_1` 结束前也调用 `_stop_all_workers_and_caches()`（不调 close()，避免 closeEvent 写注册表真实设置） |
| 2026-11-26 | 待办：local-role 图集与角色输出图散帧同资源像素对比——画布对齐 90/110 一致、并集 bbox 0/110 一致；疑为图集 bbox 取 TexturePacker 记录范围、散帧取实际 alpha 范围。M40 文档「像素级 0 误差」需复核（不影响 golden，golden 各目录自比） |
