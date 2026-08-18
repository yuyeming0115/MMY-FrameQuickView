"""A 区：序列帧网格显示（M1 占位，M2 实现 100% + 包围盒裁剪 + 叠层合成）。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class GridView(QFrame):
    frame_clicked = Signal(int)  # 帧索引 → B 区跳转（M3 接线）

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._title = QLabel("A · 序列帧网格（100% 原尺寸 · 包围盒裁剪）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        self._body = QLabel("M2 实现：叠层合成网格")
        self._body.setStyleSheet("color: #96A1AD; font-size: 14px;")
        layout.addWidget(self._title)
        layout.addWidget(self._body, 1)

    def show_frames(self, count: int) -> None:
        self._body.setText(f"已识别 {count} 帧（网格渲染在 M2 接入）")
