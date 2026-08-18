"""拖拽区：接收文件夹拖入。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class DropZone(QFrame):
    folder_dropped = Signal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self._label = QLabel("⬇ 拖入部件文件夹 / 父级目录（自动扫描一级子文件夹）")
        self._label.setObjectName("dropHint")
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        self._label.setStyleSheet("color: #96A1AD; font-size: 14px;")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #3A3F46; border-radius: 8px;"
            " background: rgba(255,255,255,0.02); padding: 6px; }"
        )

    def set_current(self, text: str) -> None:
        self._label.setText(f"⬇ 拖入部件文件夹 / 父级目录　·　当前: {text}")
        self._label.setStyleSheet("color: #E8E4D9; font-size: 14px;")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_dir():
                    self.folder_dropped.emit(p)
                    event.acceptProposedAction()
                    return
