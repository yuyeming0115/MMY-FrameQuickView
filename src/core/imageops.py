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


def composite_layers(
    frame_paths: list[Path | None],
    bbox: BBox,
    flat_mask: list[bool] | None = None,
    fx_offsets: dict[int, tuple[int, int]] | None = None,
    anchor: tuple[int, int] | None = None,
    bbox_cache: dict[Path, BBox | None] | None = None,
    layer_union: dict[int, BBox] | None = None,
) -> Image.Image:
    """按层序合成到统一 bbox 窗口（v8：全链路固定锚点，彻底消除抖动）。

    - frame_paths 已按 layer_order 从下到上排序（如 shadow 在最前 = 最底层）
    - **bbox = 全部层并集** → 画布够大，特效不被裁切
    - **anchor**（普通层防抖）：跨所有帧算一次的「普通层组中心（画布坐标）」，
      所有帧共用同一 shift
    - **layer_union**（特效层防抖）：{层索引: 该层跨帧并集 bbox}。
      特效层用它做锚点整体居中，**帧间保持原始相对位置**。
      若逐帧用自己的 bbox 尺寸居中，特效动画会因各帧轮廓大小不同而抖动。
    - **bbox_cache**：预计算的 {path: bbox}，避免每帧重复打开图片文件
    - **特效层**最终偏移 = 层整体居中基准 + 帧内相对位置 + fx_offsets 用户微调
    """
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    cx, cy = w // 2, h // 2  # 画布中心（像素坐标）

    def _get_bbox(p: Path) -> BBox | None:
        """优先走缓存，避免重复打开图片文件。"""
        if bbox_cache is not None and p in bbox_cache:
            return bbox_cache[p]
        try:
            return frame_bbox(p)
        except (FileNotFoundError, OSError, ValueError):
            return None

    # ---- 计算固定偏移（让普通层组中心对齐画布中心）----
    if anchor is not None:
        # 用调用方提供的跨帧统一锚点 → 所有帧偏移一致，不抖动
        shift_x = cx - anchor[0]
        shift_y = cy - anchor[1]
    else:
        # 退化路径：按本帧计算（会抖动，仅兼容用）
        normal_boxes: list[BBox] = []
        for idx, p in enumerate(frame_paths):
            if p is not None and not (flat_mask and idx < len(flat_mask) and flat_mask[idx]):
                lb = _get_bbox(p)
                if lb is not None:
                    normal_boxes.append(lb)
        if normal_boxes:
            nu = union_bbox(normal_boxes)
            normal_cx_canvas = ((nu[0] + nu[2]) // 2) - bbox[0]
            normal_cy_canvas = ((nu[1] + nu[3]) // 2) - bbox[1]
            shift_x = cx - normal_cx_canvas
            shift_y = cy - normal_cy_canvas
        else:
            shift_x, shift_y = 0, 0

    # ---- 逐层合成 ----
    for idx, p in enumerate(frame_paths):
        if p is None:
            continue
        lb = _get_bbox(p)
        if lb is None:
            continue
        im = Image.open(p).convert("RGBA").crop(lb)

        if flat_mask and idx < len(flat_mask) and flat_mask[idx]:
            # 特效层：以「该层跨帧并集 bbox」为锚点整体居中，帧间保持原始相对位置。
            # ⚠ 不能用每帧自己的 bbox 尺寸居中：特效动画各帧轮廓大小不同，
            #    逐帧居中会导致整体左右/上下抖动（与图片软件播放效果不一致）。
            udx, udy = (fx_offsets or {}).get(idx, (0, 0))
            lu = (layer_union or {}).get(idx)
            if lu is not None:
                lu_w, lu_h = lu[2] - lu[0], lu[3] - lu[1]
                # 该层整体居中基准 + 当前帧在层内相对位置 + 用户微调
                off = (
                    cx - lu_w // 2 + (lb[0] - lu[0]) + udx,
                    cy - lu_h // 2 + (lb[1] - lu[1]) + udy,
                )
            else:
                # 退化：无 layer_union 时按本帧居中（会抖动，兼容旧调用）
                fw, fh = im.size
                off = (cx - fw // 2 + udx, cy - fh // 2 + udy)
        else:
            # 普通层：原始 bbox 偏移 + 固定居中偏移
            off = (lb[0] - bbox[0] + shift_x, lb[1] - bbox[1] + shift_y)
        canvas.alpha_composite(im, off)
    return canvas
