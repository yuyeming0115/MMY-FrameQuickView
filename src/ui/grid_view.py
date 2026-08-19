"""A 区：序列帧网格显示（M2）。

- 100% 原尺寸、透明无边框、按并集 bbox 裁剪（非破坏性）
- 同 ID 叠层：传多层帧序列，按 layer_order 从下到上合成到统一窗口
- 解码/合成放在 QThread 后台，避免卡 UI（逐帧解码见 imageops 注释）
- 点击某帧 → frame_clicked(index)（M3 接 B 区跳转）
- 布局采用 FlowLayout：按帧实际尺寸从左到右流动排列，视口宽度不足时自动换行
"""
from __future__ import annotations

import numpy as np
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QSize, QRect, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QLabel, QLayout, QLayoutItem, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .worker import DecodeWorker


class FlowLayout(QLayout):
    """Qt 官方示例风格的流式布局：子控件按尺寸横向排列，宽度不足自动换行。"""

    def __init__(self, parent=None, margin=0, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list[QLayoutItem] = []

    def __del__(self):
        item = self.takeAt(0)
        while item is not None:
            item = self.takeAt(0)

    def addItem(self, item: QLayoutItem):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins()
        size += QSize(margin.left() + margin.right(), margin.top() + margin.bottom())
        return size

    def _do_layout(self, rect, test_only=False) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            space_x = self._h_spacing
            space_y = self._v_spacing
            if wid is not None:
                space_x += wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
                space_y += wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom


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
        self._flow = FlowLayout(self._viewport, margin=8, h_spacing=6, v_spacing=6)
        self._scroll.setWidget(self._viewport)
        layout.addWidget(self._scroll, 1)

        self._worker: DecodeWorker | None = None
        self._cells: list[_FrameCell] = []

    def show_sequence(self, layers: list[list[Path]]) -> None:
        """layers[0] 为最底层；单层即普通序列，多层即同 ID 叠层合成。"""
        self._clear()
        if not layers or all(len(layer) == 0 for layer in layers):
            self._title.setText("A · 序列帧网格（无帧）")
            return
        self._title.setText("A · 序列帧网格 · 加载中…")
        self._worker = DecodeWorker(layers)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _clear(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        self._worker = None
        for c in self._cells:
            self._flow.removeWidget(c)
            c.deleteLater()
        self._cells = []

    def _on_frame(self, idx: int, img, _label: str) -> None:
        cell = _FrameCell(idx)
        cell.set_pixmap(_img_to_pixmap(img))
        cell.clicked.connect(lambda i=idx: self.frame_clicked.emit(i))
        self._flow.addWidget(cell)
        self._cells.append(cell)

    def _on_done(self, total: int) -> None:
        self._title.setText(f"A · 序列帧网格（{total} 帧 · 100% 原尺寸 · 并集 bbox · 自动换行）")
