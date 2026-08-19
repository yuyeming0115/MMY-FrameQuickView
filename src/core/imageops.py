"""图像操作：包围盒裁剪（非破坏性）、帧加载。

M1 仅提供 bbox 计算基础；A/B 区显示在 M2/M3 接入。
规则（v0.3 定稿）：
- 动画显示用「全序列并集 bbox」，帧间不抖动
- 网格统一用「全序列并集 bbox」窗口对齐（每格尺寸一致，可观察帧间位移/抖动）
- 叠层各层共享同一并集 bbox 窗口对齐，shadow 等底层部件固定最底
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

BBox = tuple[int, int, int, int]  # (left, top, right, bottom)，PIL crop 语义


def frame_bbox(path: Path) -> BBox | None:
    """单帧 alpha 有效像素包围盒；全透明返回 None。"""
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"))
    return array_bbox(rgba)


def array_bbox(rgba: np.ndarray) -> BBox | None:
    alpha = rgba[..., 3]
    ys, xs = np.nonzero(alpha)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def union_bbox(boxes: list[BBox | None]) -> BBox | None:
    """并集 bbox：动画/叠层的统一裁剪窗口。"""
    valid = [b for b in boxes if b is not None]
    if not valid:
        return None
    return (
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    )


def sequence_union_bbox(paths: list[Path]) -> BBox | None:
    """全序列并集 bbox（注意：逐帧解码，调用方应放工作线程）。

    任一帧文件在磁盘上不存在 / 损坏时跳过（返回 None），而非抛异常中断整个解码线程。
    """
    boxes: list[BBox | None] = []
    for p in paths:
        try:
            boxes.append(frame_bbox(p))
        except (FileNotFoundError, OSError, ValueError):
            boxes.append(None)
    return union_bbox(boxes)


def load_cropped(path: Path, bbox: BBox | None) -> Image.Image:
    """按 bbox 裁剪加载（仅内存操作，原文件不修改）。"""
    im = Image.open(path).convert("RGBA")
    if bbox is not None:
        im = im.crop(bbox)
    return im


def composite_layers(frame_paths: list[Path | None], bbox: BBox) -> Image.Image:
    """按层序合成到统一 bbox 窗口。

    - frame_paths 已按 layer_order 从下到上排序（如 shadow 在最前 = 最底层）
    - 元素为 None 表示该层此帧缺失，跳过（叠层时该帧此处透明）
    - 每帧先按自身 alpha bbox 裁剪，再按相对偏移 alpha_composite 到并集窗口，
      保证各层在同一坐标系对齐
    """
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for p in frame_paths:
        if p is None:
            continue
        lb = frame_bbox(p)
        if lb is None:
            continue
        im = Image.open(p).convert("RGBA").crop(lb)
        off = (lb[0] - bbox[0], lb[1] - bbox[1])
        canvas.alpha_composite(im, off)
    return canvas
