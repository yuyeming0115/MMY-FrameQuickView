"""验证 M35 改动：B 区按钮矩阵独立列不遮挡画布 + 显向 overlay 弱化与双色区分。"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.app import APP_STYLE
from src.ui.anim_view import AnimView
from src.ui.button_matrix import ButtonMatrix
from src.core.template import load_templates


def fake_frame(w=200, h=280):
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(150, 80, 200, 235))
    p.drawRoundedRect(60, 80, 80, 160, 24, 24)
    p.end()
    return pm


def slot_sample_point(av, direction, frac_y=0.12):
    """取某方向槽位内避开文字的采样点（AnimView 坐标系，水平中线、y=frac_y 高度处）。"""
    canvas = av._canvas
    w, h = canvas.width(), canvas.height()
    origin = canvas.mapTo(av, QPoint(0, 0))
    col, row = {'NW': (0, 0), 'N': (1, 0), 'NE': (2, 0), 'S': (1, 2)}[direction]
    x = origin.x() + int(w / 3 * (col + 0.5))
    y = origin.y() + int(h / 3 * (row + frac_y))
    return x, y


def brightness(c):
    return (c.red() + c.green() + c.blue()) / 3


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)

    tpls = load_templates()
    av = AnimView()
    av.resize(1000, 640)
    mx = ButtonMatrix()
    if tpls:
        mx.set_template(tpls[0])
    av.set_matrix_widget(mx)
    av.set_available_dirs({'E', 'N', 'NW', 'S', 'SE'})
    av.set_current_dir('NW')
    av._canvas.set_frame(fake_frame())
    av.show()
    app.processEvents()
    app.processEvents()

    # === 1. 按钮矩阵容器与画布几何不相交（M35 核心：不再悬浮遮挡） ===
    def abs_rect(w):
        tl = w.mapTo(av, w.rect().topLeft())
        return QRect(tl, w.size())
    m_rect = abs_rect(av._matrix_container)
    c_rect = abs_rect(av._canvas)
    overlap = m_rect.intersected(c_rect)
    ok1 = overlap.width() <= 0 or overlap.height() <= 0
    print(f'[1] matrix {m_rect} vs canvas {c_rect} overlap={overlap} -> {"OK" if ok1 else "FAIL"}')

    # === 2. toggles / HUD 挂在画布 host 上（不再依赖悬浮 stack） ===
    ok2 = (av._toggles.parent() is av._stack_host
           and av._hud.parent() is av._stack_host
           and av._canvas.parent() is av._stack_host)
    print(f'[2] toggles/hud/canvas parent is canvas_host -> {"OK" if ok2 else "FAIL"}')

    # === 3. overlay 双色弱化：当前=金(r>b)，悬浮=米白(更亮)，其他=淡 ===
    av.set_dir_overlay_enabled(True)
    av._canvas._hover_dir = 'NE'
    av._canvas.update()
    app.processEvents()
    img = av.grab().toImage()

    cur = img.pixelColor(*slot_sample_point(av, 'NW'))    # 当前方向（金色系）
    hov = img.pixelColor(*slot_sample_point(av, 'NE'))    # 悬浮方向（米白系）
    oth = img.pixelColor(*slot_sample_point(av, 'N'))     # 其他方向（淡虚线）
    ok3 = (cur.red() - cur.blue() > 8
           and brightness(hov) - brightness(oth) > 12
           and brightness(oth) < brightness(hov))
    print(f'[3] cur=rgb({cur.red()},{cur.green()},{cur.blue()}) '
          f'hov_b={brightness(hov):.0f} oth_b={brightness(oth):.0f} -> {"OK" if ok3 else "FAIL"}')

    # === 4. overlay 关闭后这些位置全部回到背景（无残留高亮） ===
    av.set_dir_overlay_enabled(False)
    av._canvas._hover_dir = None
    av._canvas.update()
    app.processEvents()
    img2 = av.grab().toImage()
    hov2 = img2.pixelColor(*slot_sample_point(av, 'NE'))
    ok4 = abs(brightness(hov2) - brightness(oth)) < 12
    print(f'[4] disabled hov_b={brightness(hov2):.0f} (≈oth) -> {"OK" if ok4 else "FAIL"}')

    passed = all([ok1, ok2, ok3, ok4])
    print('ALL M35 PASS' if passed else 'M35 FAIL')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
