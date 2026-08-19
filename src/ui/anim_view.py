"""B 区：GIF 动画预览（M1 占位，M3 实现播放/FPS/步进/棋盘格背景）。"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class AnimView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._title = QLabel("B · GIF 动画预览（并集 bbox · 防抖动）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        self._body = QLabel("M3 实现：播放 / FPS / 逐帧步进 / 棋盘格背景")
        self._body.setStyleSheet("color: #96A1AD; font-size: 14px;")
        self._body.setMinimumWidth(220)
        layout.addWidget(self._title)
        layout.addWidget(self._body, 1)
