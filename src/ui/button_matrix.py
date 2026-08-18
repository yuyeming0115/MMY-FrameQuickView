"""方向/动作按钮矩阵：全展开按钮 + 缺失暗红标记 + 选中金色。

不用下拉列表——查漏 = 看按钮颜色。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..core.scanner import PartData
from ..core.template import Template

BTN_STYLE = """
QPushButton {
    background: #2A2E33; border: 1px solid #3A3F46; color: #E8E4D9;
    border-radius: 6px; padding: 5px 14px; font-size: 14px;
}
QPushButton:checked {
    border: 2px solid #D4AF37; color: #D4AF37;
}
QPushButton[missing="true"] {
    background: rgba(199,68,68,0.18); border: 1px solid #C74444; color: #E79A9A;
}
"""
LBL_STYLE = "color: #96A1AD; font-size: 14px;"


class ButtonRow(QFrame):
    """一行全展开按钮。"""

    selected = Signal(str)

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = QLabel(label)
        self._label.setStyleSheet(LBL_STYLE)
        self._label.setFixedWidth(38)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addWidget(self._label)
        self._buttons: dict[str, QPushButton] = {}
        self._layout.addStretch(1)

    def rebuild(self, names: list[str], missing: set[str], current: str | None) -> None:
        for btn in self._buttons.values():
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        # 移除尾部 stretch 后重建
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        for name in names:
            btn = QPushButton(name + (" 缺" if name in missing else ""))
            btn.setCheckable(True)
            btn.setProperty("missing", name in missing)
            btn.setStyleSheet(BTN_STYLE)
            btn.setChecked(name == current)
            btn.clicked.connect(lambda _=False, n=name: self.selected.emit(n))
            self._layout.addWidget(btn)
            self._buttons[name] = btn
        self._layout.addStretch(1)

    def set_current(self, name: str) -> None:
        for n, btn in self._buttons.items():
            btn.setChecked(n == name)


class ButtonMatrix(QFrame):
    """方向行 + 动作行。"""

    direction_selected = Signal(str)
    action_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dir_row = ButtonRow("方向")
        self.act_row = ButtonRow("动作")
        layout.addWidget(self.dir_row)
        layout.addWidget(self.act_row)
        self.dir_row.selected.connect(self.direction_selected)
        self.act_row.selected.connect(self.action_selected)

        self._tpl: Template | None = None
        self._part: PartData | None = None

    def set_template(self, tpl: Template) -> None:
        self._tpl = tpl
        self.dir_row.rebuild(tpl.directions, set(tpl.directions), None)
        self.act_row.rebuild(tpl.actions, set(tpl.actions), None)

    def show_part(self, part: PartData, direction: str | None, action: str | None) -> None:
        """按部件扫描结果刷新两行按钮的缺失状态。"""
        self._part = part
        tpl = self._tpl
        if tpl is None:
            return
        miss_dirs = set(part.missing_directions)
        if direction is None or direction in miss_dirs:
            avail = part.available_directions()
            direction = avail[0] if avail else None
        self.dir_row.rebuild(tpl.directions, miss_dirs, direction)

        miss_acts: set[str] = set()
        if direction:
            miss_acts = set(part.missing_actions.get(direction, tpl.actions))
            if action is None or action in miss_acts:
                avail = part.available_actions(direction)
                action = avail[0] if avail else None
        self.act_row.rebuild(tpl.actions, miss_acts, action)

    def current(self) -> tuple[str | None, str | None]:
        d = next((n for n, b in self.dir_row._buttons.items() if b.isChecked()), None)
        a = next((n for n, b in self.act_row._buttons.items() if b.isChecked()), None)
        return d, a
