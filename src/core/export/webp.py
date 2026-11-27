"""WebP 动图写入（M45 阶段 C）。

WebP 动图 = 体积最小的「会动的图片」：真彩色 + 完整半透明，浏览器、Android、新版聊天软件都支持。
这里用**无损**模式：像素风/游戏美术不能有压缩糊边，无损的体积也已经比 APNG 小很多。
缺点：部分老图片软件（如旧版 Windows 照片查看器）不能播放，需要更广的兼容性请选 GIF。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import ExportOptions, flatten, frame_ms


def write_webp(frames: list[Image.Image], out_path: Path, opts: ExportOptions) -> None:
    """frames 是 prepare_frames 处理过的 RGBA 帧（尺寸都一样）。"""
    if opts.background:
        imgs = [f.convert("RGBA") for f in flatten(frames, opts.background)]
    else:
        imgs = frames
    imgs[0].save(
        out_path, format="WEBP", save_all=True, append_images=imgs[1:],
        duration=frame_ms("webp", opts.fps),
        loop=0 if opts.loop else 1,        # 0 = 无限循环
        lossless=True,                     # 无损：像素边缘不糊
        quality=100,                       # 无损模式下 quality 表示「压缩力度」，100 = 最小体积（慢一点）
        method=4,                          # 0-6：越大压得越小也越慢；4 是速度和体积的折中
    )
