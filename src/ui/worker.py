"""后台解码 worker（grid_view / anim_view 共用）。

QPixmap 不可跨线程创建，因此 worker 只返回 PIL.Image，由主线程转 QPixmap。
"""
from __future__ import annotations

import sys
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
        # 防御：跳过磁盘上不存在的帧文件，避免后台线程抛 FileNotFoundError 崩溃。
        missing = [str(p) for p in all_paths if not Path(p).is_file()]
        if missing:
            print(f"[DecodeWorker] 跳过 {len(missing)} 个不存在的帧文件，例如: {missing[0]}",
                  file=sys.stderr)
        existing = [p for p in all_paths if Path(p).is_file()]
        if not existing:
            self.finished.emit(0)
            return
        bbox = sequence_union_bbox(existing) or (0, 0, 1, 1)
        n = max((len(layer) for layer in self._layers), default=0)
        for i in range(n):
            per = [
                (layer[i] if (i < len(layer) and layer[i] and Path(layer[i]).is_file()) else None)
                for layer in self._layers
            ]
            img = composite_layers(per, bbox)
            # 默认 label：取该帧首个真实存在的文件名（去后缀），
            # 反映"同方向跨动作连续编号"的真实帧号（如 attack=0017-0024、skill=0025-0032），
            # 而不是从 0001 重启的序号。
            if self._labels and i < len(self._labels):
                label = self._labels[i]
            else:
                label = next(
                    (Path(p).stem for p in per if p is not None),
                    f"{i + 1:04d}",
                )
            self.frame_ready.emit(i, img, label)
        self.finished.emit(n)
