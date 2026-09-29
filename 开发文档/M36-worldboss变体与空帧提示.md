# M36：worldboss 变体识别 + 空帧检测提示

> 日期：2026-09-29
> 状态：已合并（PR #20）
> 分支：`feature/m36-worldboss-blank-frame`（已删除）

## 1. 背景与问题

用户实测 `E:\XYJProject\美术资源\动画序列帧\角色输出图`（BOSS 类资源）反馈两个问题：

### 问题 1：`_worldboss` 后缀资源无法读取

- 目录中存在 13 个 `504XXX_worldboss` 文件夹（504001~504015），全部未出现在左栏。
- 根因（实测确认）：
  1. `Template.parse_folder_name` 只认模板 `parts` 列表中的后缀，`worldboss` 不在其中 → 名字解析失败；
  2. 结构兜底 `_looks_like_part_folder` 要求 ≥2 个已知方向/动作子目录，而这批文件夹
     **只有 `SE` 一个方向**（内含 attack/idle/skill）→ 兜底也不命中 → 进 `ignored`。

### 问题 2：特定 BOSS「缺方向/动作」没有正确显示或提示

对 13 个 BOSS 逐序列做 alpha 检测（实测数据）：

| 资源 | 情况 |
|------|------|
| 504001/504009/504013 等大多数 | 5 方向全部有真实内容 |
| **504004（黑龙王）** | **只有 SE 有真内容，E/N/NW/S 全部帧为 875×875 全透明空图**（alpha max=0） |
| 13 个 `_worldboss` 变体 | 仅 SE × attack/idle/skill，帧内容正常（1312×1312） |

现象：504004 的 NW/dead 显示「1 帧 ✅ 连续」，蓝/紫角标一切正常，但 A/B 区画布全空——
**空图帧被当成正常帧，没有任何提示**。软件缺少「帧文件存在但无有效像素」的检测维度。

## 2. 方案设计

### 2.1 worldboss 变体识别（模板驱动）

- `templates/default.json`：
  - `parts` 增加 `"worldboss"`；
  - `action_rules` 增加 `"worldboss"` 规则：SE 期望 `[idle, attack, skill]`，其余方向 `[]`。
- `scanner.scan_part`：`part == "worldboss"` 时 `part_type = "worldboss"`（同 shadow/wings/mount
  的覆盖机制）：
  - 查漏只针对「有期望动作的方向」（SE）——SE 缺 idle/attack/skill 才标红；
  - 其余方向若资源不存在 → 记入新字段 `unexpected_directions`（**不适用**，UI 灰显，非红色缺失）；
  - 若 worldboss 变体意外拥有某方向（如 E 有帧）→ 正常显示，不算异常。
- 组级（`_finalize_group` / `_check_pairing`）：
  - worldboss 是**独立变体**（不是 shadow 那类叠层配套件）：不参与组级方向/动作并集、
    不参与配套校验、不计入组级查漏基准、不参与组级类型判定——否则同 ID 组会把
    plain 主体误报「缺 N 组配套」、组级查漏被 worldboss 的 SE 动作掩盖；
  - 组内**只有** worldboss 部件（无主体）时：按 `worldboss` 规则对组做查漏；
  - worldboss 不参与组级 fills 警告基准（无 fills 惯例，同特效豁免）。
- 叠层：worldboss **不参与叠层渲染**（组视图显隐层不含它），只能通过左栏子行进入
  单部件视图预览——把「世界BOSS 变体」叠到本体上没有意义。

### 2.2 空帧检测提示（不破坏「扫描只读文件名」原则）

扫描阶段仍只读文件名；空帧检测在**选中后**由后台线程抽样完成：

- 新增 `core/blankcheck.py`：`blank_sequences(matrix) -> set[(direction, action)]`，
  对每个 (方向,动作) 抽样**首帧**做 alpha 检测（`imageops.frame_info` bbox 为 None 即空帧），
  首帧空 → 视为空序列（美术占位是整序列空图，抽样近似成立）。
- `app.py`：选中部件/组后启动 `QThread` 工作线程（≤ 约 30 次解码，秒级内），
  结果缓存 `{部件文件夹: set}`（会话级内存缓存，二次选中零 IO）；快速切换时旧线程作废。
- 提示三层：
  1. **按钮矩阵**：空序列的动作按钮角标变红色「空」（原帧数角标位置）；
     整方向全部序列为空 → 方向角标同步红色「空」；
  2. **A/B 区画布**：全空序列渲染时显示居中占位文字「⚠ 空帧：N 帧全透明（占位图）」；
  3. **状态栏**：当前 (方向,动作) 为空序列时追加「⚠ 空帧」段。

### 2.3 显示名

- `namemap.PART_CN_DEFAULT` 增加 `"worldboss": "世界BOSS"`；
- 左栏子行显示「世界BOSS · 黑龙王」；HUD 副标题显示「ID 504004 · 世界BOSS」。

## 3. 涉及模块

