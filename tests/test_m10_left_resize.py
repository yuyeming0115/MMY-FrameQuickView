"""验证 M10 改动：左栏宽度允许 splitter 拖动（220~480），树列宽跟随容器。

测点：
1. PartList 移除了 setFixedWidth，min=220, max=480
2. 初始列宽 = 220 - 40 = 180
3. 拖到 320 → 列宽 = 280
4. 拖到 480 → 列宽 = 440（达到 max）
5. 拖到 100 → 被 min 限制为 220
6. 拖到 600 → 被 max 限制为 480
"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from pathlib import Path
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # 恢复 QSettings 避免弹 QFileDialog
    test_path = Path(r'E:/XYJProject/美术资源/动画序列帧/ID-角色-名字匹配表.txt')
    s = QSettings('MMY', 'FrameQuickView')
    if test_path.exists():
        s.setValue('last_map_file', str(test_path))
        s.sync()

    from src.app import MainWindow
    w = MainWindow()
    w.show()
    w.resize(1280, 800)
    app.processEvents()

    pl = w.part_list
    print('=' * 60)
    print('M10: 左栏宽度 splitter 拖动验证')
    print('=' * 60)

    # === 1. setFixedWidth 已移除, min/max 正确 ===
    print(f'\n[1] PartList min={pl.minimumWidth()}, max={pl.maximumWidth()} (expect 220/480)')

    # === 2. 加载数据让树有内容（更接近真实使用） ===
    w._on_folder_dropped(Path(r'E:/Temp/新ID'))
    app.processEvents()

    # 找到 splitter
    splitter = pl.parent()
    while splitter is not None and not splitter.inherits('QSplitter'):
        splitter = splitter.parent()
    if splitter is None:
        # 兜底：MainWindow.centralwidget().findChild(QSplitter)
        for sp in w.findChildren(type(pl.parentWidget())):
            pass
        # 简单点：splitter 一定在 part_list 父链上
        p = pl.parent()
        while p is not None and not isinstance(p, type(pl).__bases__):
            p = p.parent()
        print('[ERR] 找不到 splitter')
        return 1
    print(f'[2] splitter found: {splitter}, sizes={splitter.sizes()}')

    initial = splitter.sizes()
    initial_left = initial[0] if len(initial) > 0 else 0
    print(f'[2] initial left pane width = {initial_left} (期望 220)')

    # === 3. 检查 tree 第 0 列初始宽 ===
    col_w0 = pl.tree.header().sectionSize(0)
    print(f'[3] tree col 0 initial = {col_w0} (期望 180 = 220 - 40)')

    # === 4. 模拟拖到 320 ===
    total = sum(splitter.sizes())
    splitter.setSizes([320, total - 320])
    app.processEvents()
    pl_w = pl.width()
    col_w = pl.tree.header().sectionSize(0)
    print(f'[4] after setSizes([320,...]): pl.width={pl_w} (expect 320), col0={col_w} (expect 280)')

    # === 5. 拖到 480（达 max）===
    splitter.setSizes([480, max(0, total - 480)])
    app.processEvents()
    pl_w = pl.width()
    col_w = pl.tree.header().sectionSize(0)
    print(f'[5] after setSizes([480,...]): pl.width={pl_w} (expect 480), col0={col_w} (expect 440)')

    # === 6. 拖到 100（应被 min 限制为 220）===
    splitter.setSizes([100, max(0, total - 100)])
    app.processEvents()
    pl_w = pl.width()
    col_w = pl.tree.header().sectionSize(0)
    print(f'[6] after setSizes([100,...]): pl.width={pl_w} (expect >= 220, max=480), col0={col_w}')

    # === 7. 拖到 600（应被 max 限制为 480）===
    splitter.setSizes([600, max(0, total - 600)])
    app.processEvents()
    pl_w = pl.width()
    col_w = pl.tree.header().sectionSize(0)
    print(f'[7] after setSizes([600,...]): pl.width={pl_w} (expect <= 480, >= 220), col0={col_w}')

    # === 8. 中文长 ID 不再被截断（用 col 宽对比最长 label 像素宽）===
    fm = pl.tree.fontMetrics()
    longest = max(
        (pl.tree.topLevelItem(i).text(0) for i in range(pl.tree.topLevelItemCount())),
        key=len,
        default='',
    )
    px = fm.horizontalAdvance(longest)
    print(f'\n[8] longest label = "{longest[:30]}{"..." if len(longest) > 30 else ""}" ({len(longest)} chars, ~{px}px)')

    # 回到 320 看是否够装
    splitter.setSizes([320, max(0, total - 320)])
    app.processEvents()
    col_w_320 = pl.tree.header().sectionSize(0)
    print(f'[8] at pl=320: col0={col_w_320}px, longest label ~{px}px → {"OK 装得下" if col_w_320 >= px else "可能仍截断"}')

    # === 清理 ===
    s2 = QSettings('MMY', 'FrameQuickView')
    s2.remove('last_map_file')
    s2.sync()
    print('\n[OK] cleanup done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
