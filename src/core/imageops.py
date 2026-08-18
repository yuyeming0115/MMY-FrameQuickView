"""图像操作：包围盒裁剪（非破坏性）、帧加载。

M1 仅提供 bbox 计算基础；A/B 区显示在 M2/M3 接入。
规则（v0.3 定稿）：
- 动画显示用「全序列并集 bbox」，帧间不抖动
- 网格单帧用自身 bbox
- 叠层各层共享同一并集 bbox 窗口对齐
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
    """全序列并集 bbox（注意：逐帧解码，调用方应放工作线程）。"""
    return union_bbox([frame_bbox(p) for p in paths])


def load_cropped(path: Path, bbox: BBox | None) -> Image.Image:
    """按 bbox 裁剪加载（仅内存操作，原文件不修改）。"""
    im = Image.open(path).convert("RGBA")
    if bbox is not None:
        im = im.crop(bbox)
    return im
