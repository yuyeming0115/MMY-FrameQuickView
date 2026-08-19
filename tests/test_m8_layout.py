"""验证 M8 改动：按钮高度 / 控制条整行 / 左栏折叠 / 显向 overlay。"""
import os, sys, math
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from pathlib import Path

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication


def mouse_event(t: int, pos: QPointF, btn=Qt.LeftButton) -> QMouseEvent:
    return QMouseEvent(t, pos, pos, btn, btn if t == 4 else Qt.NoButton, Qt.NoModifier)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # 恢复匹配表路径到 QSettings，模拟用户上次成功状态
    from PySide6.QtCore import QSettings
    test_path = Path(r'E:/XYJProject/美术资源/动画序列帧/ID-角色-名字匹配表.txt')
    s = QSettings('MMY', 'FrameQuickView')
    if test_path.exists():
        s.setValue('last_map_file', str(test_path))
        s.sync()

    from src.app import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()

    # === 1. 按钮高度是否锁死 ===
    matrix = w.matrix
    dir_btns = list(matrix.dir_stack._buttons.values())
    act_btns = list(matrix.act_stack._buttons.values())
    h0 = dir_btns[0].height()
    h1 = act_btns[0].height()
    print(f'[1] dir btn h={h0} / act btn h={h1}  (expect 26-32)')

    # === 2. 播放控制条跨整行（matrix_container 上方不含控制栏） ===
    av = w.anim_view
    # 原 AnimView layout 是 QVBoxLayout：[body(QHBoxLayout), bar(QHBoxLayout)]
    lay = av.layout()
    print(f'[2] anim_view outer layout items = {lay.count()} (expect 2: body + bar)')

    # === 3. 左栏折叠/展开按钮 ===
    pl = w.part_list
    print(f'[3] collapse_btn exists: {hasattr(pl, "collapse_btn")}, text={pl.collapse_btn.text()}')
    # 模拟点击
    pl.collapse_btn.click()
    print(f'[3] after click 1: _all_expanded={pl._all_expanded}, btn.text={pl.collapse_btn.text()}')
    pl.collapse_btn.click()
    print(f'[3] after click 2: _all_expanded={pl._all_expanded}, btn.text={pl.collapse_btn.text()}')

    # === 4. 显向 overlay ===
    print(f'[4] matrix.overlay_toggled signal exists: {hasattr(matrix, "overlay_toggled")}')
    print(f'[4] dir_stack.header_btn exists: {matrix.dir_stack.header_btn is not None}')
    print(f'[4] header_btn text: {matrix.dir_stack.header_btn.text() if matrix.dir_stack.header_btn else None}')
    print(f'[4] overlay_enabled init: {av._canvas._overlay_enabled} (expect False)')

    # 打开显向
    matrix.dir_stack.header_btn.setChecked(True)
    print(f'[4] after toggle on: overlay_enabled={av._canvas._overlay_enabled} (expect True)')
    matrix.dir_stack.header_btn.setChecked(False)
    print(f'[4] after toggle off: overlay_enabled={av._canvas._overlay_enabled} (expect False)')

    # === 5. 角度到方向映射 ===
    canvas = av._canvas
    # 屏幕 y 向下：0°=E, 90°=S, 180°=W, 270°=N
    for deg, expect in [(0, 'E'), (45, 'SE'), (90, 'S'), (135, 'SW'),
                        (180, 'W'), (225, 'NW'), (270, 'N'), (315, 'NE')]:
        d = canvas._angle_to_dir(deg)
        print(f'[5] angle={deg}° -> {d} (expect {expect}): {"OK" if d == expect else "FAIL"}')

    # === 6. 拖入数据 + 模拟拖拽 ===
    w._on_folder_dropped(Path(r'E:/Temp/新ID'))
    app.processEvents()

    # 拖一个真实部件以让 _current_dir 有值
    av.set_available_dirs(set(w._tpl.directions))
    av.set_dir_overlay_enabled(True)
    av.set_current_dir('E')
    print(f'[6] overlay enabled: {av._canvas._overlay_enabled}')
    print(f'[6] current_dir set: {av._canvas._current_dir}')

    # 模拟点击 E 槽位（右上区域）
    cw, ch = av._canvas.width(), av._canvas.height()
    print(f'[6] canvas size: {cw}x{ch}')
    # 点击画布右上角 → 应该是 NE 槽
    click_pos = QPointF(cw * 0.85, ch * 0.15)
    print(f'[6] click at ({click_pos.x():.0f},{click_pos.y():.0f}) -> slot: {canvas._slot_dir_at(click_pos)}')

    # === 清理 ===
    s2 = QSettings('MMY', 'FrameQuickView')
    s2.remove('last_map_file')
    s2.sync()
    print('[OK] cleanup done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
