"""后台解码 worker（grid_view / anim_view 共用）。

QPixmap 不可跨线程创建，因此 worker 只返回 PIL.Image，由主线程转 QPixmap。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.imageops import composite_layers, frame_info, union_bbox


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
        # 收集所有路径
        all_paths: list[Path] = []
        for layer in self._layers:
            for p in layer:
                if p and Path(p).is_file():
                    all_paths.append(p)

        if not all_paths:
            self.finished.emit(0)
            return

        missing_count = sum(1 for layer in self._layers for p in layer if p and not Path(p).is_file())
        if missing_count:
            print(f"[DecodeWorker] 跳过 {missing_count} 个不存在的帧文件", file=sys.stderr)

        # ---- 一次性预计算所有路径的 bbox + 画布尺寸（缓存）----
        # 关键提速：原本每帧合成时都会重新打开图片算 bbox，
        # 20帧×多层场景下会产生上百次重复文件 IO。这里每个文件只开一次。
        bbox_cache: dict[Path, tuple | None] = {}
        size_cache: dict[Path, tuple[int, int]] = {}
        for p in all_paths:
            try:
                b, sz = frame_info(p)
            except (FileNotFoundError, OSError, ValueError):
                b, sz = None, (0, 0)
            bbox_cache[p] = b
            size_cache[p] = sz
        # M36：全帧 alpha bbox 均为 None → 整个合成结果不可见，标记空帧
        self.all_blank = all(b is None for b in bbox_cache.values())

        def _is_flat(j: int) -> bool:
            return bool(self._flat_mask and j < len(self._flat_mask) and self._flat_mask[j])

        if self._canvas_align:
            # ---- M34 固定画布对齐 ----
            # 窗口 = 原始渲染画布（普通层帧尺寸取 max），普通层按原始画布坐标
            # 放置（不平移）→ 跨视图（有无武器/不同部件组合）角色位置纹丝不动。
            # anchor 取画布中心 → composite_layers 的 shift=0。
            # 特效层无画布语义，保持「层 union 居中 + 用户微调」不变。
            sizes = [size_cache[p] for j, layer in enumerate(self._layers)
                     if not _is_flat(j)
                     for p in layer if size_cache.get(p, (0, 0)) != (0, 0)]
            if not sizes:                    # 纯特效组：退化用全部帧尺寸（≈原模式）
                sizes = [s for s in size_cache.values() if s != (0, 0)]
            w = max((s[0] for s in sizes), default=1)
            h = max((s[1] for s in sizes), default=1)
            bbox = (0, 0, max(1, w), max(1, h))
            anchor = (bbox[2] // 2, bbox[3] // 2)
        else:
            # 画布 bbox = **全部层**并集（从缓存算，无额外 IO）→ 特效不被裁切
            bbox = union_bbox([bbox_cache[p] for p in all_paths]) or (0, 0, 1, 1)

            # ---- 跨帧统一锚点（防角色左右晃动）----
            # 普通层组中心，用**所有帧**的并集算一次，所有帧共用同一 shift。
            # 若逐帧计算，角色动画每帧轮廓不同会导致整体位移抖动。
            normal_boxes = []
            for j, layer in enumerate(self._layers):
                if _is_flat(j):
                    continue
                for p in layer:
                    if p in bbox_cache and bbox_cache[p] is not None:
                        normal_boxes.append(bbox_cache[p])
            anchor = None
            if normal_boxes:
                nu = union_bbox(normal_boxes)
                if nu is not None:
                    # 转为画布坐标（画布左上角 = bbox[0], bbox[1]）
                    anchor = (
                        ((nu[0] + nu[2]) // 2) - bbox[0],
                        ((nu[1] + nu[3]) // 2) - bbox[1],
                    )

        # ---- 特效层：各自计算「跨帧并集 bbox」做锚点 ----
        # 特效动画各帧轮廓大小不同，若逐帧按自身尺寸居中会整体抖动，
        # 必须用整条序列的并集作为固定锚点，帧间保留原始相对位移。
        layer_union: dict[int, tuple] = {}
        if self._flat_mask:
            for j, layer in enumerate(self._layers):
                if not (j < len(self._flat_mask) and self._flat_mask[j]):
                    continue
                boxes = [bbox_cache[p] for p in layer
                         if p in bbox_cache and bbox_cache[p] is not None]
                lu = union_bbox(boxes)
                if lu is not None:
                    layer_union[j] = lu

        n = max((len(layer) for layer in self._layers), default=0)
        # 各层独立循环：短序列用取模回绕（8帧角色循环播放）
        for i in range(n):
            per = [
                (
                    layer[i % len(layer)]
                    if (layer and i % len(layer) < len(layer) and
                        layer[i % len(layer)] and
                        Path(layer[i % len(layer)]).is_file())
                    else None
                )
                for j, layer in enumerate(self._layers)
            ]
            img = composite_layers(
                per, bbox, self._flat_mask, self._fx_offsets,
                anchor=anchor, bbox_cache=bbox_cache, layer_union=layer_union,
            )
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
