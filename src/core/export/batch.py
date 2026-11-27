"""批量导出（M45 阶段 C）：整个角色 / 当前方向全部动作 / 展示合集。

和阶段 B 的「导出当前动画」用的是同一条流水线，只是外面套了一层循环：
    plan_combos()   列出这个角色有哪些「方向×动作」
    run_batch()     逐个：叠层(compose) → 渲染(render) → 写文件(export_frames)，并汇总成报告

本文件不依赖 Qt。进度通过回调函数 progress 汇报、「要不要中途停止」通过 should_stop 传进来，
界面线程（ExportWorker）把 Qt 的信号和这两个函数接上即可。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from .. import compose
from ..render import SequenceRender
from . import ExportOptions, content_bbox, export_frames, safe_name


# =====================================================================
# 导出计划：这个角色有哪些方向×动作
# =====================================================================
def _ordered(values: set, order: list) -> list:
    """按模板里的顺序排；模板里没有的排在后面（按字母序）。"""
    known = [v for v in order if v in values]
    rest = sorted(v for v in values if v not in order)
    return known + rest


def target_parts(target, look: "compose.LookConfig", ctx: "compose.ComposeContext") -> list:
    """这个对象预览时实际会用到的部件：显示部件（含形象组合 M37）+ 穿戴的时装/头发/武器（M38/M42）。

    和预览用的是同一批（见 compose.display_parts / dressed_look_parts），所以导出的组合
    和界面上方向/动作按钮里能点的组合一致。
    """
    if not hasattr(target, "parts"):                 # 单个部件
        return [target]
    display = compose.display_parts(target, ctx)
    return display + compose.dressed_look_parts(target, display, look, ctx)


def plan_combos(target, look: "compose.LookConfig", ctx: "compose.ComposeContext",
                direction: str | None = None) -> list[tuple[str, str]]:
    """列出要导出的 (方向, 动作)，按模板顺序排列（先方向后动作）。

    direction 不为空时只列这个方向（「当前方向全部动作」「展示合集」用）。
    """
    parts = target_parts(target, look, ctx)
    # 扁平特效没有真正的方向/动作（用的是虚拟方向「特效」）。角色带普通部件时，
    # 特效跟着角色叠在每个方向动作上，不单独算一个组合；纯特效资源才用它自己的。
    normal = [p for p in parts if not p.is_flat]
    combos: set[tuple[str, str]] = set()
    for p in (normal or parts):
        for d, col in p.matrix.items():
            for a in col:
                combos.add((d, a))
    if direction:
        combos = {c for c in combos if c[0] == direction}
    tpl = ctx.tpl
    dirs = _ordered({d for d, _ in combos}, tpl.directions if tpl else [])
    acts = _ordered({a for _, a in combos}, tpl.actions if tpl else [])
    return [(d, a) for d in dirs for a in acts if (d, a) in combos]


# =====================================================================
# 输出路径
# =====================================================================
def item_path(out_dir: Path, name: str, direction: str, action: str, ext: str) -> Path:
    """单个动画的输出路径：输出目录 / 资源名 / 方向_动作.扩展名"""
    return Path(out_dir) / safe_name(name) / f"{safe_name(direction)}_{safe_name(action)}.{ext}"


def showcase_path(out_dir: Path, name: str, direction: str, ext: str) -> Path:
    """展示合集的输出路径：输出目录 / 资源名 / 方向_展示.扩展名"""
    return Path(out_dir) / safe_name(name) / f"{safe_name(direction)}_展示.{ext}"


def planned_paths(items: list[tuple[str, str]], out_dir: Path, name: str, ext: str,
                  showcase: bool) -> list[Path]:
    """这次会写出哪些文件（导出前检查「已存在」用）。"""
    if showcase:
        return [showcase_path(out_dir, name, items[0][0], ext)] if items else []
    return [item_path(out_dir, name, d, a, ext) for d, a in items]


# =====================================================================
# 导出报告
# =====================================================================
@dataclass
class BatchReport:
    ok: list = field(default_factory=list)          # [(名称, 文件路径, 导出信息)]
    skipped: list = field(default_factory=list)     # [(名称, 原因)]  例：("SE · dead", "空帧（图片全透明，是占位图）")
    failed: list = field(default_factory=list)      # [(名称, 错误说明)]
    cancelled: bool = False                         # 用户中途点了取消
    folder: Path | None = None                      # 输出到哪个文件夹（完成后「打开文件夹」用）

    def summary(self) -> str:
        """一句话汇总，如「成功 4 个，跳过 1 个，失败 0 个」。"""
        s = f"成功 {len(self.ok)} 个，跳过 {len(self.skipped)} 个，失败 {len(self.failed)} 个"
        return s + "（已取消）" if self.cancelled else s

    def details(self) -> str:
        """跳过和失败的明细，一行一条。"""
        lines = [f"跳过 {name}：{why}" for name, why in self.skipped]
        lines += [f"失败 {name}：{why}" for name, why in self.failed]
        return "\n".join(lines)


# =====================================================================
# 逐个导出
# =====================================================================
def _render(stack: "compose.LayerStack", canvas_align: bool,
            should_stop: Callable[[], bool] | None) -> list[Image.Image]:
    """把一个叠层配方渲染成一串帧（和预览同一份代码：core/render.py）。"""
    job = SequenceRender(stack.layers, flat_mask=stack.flat_mask, fx_offsets=stack.fx_offsets,
                         canvas_align=canvas_align)
    return [img for _i, img, _label in job.frames(should_stop)]


def _unify_size(frames: list[Image.Image]) -> list[Image.Image]:
    """各帧尺寸不一致时，贴到同一张最大的透明画布上（左上角对齐）。

    画布对齐模式下，各帧内容本来就在原始画布坐标里，左上角对齐 = 位置不变。
    展示合集把不同动作的帧串在一起时可能用到（同一角色各动作画布通常一样，这是保险）。
    """
    w = max(f.width for f in frames)
    h = max(f.height for f in frames)
    if all(f.size == (w, h) for f in frames):
        return frames
    out = []
    for f in frames:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(f.convert("RGBA"))
        out.append(canvas)
    return out


def _frames_for(target, direction: str, action: str, look, ctx, canvas_align: bool,
                should_stop) -> tuple[list[Image.Image] | None, str]:
    """渲染一个方向×动作。返回 (帧列表, 跳过原因)；能导出时原因为空，不能导出时帧列表为 None。"""
    stack = compose.build_stack(target, direction, action, look, ctx)
    if max((len(layer) for layer in stack.layers), default=0) == 0:
        return None, "没有帧"
    frames = _render(stack, canvas_align, should_stop)
    if not frames:
        return None, "没有帧"
    if content_bbox(frames) is None:
        return None, "空帧（图片全透明，是占位图）"
    return frames, ""


def run_batch(target, items: list[tuple[str, str]], look: "compose.LookConfig",
              ctx: "compose.ComposeContext", opts: ExportOptions, out_dir: Path, name: str,
              canvas_align: bool = True, showcase: bool = False,
              progress: Callable[[int, int, str], None] | None = None,
              should_stop: Callable[[], bool] | None = None) -> BatchReport:
    """按 items 逐个导出。

    items：要导出的 [(方向, 动作)]（来自 plan_combos）
    showcase：True = 把 items 的所有动作首尾相接合成**一个**文件（items 必须同一个方向）
    progress(已完成数, 总数, 正在处理的名称)：每开始处理一项调用一次
    should_stop()：返回 True 就在下一项开始前停下（已写好的文件保留）
    单项失败不影响后面的：错误记进报告，继续导下一个。
    """
    rep = BatchReport(folder=Path(out_dir) / safe_name(name))
    total = len(items)

    def report_progress(done: int, label: str) -> None:
        if progress is not None:
            progress(done, total, label)

    # ---------------- 展示合集：所有动作串成一个文件 ----------------
    if showcase:
        all_frames: list[Image.Image] = []
        for k, (d, a) in enumerate(items):
            if should_stop is not None and should_stop():
                rep.cancelled = True
                return rep
            label = f"{d} · {a}"
            report_progress(k, label)
            try:
                frames, why = _frames_for(target, d, a, look, ctx, canvas_align, should_stop)
            except Exception as e:                       # 某个动作出错：记下来，其余动作继续
                rep.failed.append((label, f"{type(e).__name__}: {e}"))
                continue
            if should_stop is not None and should_stop():
                rep.cancelled = True                     # 渲染到一半被取消：这一项的帧不完整，丢弃
                return rep
            if frames is None:
                rep.skipped.append((label, why))
            else:
                all_frames.extend(frames)
        if should_stop is not None and should_stop():
            rep.cancelled = True
            return rep
        if all_frames:
            path = showcase_path(out_dir, name, items[0][0], opts.ext)
            label = f"{items[0][0]} · 展示合集"
            report_progress(total, label)
            try:
                # prepare_frames 对全部帧算一个内容并集 → 整个合集里角色位置不跳
                info = export_frames(_unify_size(all_frames), path, opts)
                rep.ok.append((label, path, info))
            except Exception as e:
                rep.failed.append((label, f"{type(e).__name__}: {e}"))
        return rep

    # ---------------- 一个动作一个文件 ----------------
    for k, (d, a) in enumerate(items):
        if should_stop is not None and should_stop():
            rep.cancelled = True
            return rep
        label = f"{d} · {a}"
        report_progress(k, label)
        try:
            frames, why = _frames_for(target, d, a, look, ctx, canvas_align, should_stop)
            if should_stop is not None and should_stop():
                rep.cancelled = True                     # 渲染到一半被取消：不写半截文件
                return rep
            if frames is None:
                rep.skipped.append((label, why))
                continue
            path = item_path(out_dir, name, d, a, opts.ext)
            info = export_frames(frames, path, opts)
            rep.ok.append((label, path, info))
        except Exception as e:                           # 单项失败不拖累后面的
            rep.failed.append((label, f"{type(e).__name__}: {e}"))
    if should_stop is not None and should_stop():
        rep.cancelled = True
    report_progress(total, "完成")
    return rep
