# M37：跨 ID 形象组合预览 + fills 检测默认关闭

> 日期：2026-09-29
> 状态：开发中
> 分支：`feature/m37-look-combine`

## 1. 背景

用户实测（角色输出图）反馈：

1. **fills 检测希望默认关闭**：目前默认开，BOSS/主角等无 fills 部件的资源满屏 🟠 橙点。
2. **希望支持「时装 + 武器 + 头发」跨 ID 一起叠显**：这三类是**不同资源 ID** 的部件
   （如 501112101_body / 501111101_hair / 501031001_weapon），分属三个 ID 组；
   现有同 ID 叠层、套装组（M23，同父 ≤16 件）都覆盖不到「大库按 ID 分组」场景下的
   跨 ID 组合预览。

命名规律（匹配表中文名，用户主动维护）：

```
501112101  天命·男〔剑〕时装     ← body+shadow
501111101  天命·男〔剑〕发       ← hair
501031001  天命·男〔剑〕武器     ← weapon+shadow
```

去掉尾部**类型词**（武器/职业/时装/发…）后前缀相同 = 同一「形象」。
部件文件夹本身带 `_body/_hair/_weapon/_shadow` 后缀 → 部位类型、layer_order
叠层顺序、影子归属文案全部可复用现有机制。

## 2. 方案

### 2.1 fills 检测默认关闭

- `app.py`：`checks/fills` 的 QSettings 默认值 True → False；
- `part_list.py`：初始 `_fills_check` 同步改 False。
- 注意：QSettings 已记忆「开」的机器不受默认值影响，在界面上点一次「fills 检测」
  关掉即长期记忆为关。

### 2.2 跨 ID 形象组合（自动配对，显示层可关）

- **模板新字段 `look_types`**（默认随 default.json 下发）：
  `["武器", "职业", "时装", "发"]`。旧模板无此字段 → 功能关闭（行为不变）。
- **配对规则**：`Template.look_split(cn)` 在中文名里**从后往前**找类型词命中，
  返回 `(形象前缀, 类型词)`；无命中返回 None。前缀非空才有效。
- **展示范围**（`app._display_parts(grp)`）：组视图（点组头）时，显示部件 =
  本组渲染部件 + 同形象前缀的其他 ID 组渲染部件，按 `layer_order` 排序
  （影子最底 → body → hair → weapon）；A/B 区叠层、显示层 chips、空帧抽样
  全部使用该列表。特效（flat）/世界BOSS 变体/套装组不参与配对。
- **开关与记忆**：chips 沿用 `_hidden_parts`（QSettings 全局记忆），每个部件
  可单独关掉；chips 的重名消歧（两个影子 → `影子·ID`）按组合后的列表计算。
- **无匹配表 / 前缀无命中 / part 视图（点子项）**：退化为现状（仅本组）。
- **状态栏提示**：配对生效时追加「👤 形象组合 +N 部件」，让用户知道当前叠加了
  跨 ID 部件。
- HUD 账目（M32）本次保持「当前组」口径不变（显示 union 后另立 issue 再议）。

## 3. 涉及模块

| 文件 | 变更 |
|------|------|
| `templates/default.json` | 新增 `look_types` |
| `src/core/template.py` | `look_types` 字段 + `look_split()` |
| `src/app.py` | fills 默认关；`_display_parts/_look_split`；chips/叠层/空帧/状态栏接入 |
| `src/ui/part_list.py` | `_fills_check` 初始 False |
| `tests/test_m37_look_combine.py` | 新增测试 |
| `AGENTS.md` | 项目概述「不做跨 ID 搭配」表述更新 + fills 默认说明 |

## 4. 验收标准

1. 拖入角色输出图，选中「天命·男〔剑〕时装」组 → 画布同时显示 身体影子+身体+头发+武器影子+武器；
2. 显示层 chips 列出 5 个部件（影子带归属名），逐个可关；关闭「头发」后画布只剩 4 层；
3. 选中「逍遥仙〔扇〕武器」（无同前缀其他组）→ 行为与现状一致，无形象组合提示；
4. 无匹配表时行为与现状完全一致；
5. 全新 QSettings 下启动，fills 检测默认为关；
6. 全量既有测试通过。

## 变更记录

- 2026-09-29：创建方案文档。
- 2026-09-29：实现完成。
  - `templates/default.json` / `template.py`：新增 `look_types`（默认 武器/职业/时装/发）
    与 `look_split()`（中文名最后一次类型词命中 → (形象前缀, 类型词)，前缀为空不配对）。
  - `app.py`：
    - fills 检测默认关（`checks/fills` QSettings 默认值 True → False）；
    - `_look_split/_display_parts`：组视图显示部件 = 本组 + 同形象其他 ID 组部件
      （layer_order 排序，影子最底）；接入显示层 chips（`_toggle_key/_toggle_label`
      改为按组合后列表判重/取归属名）、A/B 区叠层（`_layers_for_current`）、
      空帧抽样（`_blank_target_matrix`）；状态栏配对生效时提示「👤 形象组合 +N 部件」。
  - `part_list.py`：`_fills_check` 初始 False（启动时 app 同步实际值）。
  - 测试：`tests/test_m37_look_combine.py`（look_split 边界/持久化/组合 5 部件叠显/
    chips 开关/无配对退化/fills 默认值）；全量 33 套测试通过。
  - 已知边界：hidden_parts 的 key 全局记忆（跨形象共用 body/hair/weapon 等 key，
    关掉一个形象的「头发」其他同 key 一起隐藏）——沿用既有机制，如需按形象记忆再迭代。
