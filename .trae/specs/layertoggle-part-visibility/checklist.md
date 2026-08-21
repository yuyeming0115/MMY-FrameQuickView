# Checklist

- [ ] 组视图下 B 区画布右侧出现逐部件 toggle 列表，按 layer_order 从底到顶排列，默认全显
- [ ] 点击某部件行可切换显隐，A 区网格与 B 区动画同步刷新
- [ ] 隐藏 fills 后下层 body/weapon 可见；再次点击 fills 恢复
- [ ] 隐藏部分层后剩余层仍按 layer_order 从底到顶合成，shadow 始终最底
- [ ] 单部件视图下 toggle 列表隐藏，不影响单部件显示
- [ ] 显隐状态写入 QSettings，重启后进组恢复（缺失项默认可见）
- [ ] 既有测试 M11/M15/M19 全部通过，无回归