"""右栏检查器（M44 阶段 2a）：界面最右边那一列。

为什么要有它
------------
以前「方向/动作按钮」夹在 A 区和 B 区中间，「显示层 / 穿戴」面板浮在 B 区画布右上角：
- 按钮列把本该挨着的 A、B 两个视图隔开了
- 显示层面板会挡住画布上的角色
现在把它们都收进右边单独一列，B 区画布只放动画本身。

结构
----
    Inspector（整列可上下滚动）
    ├── InspectorSection「方向 × 动作」   ← 按钮矩阵，方向列和动作列左右并排
    └── InspectorSection「显示层 / 穿戴」  ← 部件显隐开关 + 穿戴特效/翅膀/时装/头发/武器下拉
每个区块点标题可以折叠 / 展开。

说明：本文件只负责「摆放」，按钮和面板本身的逻辑没有改，仍在 button_matrix.py / anim_view.py 里。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .hover_scroll import enable_hover_scroll

INSPECTOR_MIN_WIDTH = 240        # 最窄：方向列 + 动作列并排还能看清按钮文字
INSPECTOR_DEFAULT_WIDTH = 280    # 第一次打开（或旧版本升级上来）时的默认宽度

_HEADER_STYLE = (
    "QPushButton { background: transparent; border: none; border-bottom: 1px solid #3A3F46;"
    " border-radius: 0; color: #96A1AD; font-size: 13px; font-weight: 600;"
    " padding: 6px 2px; text-align: left; letter-spacing: 1px; }"
    "QPushButton:hover { color: #E8E4D9; }"
)


class InspectorSection(QFrame):
    """可折叠的区块：上面一行标题（点击折叠/展开），下面是内容。"""

    toggled = Signal(bool)       # 用户点标题展开(True)/折叠(False)时发出

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # 标题用「可勾选的按钮」：勾选 = 展开。▾ / ▸ 小箭头提示当前状态
        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(_HEADER_STYLE)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.toggled.connect(self._on_header_toggled)
        lay.addWidget(self.header)

        self.body = QWidget()
        self._body_lay = QVBoxLayout(self.body)
        self._body_lay.setContentsMargins(0, 0, 0, 4)
        self._body_lay.setSpacing(4)
        lay.addWidget(self.body)
        self._refresh_title()

    def add_widget(self, widget: QWidget) -> None:
        """往区块里放一个控件（从上往下排）。"""
        self._body_lay.addWidget(widget)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        """由程序设置展开/折叠（启动恢复、和其他开关同步时用），**不发** toggled 信号，避免来回触发。"""
        expanded = bool(expanded)
        if self.header.isChecked() != expanded:
            self.header.blockSignals(True)
            self.header.setChecked(expanded)
            self.header.blockSignals(False)
        self._apply(expanded)

    def _on_header_toggled(self, checked: bool) -> None:
        self._apply(checked)
        self.toggled.emit(checked)

    def _apply(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self._refresh_title()

    def _refresh_title(self) -> None:
        self.header.setText(("▾ " if self.header.isChecked() else "▸ ") + self._title)


class Inspector(QScrollArea):
    """右栏：从上到下若干个可折叠区块，内容超出高度时整列滚动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 只上下滚，不左右滚
        self.setMinimumWidth(INSPECTOR_MIN_WIDTH)
        enable_hover_scroll(self)                                   # 鼠标离开时自动隐藏滚动条
        host = QWidget()
        self._lay = QVBoxLayout(host)
        self._lay.setContentsMargins(8, 0, 6, 8)
        self._lay.setSpacing(10)
        self._lay.addStretch(1)          # 区块都靠上排，下面留白
        self.setWidget(host)

    def add_section(self, title: str, *widgets: QWidget) -> InspectorSection:
        """新增一个区块，按调用顺序从上往下排；返回区块对象（可用来折叠/监听）。"""
        sec = InspectorSection(title)
        for w in widgets:
            sec.add_widget(w)
        self._lay.insertWidget(self._lay.count() - 1, sec)     # 插在最后那个「留白」前面
        return sec

    def sizeHint(self) -> QSize:
        return QSize(INSPECTOR_DEFAULT_WIDTH, 400)

    def keyPressEvent(self, event) -> None:
        """方向键不用来滚动右栏，交给主窗口切方向/动作（M5：←→ 切方向，↑↓ 切动作）。

        点过右栏里的方向/动作按钮后，键盘焦点在按钮上；按钮不处理方向键，事件会一路
        往上传到这个滚动区。滚动区默认会「吃掉」方向键去滚动，主窗口就收不到了。
        这里明确不处理（ignore），让事件继续往上传给主窗口。
        """
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            event.ignore()
            return
        super().keyPressEvent(event)
