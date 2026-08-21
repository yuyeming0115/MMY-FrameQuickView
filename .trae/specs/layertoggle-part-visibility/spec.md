# 同 ID 叠层逐部件显隐开关 Spec

## Why
组视图下同 ID 部件全量叠层，fills 等顶层部件会直接遮挡下层（body/hair/weapon），无法单独查看某个部件，也影响对叠层顺序和资源配套的检查。

## What Changes
- B 区画布右侧新增**逐部件 toggle 列表**：列出当前组所有部件（按 layer_order 从底到顶），每部件一行显示自身类型名 + 勾选态，点击切换该层显隐。
- 隐藏层不参与 A 区网格与 B 区动画的合成，其余层仍按原 layer_order 从底到顶叠层；shadow 仍固定最底。
- 组视图默认全显；切换到单部件视图时列表隐藏。
- 各部件显隐状态**跨会话记忆**（QSettings），重启后按上次状态恢复，与列宽/最后目录偏好一致。
- 影响范围限定组视图；不改变单部件模式、不改变模板 layer_order 定义、不做跨 ID 搭配。

## Impact
- Affected specs: 同 ID 部件叠层（组视图）
- Affected code:
  - `src/app.py` — 组选中时加载/维护每层显隐集合；把显隐过滤后的层传给 grid_view/anim_view；QSettings 读写
  - `src/ui/part_list.py` 或新增小部件 — 右侧 toggle 列表的载体（复用深色主题 toggle 样式）
  - `src/ui/grid_view.py` / `src/ui/anim_view.py` — 保持接口不变，只接收已过滤的 layers 列表
- Prob.BREAKING: 无（纯新增 UI + 过滤逻辑，不改数据结构与公共接口）

## ADDED Requirements
### Requirement: 逐部件显隐 toggle 列表
系统 SHALL 在组视图下于 B 区画布右侧显示该组全部部件的 toggle 列表，每部件一行，按 layer_order 从底到顶排列，默认全部可见。

#### Scenario: 隐藏顶层 fills
- **WHEN** 用户选中某组，fills 处于顶层并遮挡下层
- **THEN** 用户在右侧 toggle 列表点击 fills 行 → fills 层从当前方向/动作的叠层合成中移除，A 区网格与 B 区动画同步刷新，下层部件可见；再次点击 fills 行恢复显示

#### Scenario: 显示顺序保持
- **WHEN** 部分层被隐藏
- **THEN** 剩余可见层仍按 layer_order 从底到顶合成，shadow 始终最底

#### Scenario: 单部件模式隐藏列表
- **WHEN** 用户由组视图切换到某个单部件
- **THEN** 右侧 toggle 列表隐藏，不干扰单部件显示

#### Scenario: 跨会话记忆
- **WHEN** 用户隐藏 fills 后重启应用并重新选中同一组
- **THEN** 各部件显隐状态按上次记忆恢复，无需重新设置

### Requirement: 显隐状态持久化
系统 SHALL 使用 QSettings 保存组视图各部件（按部分类名）的显隐状态，并按需恢复。

#### Scenario: 重启恢复
- **WHEN** 应用重启后再次进入组视图
- **THEN** 读 QSettings 恢复上次各部件显隐，缺失项默认可见

## MODIFIED Requirements
无。

## REMOVED Requirements
无。