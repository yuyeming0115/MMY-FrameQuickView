"""序列渲染（M45 阶段 A-1）：把「叠层配方」渲染成一串对齐好的帧。

这是什么
--------
输入「每一层用哪些帧文件」，输出「一串合成好的 PIL 图片」。
预览（A/B 区）、以后的导出 GIF、命令行批量导出，都调用这同一份代码，
保证「看到什么就导出什么」。

从哪来的
--------
从 ui/worker.py 的 DecodeWorker.run 原样搬过来，**算法一行没改**，只是：
- 不再依赖 Qt（不发信号、不继承 QThread），本文件里没有任何 PySide6
- 结果改成逐帧返回（yield），几百帧的序列也不会一次占满内存
- DecodeWorker 现在只负责「在后台线程里调用它，再把每帧发给界面」

改动前后画面是否完全一致，用 scripts/golden_frames.py check 验证（逐像素比对）。

怎么用
------
    job = SequenceRender(layers, flat_mask=flat_mask, fx_offsets=fx_offsets, canvas_align=True)
    print(job.total, job.all_blank)          # 总帧数、是否整串全透明
    for i, img, label in job.frames():       # 逐帧取出合成图
        img.save(f"{label}.png")

或者只要图片：
    for img, label in render_frames(layers, canvas_align=True):
        ...
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterator

from PIL import Image

from .imageops import composite_layers, frame_info, union_bbox


def _is_valid_frame(item) -> bool:
    """这一帧的文件是否真的存在。

    item 可能是普通文件路径（Path），也可能是图集里的一帧（AtlasFrame，M40），
    后者自带 is_file() 方法，所以先看对象自己有没有 is_file。
    """
    if hasattr(item, "is_file"):
        return bool(item.is_file())
    return Path(item).is_file()


class SequenceRender:
    """一条序列的渲染任务。

    创建时（__init__）会先把所有帧文件打开一遍，算好对齐需要的数据（这一步有磁盘读取，
    应放在后台线程里做）；之后调用 frames() 逐帧合成。

    参数（和原来 DecodeWorker 的参数完全一样）：
        layers       每一层的帧文件列表；layers[0] 是最底层（影子），越往后越靠上
        labels       每一帧的显示标签；不传则用帧文件名（如 "0017"）
        flat_mask    第 j 层是不是「特效层」（扁平特效：按自身范围居中，而不是按画布坐标）
        fx_offsets   特效层的手动微调偏移 {层下标: (dx, dy)}
        canvas_align 是否「固定画布对齐」（M34）：True = 按原始渲染画布摆放，角色位置不随内容漂移；
                     False = 按全部层的内容并集裁剪（画面更紧凑）

    属性：
        total        这条序列一共几帧（= 最长那一层的帧数；短的层循环补齐）
        all_blank    是否整串都是全透明的占位图（M36 空帧提示用）
    """

    def __init__(
        self,
        layers: list[list[Path]],
        labels: list[str] | None = None,
        flat_mask: list[bool] | None = None,
        fx_offsets: dict[int, tuple[int, int]] | None = None,
        canvas_align: bool = False,
    ):
        self._layers = layers
        self._labels = labels
        self._flat_mask = flat_mask
        self._fx_offsets = fx_offsets
        self._canvas_align = canvas_align
        self.all_blank = False
        self.total = 0
        # 以下几项由 _prepare() 算好，frames() 合成时使用
        self._bbox: tuple[int, int, int, int] = (0, 0, 1, 1)
        self._anchor: tuple[int, int] | None = None
        self._bbox_cache: dict = {}
        self._layer_union: dict[int, tuple] = {}
        self._prepare()

    def _is_flat(self, j: int) -> bool:
        """第 j 层是不是特效层。"""
        return bool(self._flat_mask and j < len(self._flat_mask) and self._flat_mask[j])

    # ------------------------------------------------------------------
    # 第一步：预计算（读文件、算对齐）——原 DecodeWorker.run 的前半段
    # ------------------------------------------------------------------
    def _prepare(self) -> None:
        # 收集所有真实存在的帧文件
        all_paths: list = []
        for layer in self._layers:
            for p in layer:
                if p and _is_valid_frame(p):
                    all_paths.append(p)

        if not all_paths:
            self.total = 0          # 一帧可用的都没有 → frames() 什么也不产出
            return

        missing_count = sum(1 for layer in self._layers for p in layer if p and not _is_valid_frame(p))
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

        _is_flat = self._is_flat

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

        self._bbox = bbox
        self._anchor = anchor
        self._bbox_cache = bbox_cache
        self._layer_union = layer_union
        self.total = max((len(layer) for layer in self._layers), default=0)

    # ------------------------------------------------------------------
    # 第二步：逐帧合成——原 DecodeWorker.run 的后半段（for 循环）
    # ------------------------------------------------------------------
    def frames(self, should_stop: Callable[[], bool] | None = None
               ) -> Iterator[tuple[int, Image.Image, str]]:
        """逐帧产出 (第几帧, 合成图, 帧号标签)。

        should_stop：可选，一个「要不要停下」的函数。批量导出点「取消」、
        或者预览时切换到别的资源，调用方可以传进来让渲染提前结束。
        这里不认识 Qt，由调用方把 QThread.isInterruptionRequested 之类的传进来。
        """
        # 各层独立循环：短序列用取模回绕（8帧角色循环播放）
        for i in range(self.total):
            if should_stop is not None and should_stop():
                return
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
                per, self._bbox, self._flat_mask, self._fx_offsets,
                anchor=self._anchor, bbox_cache=self._bbox_cache, layer_union=self._layer_union,
            )
            # 默认 label：取该帧首个真实存在的文件名（去后缀），
            # 反映"同方向跨动作连续编号"的真实帧号（如 attack=0017-0024、skill=0025-0032），
            # 而不是从 0001 重启的序号。
            if self._labels and i < len(self._labels):
                label = self._labels[i]
            else:
                label = next(
                    (p.stem if hasattr(p, "stem") else Path(p).stem for p in per if p is not None),
                    f"{i + 1:04d}",
                )
            yield i, img, label


def render_frames(
    layers: list[list[Path]],
    labels: list[str] | None = None,
    flat_mask: list[bool] | None = None,
    fx_offsets: dict[int, tuple[int, int]] | None = None,
    canvas_align: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[tuple[Image.Image, str]]:
    """简便写法：只要 (合成图, 帧号标签)，不关心总帧数和空帧标记时用这个。"""
    job = SequenceRender(layers, labels, flat_mask, fx_offsets, canvas_align)
    for _i, img, label in job.frames(should_stop):
        yield img, label
