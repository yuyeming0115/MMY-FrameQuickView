"""验证 M7 改动：B 区纵向按钮 + QSettings 持久化兜底。"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # === 1: QSettings 持久化 ===
    test_path = Path(r'E:/XYJProject/美术资源/动画序列帧/ID-角色-名字匹配表.txt')
    assert test_path.exists(), "测试用匹配表缺失"
    s = QSettings('MMY', 'FrameQuickView')
    s.setValue('last_map_file', str(test_path))
    s.sync()
    s2 = QSettings('MMY', 'FrameQuickView')
    rv = s2.value('last_map_file')
    print(f'[1] QSettings roundtrip ok: {rv == str(test_path)}')
    s2.remove('last_map_file')    # 测试完清理
    s2.sync()

    # === 2: 启动 MainWindow 看布局 ===
    from src.app import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()

    mxc = w.anim_view._matrix_container
    print(f'[2] matrix_container width = {mxc.width()} (期望 96)')
    print(f'[2] matrix width = {w.matrix.width()} (期望 90)')

    # 验证 B 区内部动画 + button matrix 都在（M8 起走 _matrix_scroll 而非 _matrix_container.layout）
    bv = w.anim_view
    matrix_widget = bv._matrix_scroll.widget()
    print(f'[2] dir_stack buttons = {len(matrix_widget.dir_stack._buttons)}')
    print(f'[2] act_stack buttons = {len(matrix_widget.act_stack._buttons)}')

    # === 3: 拖入无匹配表目录，验证 saved path 兜底 ===
    # 先保存一个有效路径
    w._save_map_path(test_path)
    w._last_map_file = None     # 强制下一次走 discover(saved) 而不是直接命中内存

    w._on_folder_dropped(Path(r'E:/Temp/新ID'))
    app.processEvents()
    print(f'[3] last_map_file = {w._last_map_file}')
    print(f'[3] STATUS = {w.statusBar().currentMessage()!r}')

    # 检查 50132101 是否显示中文
    tree = w.part_list.tree
    hits = []
    for i in range(tree.topLevelItemCount()):
        ti = tree.topLevelItem(i)
        txt = ti.text(0)
        if '50132101' in txt:
            hits.append(txt)
            break
    print(f'[3] 50132101 row in tree: {hits}')
    print(f'[3] expect contains 「天命男（扇）」')

    # 清理：清掉 QSettings 中我们写的值（避免污染用户配置）
    s3 = QSettings('MMY', 'FrameQuickView')
    s3.remove('last_map_file')
    s3.sync()
    print('[OK] cleanup')

    return 0


if __name__ == '__main__':
    sys.exit(main())
