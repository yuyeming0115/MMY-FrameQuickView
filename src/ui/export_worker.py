"""导出后台线程（M45 阶段 B）。

导出要渲染几十帧大图再编码成 GIF，放在主线程会卡住界面，所以放到后台线程里做。
这里只负责「在后台调 core 的三步流水线，把结果用信号告诉界面」，算法都在 core 里。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.compose import LayerStack
from ..core.export import ExportOptions, export_frames
from ..core.render import SequenceRender


class ExportWorker(QThread):
    done = Signal(str, dict)     # (导出文件路径, 导出信息：帧数/尺寸/每帧毫秒/实际帧率)
    failed = Signal(str)         # 失败原因（给人看的一句话）

    def __init__(self, stack: LayerStack, opts: ExportOptions, out_path: Path,
                 canvas_align: bool, parent=None):
        super().__init__(parent)
        self._stack = stack
        self._opts = opts
        self._out_path = Path(out_path)
        self._canvas_align = canvas_align

    def run(self) -> None:
        try:
            # ② 渲染：和预览走同一份代码（core/render.py）
            job = SequenceRender(self._stack.layers, flat_mask=self._stack.flat_mask,
                                 fx_offsets=self._stack.fx_offsets,
                                 canvas_align=self._canvas_align)
            frames = [img for _i, img, _label in
                      job.frames(should_stop=self.isInterruptionRequested)]
            if self.isInterruptionRequested():
                return                                   # 窗口关闭等原因中途取消：什么都不写
            # ③ 写文件：裁剪 / 放大 / 背景 / 编码
            info = export_frames(frames, self._out_path, self._opts)
            self.done.emit(str(self._out_path), info)
        except ValueError as e:                          # 已知情况（如全是空帧）：直接显示原因
            self.failed.emit(str(e))
        except OSError as e:                             # 磁盘相关：没权限、路径太长、被占用…
            self.failed.emit(f"写文件失败：{e}")
        except Exception as e:                           # 其他意外：带上类型名，方便排查
            self.failed.emit(f"{type(e).__name__}: {e}")
