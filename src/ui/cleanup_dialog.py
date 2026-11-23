"""清理动作确认弹窗：展示待移入回收站的目录/图集文件清单并支持逐项勾选。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from ..core.cleanup import CleanupItem

_DIALOG_STYLE = """
QDialog { background: #1E2023; color: #E8E4D9; font-size: 14px; }
QLabel#hintLabel { color: #E8A33D; font-size: 14px; font-weight: 600; }
QLabel#countLabel { color: #96A1AD; font-size: 14px; }
QListWidget {
    background: #23262A; border: 1px solid #3A3F46; border-radius: 6px;
    color: #E8E4D9; font-size: 14px; padding: 4px;
}
QListWidget::item {
    padding: 6px 8px; border-bottom: 1px solid #2E3238;
}
QListWidget::item:selected {
    background: rgba(212, 175, 55, 0.14); color: #E8E4D9;
}
QPushButton {
    background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;
    padding: 6px 14px; color: #E8E4D9; font-size: 14px;
}
QPushButton:hover { border-color: #D4AF37; }
QPushButton#dangerBtn {
    background: #C74444; border-color: #C74444; color: #FFFFFF; font-weight: 600;
}
QPushButton#dangerBtn:hover { background: #D85555; border-color: #D85555; }
QPushButton#dangerBtn:disabled {
    background: #3A3F46; border-color: #3A3F46; color: #7A828C;
}
"""


class CleanupConfirmDialog(QDialog):
    """移入回收站前的勾选确认弹窗。"""

    def __init__(self, items: list[CleanupItem], title: str = "清理多余动作", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 460)
        self.setStyleSheet(_DIALOG_STYLE)
        self._items = list(items)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        hint = QLabel("⚠ 以下动作将移入系统回收站（可随时从回收站还原），请确认勾选：")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        for item in self._items:
            paths_str = " | ".join(str(p) for p in item.paths)
            text = (
                f"{item.part_name}  |  {item.direction}/{item.action}"
                f"（{item.frame_count} 帧 · {item.reason}）\n"
                f"    ↳ {paths_str}"
            )
            li = QListWidgetItem(text)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked)
            li.setData(Qt.ItemDataRole.UserRole, item)
            self._list.addItem(li)
        self._list.itemChanged.connect(self._update_count)
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._sel_all_btn = QPushButton("全选")
        self._sel_all_btn.clicked.connect(lambda: self._set_all(True))
        self._sel_none_btn = QPushButton("取消全选")
        self._sel_none_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(self._sel_all_btn)
        btn_row.addWidget(self._sel_none_btn)

        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("countLabel")
        btn_row.addWidget(self._count_lbl)
        btn_row.addStretch(1)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton()
        self._ok_btn.setObjectName("dangerBtn")
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

        self._update_count()

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        blocked = self._list.blockSignals(True)
        try:
            for i in range(self._list.count()):
                self._list.item(i).setCheckState(state)
        finally:
            self._list.blockSignals(blocked)
        self._update_count()

    def _update_count(self) -> None:
        sel = len(self.selected_items())
        total = self._list.count()
        self._count_lbl.setText(f"已选 {sel} / {total} 项")
        self._ok_btn.setText(f"🗑 移入回收站（{sel} 项）")
        self._ok_btn.setEnabled(sel > 0)

    def selected_items(self) -> list[CleanupItem]:
        out: list[CleanupItem] = []
        for i in range(self._list.count()):
            li = self._list.item(i)
            if li.checkState() == Qt.CheckState.Checked:
                item = li.data(Qt.ItemDataRole.UserRole)
                if item is not None:
                    out.append(item)
        return out
