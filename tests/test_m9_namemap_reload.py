"""验证：模拟用户在弹窗里选好匹配表 → 中文名能照常显示。"""
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QSettings
from pathlib import Path

app = QApplication.instance() or QApplication(sys.argv)

from src.app import MainWindow

w = MainWindow()
w.resize(1280, 800)
w.show()
app.processEvents()

# 模拟用户在弹窗里选了匹配表
map_file = Path(r'E:/XYJProject/美术资源/动画序列帧/ID-角色-名字匹配表.txt')
if not map_file.exists():
    print('match table NOT found, abort')
    sys.exit(1)

w._reload_namemap(map_file)
app.processEvents()

# 拖入目录
w._on_folder_dropped(Path(r'E:/Temp/新ID'))
app.processEvents()

# 检查中文名
tree = w.part_list.tree
hits = []
for i in range(tree.topLevelItemCount()):
    ti = tree.topLevelItem(i)
    txt = ti.text(0)
    if any(k in txt for k in ('50132101', '50112101', '50152101', '50162101')):
        hits.append(txt)

print('=== 中文名查中 ===')
for h in hits:
    print(f'  {h!r}')

s = QSettings('MMY', 'FrameQuickView')
print()
print('after simulation:')
print(f'  last_map_file: {s.value("last_map_file", "", type=str)!r}')

# 清理：避免污染用户本机
s.remove('last_map_file')
s.sync()
print('  cleaned up')

QTimer.singleShot(50, app.quit)
app.exec()
