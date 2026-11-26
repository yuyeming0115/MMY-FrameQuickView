"""APNG 写入（M45 阶段 B）。

APNG = 会动的 PNG：真彩色、完整半透明（影子、发光边缘都能保留），帧时长精确到毫秒。
缺点：文件比 GIF 大；少数老软件只显示第一帧（浏览器、新版聊天软件都支持）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, PngImagePlugin

from . import ExportOptions, flatten, frame_ms


def write_apng(frames: list[Image.Image], out_path: Path, opts: ExportOptions) -> None:
    """frames 是 prepare_frames 处理过的 RGBA 帧（尺寸都一样）。"""
    if opts.background:
        imgs = [f.convert("RGBA") for f in flatten(frames, opts.background)]
    else:
        imgs = frames
    imgs[0].save(
        out_path, format="PNG", save_all=True, append_images=imgs[1:],
        duration=frame_ms("apng", opts.fps),
        loop=0 if opts.loop else 1,                        # 0 = 无限循环，1 = 播一次
        disposal=PngImagePlugin.Disposal.OP_BACKGROUND,    # 每帧播完清空（透明背景不留残影）
        blend=PngImagePlugin.Blend.OP_SOURCE,              # 新帧直接覆盖，不和上一帧混合
    )
