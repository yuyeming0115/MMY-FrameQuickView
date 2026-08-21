# Tasks

- [ ] Task 1: 新建右侧逐部件 toggle 层控制小组件
  - [ ] SubTask 1.1: 实现 PartToggles 部件（QFrame），接收 `[(part名, 是否可见)]` 渲染成 toggle 行，按 layer_order 从底到顶，深色主题样式
  - [ ] SubTask 1.2: 暴露 `toggled(part)` 信号，点击某行切换该部件显隐（默认可见）
- [ ] Task 2: app.py 接入显隐过滤
  - [ ] SubTask 2.1: app 持有 `_hidden_parts: set[str]`；组选中时把 toggle 列表装入右侧
  - [ ] SubTask 2.2: 修改 `_layers_for_current`：组模式下跳过 hidden 部件（shadow 例外仍需保留可隐藏但默认可见）
  - [ ] SubTask 2.3: 单部件视图隐藏 toggle 列表；组/部件切换时同步列表状态
- [ ] Task 3: 跨会话记忆
  - [ ] SubTask 3.1: 保存各部件显隐到 QSettings（key `layering/hidden_parts`）
  - [ ] SubTask 3.2: 启动/进组时恢复，缺失项默认可见
- [ ] Task 4: 布局与验证
  - [ ] SubTask 4.1: 把 toggle 列表放入 B 区画布右侧布局，占用合理宽度、不遮挡画布
  - [ ] SubTask 4.2: 跑 smoke（组视图 + 单部件视图 + A/B 区刷新）
  - [ ] SubTask 4.3: 运行现有测试 M11/M15/M19 确认无回归

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 2]
- [Task 4] depends on [Task 2], [Task 3]