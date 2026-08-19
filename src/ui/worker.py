"""后台解码 worker（grid_view / anim_view 共用）。

QPixmap 不可跨线程创建，因此 worker 只返回 PIL.Image，由主线程转 QPixmap。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.imageops import composite_layers, sequence_union_bbox


class DecodeWorker(QThread):
    """把多层帧序列解码为 PIL.Image 列表。"""

    frame_ready = Signal(int, object, str)  # idx, PIL.Image, label
    finished = Signal(int)                  # total

    def __init__(self, layers: list[list[Path]], labels: list[str] | None = None):
        super().__init__()
        self._layers = layers
        self._labels = labels

    def run(self) -> None:
        all_paths = [p for layer in self._layers for p in layer if p]
        bbox = sequence_union_bbox(all_paths) or (0, 0, 1, 1)
        n = max((len(layer) for layer in self._layers), default=0)
        for i in range(n):
            per = [layer[i] if i < len(layer) else None for layer in self._layers]
            img = composite_layers(per, bbox)
            label = self._labels[i] if self._labels and i < len(self._labels) else f"{i + 1:04d}"
            self.frame_ready.emit(i, img, label)
        self.finished.emit(n)
