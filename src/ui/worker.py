"""后台解码 worker（grid_view / anim_view 共用）。

QPixmap 不可跨线程创建，因此 worker 只返回 PIL.Image，由主线程转 QPixmap。

M45 阶段 A-1：渲染算法已搬到 core/render.py（不依赖 Qt，导出 GIF 等功能也能直接用）。
这里只剩「在后台线程里调用它，把每一帧通过信号发给界面」。
对外接口（构造参数、frame_ready / finished 信号、all_blank 属性）完全没变，
grid_view / anim_view / 测试都不用改。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.render import SequenceRender


class DecodeWorker(QThread):
    """把多层帧序列解码为 PIL.Image 列表。"""

    frame_ready = Signal(int, object, str)  # idx, PIL.Image, label
    finished = Signal(int)                  # total

    def __init__(
        self,
        layers: list[list[Path]],
        labels: list[str] | None = None,
        flat_mask: list[bool] | None = None,
        fx_offsets: dict[int, tuple[int, int]] | None = None,
        canvas_align: bool = False,
    ):
        super().__init__()
        self._layers = layers
        self._labels = labels
        self._flat_mask = flat_mask
        self._fx_offsets = fx_offsets
        self._canvas_align = canvas_align   # M34：固定画布对齐模式
        # M36：全部帧均无有效像素（全透明占位图）时为 True，供视图层显示空帧提示
        self.all_blank = False

    def run(self) -> None:
        # 第一步：预计算（打开所有帧文件、算对齐）——在后台线程里做，不卡界面
        job = SequenceRender(self._layers, self._labels, self._flat_mask,
                             self._fx_offsets, self._canvas_align)
        self.all_blank = job.all_blank
        # 第二步：逐帧合成，每合成一帧就发给界面
        # should_stop：别处调用 requestInterruption() 时可以提前结束（目前没人调用，行为不变）
        n = 0
        for i, img, label in job.frames(should_stop=self.isInterruptionRequested):
            self.frame_ready.emit(i, img, label)
            n += 1
        self.finished.emit(n)
