"""验证 M10.1：① BTN_STYLE 去掉 max-width 后无 setMaximumSize 警告 ② 左栏「📖」按钮能打开匹配表并刷新中文名。

测点：
1. 启动后 stderr 无 "QWidget::setMaximumSize" 警告（BTN_STYLE 不再含 16777215）
2. PartList.map_btn 存在、toolTip 正确
3. 模拟点击 map_btn → 走 _pick_map_file → 选好匹配表 → 左栏中文名加载
4. QSettings 持久化 last_map_file 写入
"""
import os, sys, io
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog


# 捕获 stderr 看有没有 setMaximumSize 警告
_stderr_buf = io.StringIO()
_real_stderr = sys.stderr
sys.stderr = _stderr_buf


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    from src.app import MainWindow
    w = MainWindow()
    w.show()
    w.resize(1280, 800)
    app.processEvents()

    print('=' * 60)
    print('M10.1: max-width warning 修复 + 选择匹配表按钮')
    print('=' * 60)

    # === 1. BTN_STYLE 不再含 16777215 ===
    from src.ui.button_matrix import BTN_STYLE
    print(f'\n[1] BTN_STYLE 含 "16777215": {"16777215" in BTN_STYLE} (期望 False)')

    # === 2. 加载数据（走自动发现，先不点按钮）===
    test_map = Path(r'E:/XYJProject/美术资源/动画序列帧/ID-角色-名字匹配表.txt')
    w._on_folder_dropped(Path(r'E:/Temp/新ID'))
    app.processEvents()

    pl = w.part_list
    print(f'\n[2] map_btn exists: {hasattr(pl, "map_btn")}, text={pl.map_btn.text()!r}')
    print(f'[2] map_btn tooltip: {pl.map_btn.toolTip()!r}')

    # === 3. 模拟点击「📖」按钮 → QFileDialog 返回匹配表路径 ===
    # 先清空 QSettings + 内存状态，模拟「自动发现失败」场景
    w._settings.remove('last_map_file')
    w._settings.sync()
    w._last_map_file = None
    w._namemap = type(w._namemap)()  # 空 NameMap
    pl.set_namemap(w._namemap)
    pl._rebuild()  # 清空显示

    # mock QFileDialog.getOpenFileName 返回真实匹配表
    chosen = [str(test_map)]
    orig_get = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (chosen[0], ''))

    # 触发按钮点击（clicked → pick_namemap.emit → _pick_map_file）
    pl.map_btn.click()
    app.processEvents()

    # 恢复
    QFileDialog.getOpenFileName = staticmethod(orig_get)

    # 检查中文名是否加载
    restored = []
    for i in range(pl.tree.topLevelItemCount()):
        grp = pl.tree.topLevelItem(i)
        restored.append(grp.text(0))
    hit = [t for t in restored if '·' in t]
    print(f'\n[3] 点击「📖」后中文名命中数: {len(hit)} / {len(restored)}')
    for t in restored[:5]:
        print(f'    - {t}')

    # === 4. QSettings 持久化 ===
    saved = w._load_saved_map_path()
    print(f'\n[4] QSettings last_map_file = {saved} (期望 {test_map})')

    # === 检查 stderr 警告 ===
    sys.stderr = _real_stderr
    err = _stderr_buf.getvalue()
    warns = [l for l in err.splitlines() if 'setMaximumSize' in l or '16777215' in l]
    print(f'\n[5] stderr setMaximumSize 警告数: {len(warns)} (期望 0)')
    for l in warns[:10]:
        print(f'    ! {l}')

    # 清理 QSettings
    w._settings.remove('last_map_file')
    w._settings.sync()
    print('\n[OK] cleanup done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
