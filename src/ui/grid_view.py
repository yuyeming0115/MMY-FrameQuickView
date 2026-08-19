"""A 区：序列帧网格显示（M2）。

- 100% 原尺寸、透明无边框、按并集 bbox 裁剪（非破坏性）
- 同 ID 叠层：传多层帧序列，按 layer_order 从下到上合成到统一窗口
- 解码/合成放在 QThread 后台，避免卡 UI（逐帧解码见 imageops 注释）
- 点击某帧 → frame_clicked(index)（M3 接 B 区跳转）
"""
from __future__ import annotations

import numpy as np
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget, QGridLayout,
)

from ..core.imageops import composite_layers, sequence_union_bbox

COLS = 8


def _img_to_pixmap(img) -> QPixmap:
    """PIL RGBA → QPixmap（在主线程执行，QPixmap 不可跨线程）。"""
    arr = np.asarray(img.convert("RGBA"))
    h, w = arr.shape[:2]
    qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class _FrameCell(QLabel):
    clicked = Signal(int)

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self.setStyleSheet("background: transparent; border: none;")

    def set_pixmap(self, pix: QPixmap) -> None:
        self.setPixmap(pix)
        self.setFixedSize(pix.size())

    def mouseReleaseEvent(self, event) -> None:
        self.clicked.emit(self._idx)
        super().mouseReleaseEvent(event)


class _GridWorker(QThread):
    frame_ready = Signal(int, object, str)
    finished = Signal(int)

    def __init__(self, layers):
        super().__init__()
        self._layers = layers

    def run(self) -> None:
        all_paths = [p for layer in self._layers for p in layer if p]
        bbox = sequence_union_bbox(all_paths) or (0, 0, 1, 1)
        n = max((len(layer) for layer in self._layers), default=0)
        for i in range(n):
            per = [layer[i] if i < len(layer) else None for layer in self._layers]
            img = composite_layers(per, bbox)
            self.frame_ready.emit(i, img, f"{i + 1:04d}")
        self.finished.emit(n)


class GridView(QFrame):
    frame_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._title = QLabel("A · 序列帧网格（100% 原尺寸 · 并集 bbox · 透明）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        layout.addWidget(self._title)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "background: #1E2023; border: 1px solid #3A3F46; border-radius: 6px;"
        )
        self._viewport = QWidget()
        self._grid = QGridLayout(self._viewport)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(4)
        self._scroll.setWidget(self._viewport)
        layout.addWidget(self._scroll, 1)

        self._worker: _GridWorker | None = None
        self._cells: list[_FrameCell] = []

    def show_sequence(self, layers: list[list[Path]]) -> None:
        """layers[0] 为最底层；单层即普通序列，多层即同 ID 叠层合成。"""
        self._clear()
        if not layers or all(len(layer) == 0 for layer in layers):
            self._title.setText("A · 序列帧网格（无帧）")
            return
        self._title.setText("A · 序列帧网格 · 加载中…")
        self._worker = _GridWorker(layers)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _clear(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        self._worker = None
        for c in self._cells:
            self._grid.removeWidget(c)
            c.deleteLater()
        self._cells = []

    def _on_frame(self, idx: int, img, _label: str) -> None:
        cell = _FrameCell(idx)
        cell.set_pixmap(_img_to_pixmap(img))
        cell.clicked.connect(lambda i=idx: self.frame_clicked.emit(i))
        self._grid.addWidget(cell, idx // COLS, idx % COLS)
        self._cells.append(cell)

    def _on_done(self, total: int) -> None:
        self._title.setText(f"A · 序列帧网格（{total} 帧 · 100% 原尺寸 · 并集 bbox）")
