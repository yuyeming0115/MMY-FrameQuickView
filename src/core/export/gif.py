"""GIF 写入（M45 阶段 B）。

GIF 格式的两个限制，以及这里的对策：

1. **每帧最多 256 色** —— 多部件 + 特效容易出现色带，而且如果每帧各自减色，
   帧与帧之间颜色会跳（闪色）。
   对策：先把**全部帧**的像素放在一起算一个共用调色板，再让每帧都用这个调色板。

2. **透明只有「全透明 / 不透明」两档** —— 影子、发光这类半透明边缘没法保留。
   对策：默认铺纯色背景（不透明，半透明边缘按背景色混合，效果和工具里看到的一样）；
   选「透明」时按 alpha_threshold 一刀切（低于阈值 = 全透明），并在弹窗里提示改用 APNG。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import ExportOptions, flatten, frame_ms

TRANSPARENT = 255            # 透明模式下，调色板最后一格（第 255 号）专门表示「透明」
_MAX_SAMPLE = 1_000_000      # 算调色板时最多取这么多个像素（大画布 × 多帧时抽样，速度快很多）


def _build_palette(pixels: np.ndarray, colors: int) -> list[int]:
    """从一堆 RGB 像素（N×3）里算出 colors 种代表色，返回 768 个数的调色板列表。"""
    step = max(1, len(pixels) // _MAX_SAMPLE)
    sample = Image.fromarray(np.ascontiguousarray(pixels[::step]).reshape(-1, 1, 3), "RGB")
    pal_img = sample.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                              dither=Image.Dither.NONE)
    pal = list(pal_img.getpalette() or [])[:colors * 3]
    first = pal[:3] or [0, 0, 0]
    # 不够 256 格的部分用第 0 格的颜色补齐：
    # 这样就算某个像素被匹配到补出来的格子，显示出来的颜色也是对的（见下面的修正）
    while len(pal) < 768:
        pal.extend(first)
    return pal[:768]


def _quantize(rgb: Image.Image, palette: list[int], dither: bool) -> np.ndarray:
    """把一帧 RGB 图片按共用调色板转成「调色板编号」数组。"""
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(palette)
    q = rgb.quantize(palette=pal_img,
                     dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    return np.array(q, dtype=np.uint8)


def write_gif(frames: list[Image.Image], out_path: Path, opts: ExportOptions) -> None:
    """frames 是 prepare_frames 处理过的 RGBA 帧（尺寸都一样）。"""
    ms = frame_ms("gif", opts.fps)

    if opts.background:
        # ---------------- 纯色背景（默认）----------------
        rgbs = flatten(frames, opts.background)
        arrs = [np.asarray(f) for f in rgbs]
        palette = _build_palette(np.concatenate([a.reshape(-1, 3) for a in arrs]), 256)
        out = []
        for a in arrs:
            idx = _quantize(Image.fromarray(a, "RGB"), palette, opts.dither)
            im = Image.fromarray(idx, "P")
            im.putpalette(palette)
            out.append(im)
        # disposal=1（保留上一帧再画下一帧）：每帧都是不透明的整图，这样最稳；
        # 如果用 disposal=2，部分看图软件会把没变化的区域显示成透明
        save_kw = dict(disposal=1)
    else:
        # ---------------- 透明 ----------------
        arrs = [np.asarray(f) for f in frames]
        masks = [a[..., 3] >= opts.alpha_threshold for a in arrs]      # True = 这个像素要显示
        opaque = [a[..., :3][m] for a, m in zip(arrs, masks) if m.any()]
        if not opaque:
            raise ValueError(f"所有像素的透明度都低于 {opts.alpha_threshold}，导出的 GIF 会是全透明的")
        # 只用要显示的像素算 255 种颜色（第 255 格留给透明）
        palette = _build_palette(np.concatenate(opaque), 255)
        palette[TRANSPARENT * 3:TRANSPARENT * 3 + 3] = palette[0:3]   # 第 255 格 = 第 0 格的颜色
        out = []
        for a, m in zip(arrs, masks):
            idx = _quantize(Image.fromarray(np.ascontiguousarray(a[..., :3]), "RGB"),
                            palette, opts.dither)
            idx[idx == TRANSPARENT] = 0          # 要显示的像素万一匹配到第 255 格 → 换成颜色相同的第 0 格
            idx[~m] = TRANSPARENT                # 不显示的像素 → 透明格
            im = Image.fromarray(idx, "P")
            im.putpalette(palette)
            out.append(im)
        # disposal=2（每帧播完清空）：不清空的话，透明背景上会留下上一帧的残影
        save_kw = dict(disposal=2, transparency=TRANSPARENT)

    if opts.loop:
        save_kw["loop"] = 0                      # 0 = 无限循环；不写 loop = 只播一次
    out[0].save(out_path, format="GIF", save_all=True, append_images=out[1:],
                duration=ms, optimize=False, **save_kw)
