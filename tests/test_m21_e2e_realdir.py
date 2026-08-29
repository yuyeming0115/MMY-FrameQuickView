"""M21 端到端：真实父级目录（角色输出图）→ MainWindow 全链路验证。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication([])
app.processEvents()

from src.app import MainWindow

w = MainWindow()
w.show()
app.processEvents()

root = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图")
assert root.exists(), "测试目录缺失"
w._on_folder_dropped(root)
app.processEvents()

r = w._result
assert r and len(r.groups) > 100, f"应有大量分组: {len(r.groups) if r else 0}"

# 分类分布
from collections import Counter
cats = Counter(g.category for g in r.groups)
print(f"[1] OK 共 {len(r.groups)} 组, 分类分布: {dict(cats)}")
assert cats.get("特效") == 1 and cats.get("主角", 0) > 10 and cats.get("坐骑", 0) >= 3

# 特效组被扫描到（不再是 ignored）
fx = next(g for g in r.groups if g.category == "特效")
assert fx.res_id == "50105101" and fx.is_flat and len(fx.parts) == 1
assert "50105101" not in r.ignored

# 选中特效组 → 按钮矩阵 + A/B 区
w._on_group_selected("50105101")
app.processEvents()
d, a = w.matrix.current()
assert d == "特效" and a == "changrao", (d, a)
layers = w._layers_for_current()[0]   # M26：返回 4 元组，取 layers
assert layers and len(layers[0]) == 20, f"20 帧: {len(layers[0]) if layers else 0}"
print(f"[2] OK 特效组预览: 方向={d} 动作={a} 帧数={len(layers[0])}")

# 状态栏文本（帧号不补零）
status = w.statusBar().currentMessage()
assert "20 帧（1–20）" in status, status
print(f"[3] OK 状态栏: {status}")

# chips 行可见且数量正确
chips = list(w.part_list._chips.keys())
assert "" in chips and len(chips) >= 9, chips
print(f"[4] OK chips: {[w.part_list._chips[c].text() for c in chips]}")

# 点击「特效」chip → 只剩 1 组可见
w.part_list._on_chip_clicked("特效")
app.processEvents()
tree = w.part_list.tree
visible = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
           if not tree.topLevelItem(i).isHidden()]
assert len(visible) == 1 and "50105101" in visible[0], visible
print(f"[5] OK 特效过滤: {visible}")

# 常规资源回归：切回全部，选一个主角组
w.part_list._on_chip_clicked("特效")
w._on_group_selected("50132101")
app.processEvents()
d, a = w.matrix.current()
assert d in ("E", "N", "NW", "S", "SE"), d
print(f"[6] OK 常规组回归: {w._group.res_id} 方向={d} 动作={a}")

print("ALL PASS")