| 文件 | 变更 |
|------|------|
| `templates/default.json` | parts/action_rules 增加 worldboss |
| `src/core/scanner.py` | worldboss 查漏规则、unexpected_directions、组级排除 |
| `src/core/blankcheck.py` | 新增：空序列抽样检测 |
| `src/core/namemap.py` | PART_CN_DEFAULT 增加 worldboss |
| `src/ui/button_matrix.py` | 方向灰显（unexpected）、空帧红色角标 |
| `src/app.py` | 空帧抽样线程 + 缓存 + 状态栏提示 + HUD/层排除 worldboss |
| `src/ui/grid_view.py` / `anim_view.py` | 全空序列占位提示 |
| `tests/test_m36_worldboss_blank.py` | 新增测试 |

## 4. 验收标准

1. 拖入「角色输出图」：13 个 `_worldboss` 全部可见（作为同 ID 组子项「世界BOSS · 中文名」）；
2. 点击 worldboss 子项：仅 SE 显示且 3 动作齐全无告警；E/N/NW/S 灰显（不适用）；
3. 点击 504004（黑龙王）：E/N/NW/S 各动作按钮出现红色「空」角标，方向角标红色，
   A/B 区显示空帧占位提示，状态栏含「⚠ 空帧」；
4. 504001 等全内容 BOSS 显示不受影响；plain + worldboss 同组不产生「配套异常」误报；
5. 既有测试全部通过。

## 变更记录

- 2026-09-29：创建方案文档（问题诊断 + 设计定稿）。
- 2026-09-29：实现完成。
  - `templates/default.json`：`parts` + `worldboss`；`action_rules.worldboss`（SE×[idle,attack,skill]，其余方向空）。
  - `scanner.py`：`PartData/IdGroup` 新增 `unexpected_directions`；`scan_part` 对
    `part=="worldboss"` 走独立类型（仅对有期望动作的方向查漏）；`_finalize_group`
    以非 worldboss 部件为查漏主体（组内仅 worldboss 时按 worldboss 规则）、
    worldboss 不参与配套校验/fills 基准/extra_actions 并集；`_check_pairing` 跳过 worldboss。
  - `stats.py`：`_pick_primary` 不选 worldboss 为主件；组账目 normal 集合排除 worldboss
    （不参与并集与 mismatch，不掩盖主体口径）。
  - 新增 `core/blankcheck.py`：`blank_sequences` 抽样首帧检测全透明空序列。
  - `button_matrix.py`：方向列支持「不适用」灰显（unexpected）；空序列动作/方向角标
    红色「空」；新增 `apply_blank` **就地**刷新角标（异步回填不重建按钮，避免外部
    引用/悬停态失效）；`_dir_counts_for_part` 抽出复用。
  - `app.py`：`_BlankCheckWorker`（QThread）+ `_blank_cache` 会话级缓存 + 同 key 去重；
    `_render_parts`（worldboss 不参与叠层/统计，纯 worldboss 组退化全渲染）；
    HUD 副标题部件数用渲染部件口径、部件名走中文名映射；状态栏追加「⚠ 空帧」段。
  - `worker.py`：DecodeWorker 计算 `all_blank`（全帧 alpha bbox 为 None）。
  - `anim_view.py / grid_view.py`：全空序列 → B 区画布中央「⚠ 空帧 · 图片全透明（占位图）」
    警示、A/B 区标题标注「⚠ 空帧占位」。
  - `namemap.py / part_list.py`：worldboss → 「世界BOSS」；左栏子行「世界BOSS · ID中文名」。
  - 测试：`tests/test_m36_worldboss_blank.py`（模板/扫描/查漏/空帧检测/GUI 角标灰显/
    主窗口端到端）；全量 32 个测试套件通过，无回归。
  - 已知注意点：M29 会把选中项 key 持久化到 QSettings（跨进程），测试需快照恢复，
    否则会污染后续 smoke 测试（本次已处理）。
- 2026-09-29：按用户反馈迭代（M36.1 / M36.2）。
  - **M36.1 worldboss 升为左栏主项**：不再作为同 ID 组子项，而是独立成组
    （`IdGroup.is_variant`，key = 变体文件夹路径，display_name = `{id}_世界BOSS`），
    组头显示 `504004_世界BOSS · 黑龙王`，排序紧跟同 ID 主体组。查漏按 worldboss
    规则独立进行；HUD 副标题「ID xxx · 世界BOSS」、状态栏「变体 xxx（1 层）」。
    `scanner._group_parts` 抽取 worldboss 单件成组（套装成员除外）；
    `part_list` 组头/子行显示适配（变体组子行只写「世界BOSS」）。
  - **M36.2 显示层面板折叠**：B 区底部控制行新增「☰ 显示层」开关按钮（与「📊 HUD」
    同款交互，在组视图开/关右上角的 部件显隐 + 穿戴特效/翅膀 面板）；
    `AnimView.layer_panel_toggled` 信号 → app 持久化到 QSettings
    （`display/layer_panel`，默认开，启动恢复）；组视图数据始终就绪，
    关闭时面板隐藏、打开即恢复。
  - 测试：test_m36 扩展（变体主项断言、变体组独立账目、面板开关持久化）；
    全量 32 套测试通过。
- 2026-09-29：用户确认测试通过，PR #20 合并入 main；文档状态更新为已合并。
