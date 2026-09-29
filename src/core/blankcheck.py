"""空帧检测（M36）：抽样 (方向,动作) 序列首帧，识别全透明占位图。

背景（实测 角色输出图/504004 黑龙王）：帧文件与帧号都齐全、扫描层一切正常，
但 E/N/NW/S 全部帧是 875×875 全透明空图（alpha max=0）→ A/B 区渲染空白且无提示。

设计约束：扫描阶段仍只读文件名（不解码）；本模块只在「选中部件/组后」由
app 的后台线程调用，每序列仅解码首帧（整目录抽样 ≤ 约 30 次解码，秒级内）。
首帧无有效像素 → 视为整个序列为空图（美术占位是整序列空图，抽样近似成立）。
"""
from __future__ import annotations

from .imageops import frame_info


def blank_sequences(matrix: dict[str, dict]) -> set[tuple[str, str]]:
    """检测 matrix（direction → action → ActionData）中的全透明空序列。

    返回 {(direction, action), ...}。首帧 alpha bbox 为 None（全透明）即判空；
    文件缺失/损坏的序列跳过（不误报，交由帧号断档与既有错误路径处理）。
    逐帧解码，调用方必须放工作线程。
    """
    blank: set[tuple[str, str]] = set()
    for direction, col in matrix.items():
        for action, ad in col.items():
            frames = getattr(ad, "frames", None)
            if not frames:
                continue
            try:
                bbox, _size = frame_info(frames[0])
            except (FileNotFoundError, OSError, ValueError):
                continue
            if bbox is None:
                blank.add((direction, action))
    return blank
