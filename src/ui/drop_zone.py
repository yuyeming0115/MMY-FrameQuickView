"""拖拽区：接收文件夹拖入 + 匹配表菜单入口。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton


class DropZone(QFrame):
    folder_dropped = Signal(Path)
    reload_namemap_requested = Signal()   # 重新加载匹配表
    pick_namemap_requested = Signal()     # 选择匹配表文件…

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self._label = QLabel("⬇ 拖入部件文件夹 / 父级目录（任意位置均可拖入）")
        self._label.setObjectName("dropHint")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        layout.addWidget(self._label)
        layout.addStretch(1)

        # ⚙ 菜单按钮：合并原「文件」菜单的两项功能
        self._menu_btn = QToolButton()
        self._menu_btn.setText("⚙ ID")
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setStyleSheet(
            "QToolButton { color: #96A1AD; font-size: 16px; padding: 2px 6px;"
            " border: none; background: transparent; }"
            "QToolButton:hover { color: #E8E4D9; }"
            "QToolButton::menu-indicator { image: none; }"
        )
        menu = QMenu(self._menu_btn)
        menu.setStyleSheet(
            "QMenu { background: #2A2E33; color: #E8E4D9; border: 1px solid #3A3F46; }"
            "QMenu::item:selected { background: #D4AF37; color: #1E2023; }"
        )
        act_reload = menu.addAction("🔄 重新加载匹配表")
        act_reload.triggered.connect(self.reload_namemap_requested.emit)
        act_pick = menu.addAction("📁 选择匹配表文件…")
        act_pick.triggered.connect(self.pick_namemap_requested.emit)
        self._menu_btn.setMenu(menu)
        layout.addWidget(self._menu_btn)

        self._label.setStyleSheet("color: #96A1AD; font-size: 14px;")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #3A3F46; border-radius: 6px;"
            " background: rgba(255,255,255,0.02); }"
        )

    def set_current(self, text: str) -> None:
        self._label.setText(f"⬇ 拖入部件文件夹 / 父级目录　·　当前: {text}")
        self._label.setStyleSheet("color: #E8E4D9; font-size: 14px;")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_dir():
                    self.folder_dropped.emit(p)
                    event.acceptProposedAction()
                    return
