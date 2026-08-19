"""B 区：GIF 动画预览（M3）。

- 100% 原尺寸、透明无边框、并集 bbox 对齐
- 播放 / 暂停、FPS 滑杆 (1–60)、上一帧 / 下一帧、循环模式
- 默认透明融入 UI，可切换棋盘格背景（方便检查 alpha 毛边）
- A 区点击某帧 → goto_frame(idx) 跳转并暂停
"""
from __future__ import annotations

import numpy as np
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from .worker import DecodeWorker


FPS_MIN, FPS_MAX = 1, 60


def _img_to_pixmap(img) -> QPixmap:
    arr = np.asarray(img.convert("RGBA"))
    h, w = arr.shape[:2]
    qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class _AnimCanvas(QLabel):
    """带可选棋盘格背景的画布，居中绘制当前帧。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: transparent; border: none;")
        self._pixmap: QPixmap | None = None
        self._checker = False
        self._cell = 16

    def set_checker(self, enabled: bool) -> None:
        self._checker = enabled
        self.update()

    def set_frame(self, pix: QPixmap | None) -> None:
        self._pixmap = pix
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        if self._checker:
            w, h = self.width(), self.height()
            c1, c2 = QColor(40, 40, 40), QColor(60, 60, 60)
            for y in range(0, h, self._cell):
                for x in range(0, w, self._cell):
                    brush = QBrush(c1 if ((x // self._cell) + (y // self._cell)) % 2 == 0 else c2)
                    painter.fillRect(x, y, self._cell, self._cell, brush)

        if self._pixmap is not None:
            x = (self.width() - self._pixmap.width()) // 2
            y = (self.height() - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)

        painter.end()


class AnimView(QFrame):
    frame_clicked = Signal(int)  # B 区点击当前帧时发出（与 A 区保持一致）

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._title = QLabel("B · GIF 动画预览（并集 bbox · 防抖动）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        layout.addWidget(self._title)

        self._canvas = _AnimCanvas()
        layout.addWidget(self._canvas, 1)

        # 方向/动作按钮矩阵占位（由 app.py 注入）
        self._matrix_container = QWidget()
        mcl = QVBoxLayout(self._matrix_container)
        mcl.setContentsMargins(0, 0, 0, 0)
        mcl.setSpacing(4)
        layout.addWidget(self._matrix_container)

        # 控制栏
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.setCheckable(True)
        self._play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self._play_btn)

        self._prev_btn = QPushButton("◀ 上一帧")
        self._prev_btn.clicked.connect(self._prev_frame)
        bar.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一帧 ▶")
        self._next_btn.clicked.connect(self._next_frame)
        bar.addWidget(self._next_btn)

        bar.addWidget(QLabel("FPS"))
        self._fps_slider = QSlider(Qt.Horizontal)
        self._fps_slider.setRange(FPS_MIN, FPS_MAX)
        self._fps_slider.setValue(12)
        self._fps_slider.setMaximumWidth(120)
        self._fps_slider.valueChanged.connect(self._on_fps_changed)
        bar.addWidget(self._fps_slider)
        self._fps_label = QLabel("12")
        self._fps_label.setStyleSheet("color: #D4AF37; min-width: 20px;")
        bar.addWidget(self._fps_label)

        self._loop_chk = QCheckBox("循环")
        self._loop_chk.setChecked(True)
        self._loop_chk.setStyleSheet("color: #96A1AD;")
        bar.addWidget(self._loop_chk)

        self._checker_chk = QCheckBox("棋盘格")
        self._checker_chk.setChecked(False)
        self._checker_chk.setStyleSheet("color: #96A1AD;")
        self._checker_chk.toggled.connect(self._canvas.set_checker)
        bar.addWidget(self._checker_chk)

        bar.addStretch(1)
        layout.addLayout(bar)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self._worker: DecodeWorker | None = None
        self._frames: list[QPixmap] = []
        self._labels: list[str] = []
        self._index = 0
        self._fps = 12
        self._on_fps_changed(12)
        self._pending_frames: list[QPixmap] = []   # 新序列解码中缓存（整体替换用）
        self._pending_labels: list[str] = []

    # ---------------- 公共 API ----------------
    def set_matrix_widget(self, widget: QWidget) -> None:
        """注入方向/动作按钮矩阵，显示在 B 区画布与控制栏之间。"""
        layout = self._matrix_container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        layout.addWidget(widget)

    def show_sequence(self, layers: list[list[Path]], start_idx: int = 0) -> None:
        """layers[0] 为最底层；多层即同 ID 叠层合成。

        切换体验（M6）：**延迟清空 + 整体替换**——保留旧动画继续播放直到
        新序列全部解码完成，避免切换瞬间画布闪黑。
        """
        self._stop_worker()
        if not layers or all(len(layer) == 0 for layer in layers):
            self._frames = []
            self._labels = []
            self._index = 0
            self._canvas.set_frame(None)
            self._play_btn.setChecked(False)
            self._play_btn.setText("▶ 播放")
            self._timer.stop()
            self._title.setText("B · GIF 动画预览（无帧）")
            return

        self._title.setText("B · GIF 动画预览 · 解码中…")
        self._pending_frames = []
        self._pending_labels = []
        self._worker = DecodeWorker(layers)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def goto_frame(self, idx: int) -> None:
        """跳转到指定帧并暂停，方便对照 A 区细节。"""
        self._pause()
        self._set_index(idx)

    # ---------------- 内部 ----------------
    def _stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        self._worker = None

    def _on_frame(self, idx: int, img, label: str) -> None:
        # sender() 竞态防护：旧 worker 残留的 queued 信号直接忽略
        if self._worker is None or self.sender() is not self._worker:
            return
        self._pending_frames.append(_img_to_pixmap(img))
        self._pending_labels.append(label)

    def _on_done(self, total: int) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        self._frames = self._pending_frames
        self._labels = self._pending_labels
        self._pending_frames = []
        self._pending_labels = []
        self._title.setText(f"B · GIF 动画预览（{total} 帧 · 并集 bbox · 防抖动）")
        if not self._frames:
            return
        self._index = 0
        self._canvas.set_frame(self._frames[0])
        self._play_btn.setChecked(True)
        self._play_btn.setText("⏸ 暂停")
        self._timer.start()

    def _toggle_play(self, checked: bool) -> None:
        if not self._frames:
            self._play_btn.setChecked(False)
            return
        if checked:
            self._timer.start()
            self._play_btn.setText("⏸ 暂停")
        else:
            self._timer.stop()
            self._play_btn.setText("▶ 播放")

    def _pause(self) -> None:
        self._play_btn.setChecked(False)
        self._timer.stop()
        self._play_btn.setText("▶ 播放")

    def _advance(self) -> None:
        if not self._frames:
            return
        next_idx = self._index + 1
        if next_idx >= len(self._frames):
            if self._loop_chk.isChecked():
                next_idx = 0
            else:
                self._pause()
                return
        self._set_index(next_idx)

    def _prev_frame(self) -> None:
        if not self._frames:
            return
        self._pause()
        self._set_index((self._index - 1) % len(self._frames))

    def _next_frame(self) -> None:
        if not self._frames:
            return
        self._pause()
        self._set_index((self._index + 1) % len(self._frames))

    def _set_index(self, idx: int) -> None:
        self._index = max(0, min(idx, len(self._frames) - 1))
        self._canvas.set_frame(self._frames[self._index])

    def _on_fps_changed(self, value: int) -> None:
        self._fps = max(FPS_MIN, min(FPS_MAX, value))
        self._fps_label.setText(str(self._fps))
        self._timer.setInterval(int(1000 / self._fps))
