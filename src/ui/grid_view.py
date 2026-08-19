"""A 区：序列帧网格显示（M2）。

- 100% 原尺寸、透明无边框、按并集 bbox 裁剪（非破坏性）
- 同 ID 叠层：传多层帧序列，按 layer_order 从下到上合成到统一窗口
- 解码/合成放在 QThread 后台，避免卡 UI（逐帧解码见 imageops 注释）
- 点击某帧 → frame_clicked(index)（M3 接 B 区跳转）
- 布局采用 FlowLayout：按帧实际尺寸从左到右流动排列，视口宽度不足时自动换行
"""
from __future__ import annotations

import math
import numpy as np
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QSize, QRect, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QLayoutItem, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .hover_scroll import enable_hover_scroll
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


class _FrameCell(QFrame):
    """单个帧格：图片居中 + 底部帧名标签，固定尺寸保证对齐。"""
    clicked = Signal(int)

    # 样式常量（与 B 区按钮风格一致）
    _LABEL_H = 18          # 底部标签高度
    _PAD = 2               # 图片区域内边距

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._pix: QPixmap | None = None
        self._orig_pix: QPixmap | None = None   # 原图（未缩放），自适应模式按此重算
        self._label_text = ""
        self._cell_size: QSize | None = None  # 统一格子尺寸（含标签）
        self.setStyleSheet(
            "background: transparent; border: 1px solid transparent; border-radius: 3px;"
        )
        self.setMouseTracking(True)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix = pix
        self._orig_pix = pix

    def set_label(self, text: str) -> None:
        self._label_text = text

    def set_cell_size(self, size: QSize) -> None:
        """统一格子尺寸（图片区 + 标签区）。所有 cell 用同一尺寸保证对齐。"""
        self._cell_size = size
        self.setFixedSize(size)
        self.update()

    def rescale_pixmap(self, target_w: int, target_h: int) -> None:
        """自适应模式：按目标图片区尺寸缩放原图（保持比例，居中）。

        target_w/target_h = 图片区可用尺寸（已扣除内边距和标签高度）。
        """
        if self._orig_pix is None or self._orig_pix.isNull():
            return
        scaled = self._orig_pix.scaled(
            target_w, target_h,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self._pix = scaled
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self.clicked.emit(self._idx)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QFont, QPainter

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = self.rect()
        # 图片区（顶部，留出底部标签高度）
        img_rect = QRect(
            self._PAD, self._PAD,
            rect.width() - 2 * self._PAD,
            rect.height() - self._LABEL_H - self._PAD,
        )

        # 画图片（居中、100% 原尺寸，大于区域时按比例缩放但通常不会发生——bbox 已裁剪）
        if self._pix is not None and not self._pix.isNull():
            pix = self._pix
            # 居中绘制：图片左上角对齐 img_rect 左上角
            x = img_rect.x() + (img_rect.width() - pix.width()) // 2
            y = img_rect.y() + (img_rect.height() - pix.height()) // 2
            p.drawPixmap(x, y, pix)

        # 底部标签
        label_rect = QRect(
            0, rect.height() - self._LABEL_H,
            rect.width(), self._LABEL_H,
        )
        p.setPen(QColor("#96A1AD"))
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        p.drawText(label_rect, Qt.AlignCenter, self._label_text)


class GridView(QFrame):
    frame_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        # 标题行：标题 + 右侧「原图/自适应」切换按钮
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel("A · 序列帧网格（100% 原尺寸 · 并集 bbox · 透明）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        header.addWidget(self._title)
        header.addStretch()
        self._mode_btn = QPushButton("自适应")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setCursor(Qt.PointingHandCursor)
        self._mode_btn.setFixedHeight(22)
        self._mode_btn.setStyleSheet(
            "QPushButton { color: #96A1AD; background: #2A2E33; border: 1px solid #3A3F46;"
            " border-radius: 4px; padding: 0 10px; font-size: 12px; }"
            "QPushButton:hover { color: #E8E4D9; border-color: #4A4F56; }"
            "QPushButton:checked { color: #1E2023; background: #D4AF37; border-color: #D4AF37; }"
        )
        self._mode_btn.toggled.connect(self._on_mode_toggled)
        header.addWidget(self._mode_btn)
        layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        enable_hover_scroll(self._scroll)  # 鼠标离开自动隐藏滚动条
        # 细滚动条 + 透明轨道：内容不溢出时 AsNeeded 自动隐藏，溢出时才滑出 8px 细条。
        self._scroll.setStyleSheet(
            "QScrollArea { background: #1E2023; border: 1px solid #3A3F46; border-radius: 6px; }"
            "QScrollBar:vertical, QScrollBar:horizontal { background: transparent; border: none; margin: 0; }"
            "QScrollBar:vertical { width: 8px; }"
            "QScrollBar:horizontal { height: 8px; }"
            "QScrollBar::handle { background: #4A4F56; border-radius: 4px; min-height: 24px; min-width: 24px; }"
            "QScrollBar::handle:hover { background: #5A6068; }"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }"
        )
        self._viewport = QWidget()
        self._flow = FlowLayout(self._viewport, margin=8, h_spacing=6, v_spacing=6)
        self._scroll.setWidget(self._viewport)
        layout.addWidget(self._scroll, 1)

        self._worker: DecodeWorker | None = None
        self._cells: list[_FrameCell] = []
        self._pending: list[tuple[int, object, str]] = []  # (idx, PIL.Image, label) 缓存
        # 解码完成后保存，供「原图/自适应」切换重算用
        self._loaded: list[tuple[QPixmap, str, int]] = []  # (pixmap, label, idx)
        self._max_w = self._max_h = 0  # 原图最大尺寸

    def show_sequence(self, layers: list[list[Path]]) -> None:
        """layers[0] 为最底层；单层即普通序列，多层即同 ID 叠层合成。

        切换体验（M6）：**延迟清空 + 整体替换**——保留旧画面直到新序列
        全部解码完成，避免「先清空再逐帧生长」的闪烁感。期间标题显示解码中。
        """
        self._stop_worker()
        if not layers or all(len(layer) == 0 for layer in layers):
            self._clear_cells()
            self._title.setText("A · 序列帧网格（无帧）")
            return
        self._title.setText("A · 序列帧网格 · 解码中…")
        self._pending = []
        self._worker = DecodeWorker(layers)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        self._worker = None

    def _clear_cells(self) -> None:
        for c in self._cells:
            self._flow.removeWidget(c)
            c.deleteLater()
        self._cells = []

    def _on_frame(self, idx: int, img, label: str) -> None:
        # sender() 竞态防护：旧 worker 残留的 queued 信号直接忽略
        if self._worker is None or self.sender() is not self._worker:
            return
        self._pending.append((idx, img, label))

    def _on_done(self, total: int) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        self._clear_cells()
        if not self._pending:
            self._title.setText("A · 序列帧网格（无帧）")
            return

        # 计算统一格子尺寸：取所有帧最大宽高 + 标签高度 + 内边距
        # （并集 bbox 后各帧尺寸本应一致，取 max 兼容异常情况）
        max_w = max_h = 0
        pixmaps: list[QPixmap] = []
        for _idx, img, _label in self._pending:
            pix = _img_to_pixmap(img)
            pixmaps.append(pix)
            max_w = max(max_w, pix.width())
            max_h = max(max_h, pix.height())

        # 保存解码结果，供「原图/自适应」切换重算
        self._loaded = [(pixmaps[i], label, idx) for i, (idx, _img, label) in enumerate(self._pending)]
        self._max_w, self._max_h = max_w, max_h

        self._build_cells()
        pixmaps.clear()
        self._pending = []
        mode = "自适应一屏全显" if self._mode_btn.isChecked() else "100% 原尺寸"
        self._title.setText(f"A · 序列帧网格（{total} 帧 · {mode} · 并集 bbox）")

    def _build_cells(self) -> None:
        """根据当前模式（原图/自适应）构建所有 cell。"""
        self._clear_cells()
        if not self._loaded:
            return
        if self._mode_btn.isChecked():
            # 自适应模式：按视口宽度算 4 列的格子尺寸，图片缩放适应
            self._apply_fit_layout()
        else:
            # 原图模式：100% 原尺寸
            cell_w = self._max_w + 2 * _FrameCell._PAD
            cell_h = self._max_h + 2 * _FrameCell._PAD + _FrameCell._LABEL_H
            cell_size = QSize(cell_w, cell_h)
            for pix, label, idx in self._loaded:
                cell = _FrameCell(idx)
                cell.set_pixmap(pix)
                cell.set_label(label)
                cell.set_cell_size(cell_size)
                cell.clicked.connect(lambda i=idx: self.frame_clicked.emit(i))
                self._flow.addWidget(cell)
                self._cells.append(cell)

    def _apply_fit_layout(self) -> None:
        """自适应模式：根据帧数和 A 区宽高动态算行列，所有帧一屏全显，格子最大化。"""
        if not self._loaded:
            return
        N = len(self._loaded)
        margin = 8
        h_spacing = 6
        v_spacing = 6
        # 可用宽高（预留滚动条宽度，避免算出滚动后溢出导致再加滚动条的抖动）
        avail_w = self._scroll.viewport().width() - 2 * margin
        avail_h = self._scroll.viewport().height() - 2 * margin
        if avail_w < 100 or avail_h < 100:
            return

        # 原图宽高比（所有帧并集 bbox 后尺寸应一致，取 max 兜底）
        ratio = (self._max_w / self._max_h) if self._max_h > 0 else 1.0

        # 遍历可行列数，找让格子图片显示最大化的方案
        # 约束：rows = ceil(N/cols)，总高 <= avail_h，保持原图比例不变形
        best = None  # (img_w, cols, rows, img_area_w, img_area_h, cell_w, cell_h)
        for cols in range(1, N + 1):
            rows = math.ceil(N / cols)
            cell_w = (avail_w - (cols - 1) * h_spacing) / cols
            cell_h = (avail_h - (rows - 1) * v_spacing) / rows
            img_area_w = cell_w - 2 * _FrameCell._PAD
            img_area_h = cell_h - 2 * _FrameCell._PAD - _FrameCell._LABEL_H
            if img_area_w <= 0 or img_area_h <= 0:
                continue
            # 保持原图比例：取宽高约束的较小缩放
            img_w = min(img_area_w, img_area_h * ratio)
            img_h = img_w / ratio
            if best is None or img_w > best[0]:
                best = (img_w, cols, rows, img_area_w, img_area_h, cell_w, cell_h)
        if best is None:
            return
        _, cols, rows, img_area_w, img_area_h, cell_w, cell_h = best
        # 实际图片显示尺寸（保持比例，居中于格子）
        img_w = min(img_area_w, img_area_h * ratio)
        img_h = img_w / ratio
        cell_size = QSize(int(cell_w), int(cell_h))
        img_area_pw = int(img_w)
        img_area_ph = int(img_h)
        for pix, label, idx in self._loaded:
            cell = _FrameCell(idx)
            cell.set_pixmap(pix)  # 存原图
            cell.set_label(label)
            cell.set_cell_size(cell_size)
            cell.rescale_pixmap(img_area_pw, img_area_ph)  # 按比例缩放显示
            cell.clicked.connect(lambda i=idx: self.frame_clicked.emit(i))
            self._flow.addWidget(cell)
            self._cells.append(cell)

    def _on_mode_toggled(self, checked: bool) -> None:
        """切换原图/自适应。"""
        self._mode_btn.setText("原图" if checked else "自适应")
        if self._loaded:
            self._build_cells()
            total = len(self._loaded)
            mode = "自适应一屏全显" if checked else "100% 原尺寸"
            self._title.setText(f"A · 序列帧网格（{total} 帧 · {mode} · 并集 bbox）")

    def resizeEvent(self, event) -> None:
        """视口宽度变化时，自适应模式重算布局。"""
        super().resizeEvent(event)
        if self._mode_btn.isChecked() and self._loaded:
            # 用 _build_cells 而非 _apply_fit_layout：前者会先 _clear_cells，
            # 避免 resize 反复触发导致 cell 无限累积。
            self._build_cells()
