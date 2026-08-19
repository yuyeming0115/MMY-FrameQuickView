"""智能滚动条：鼠标悬停时按需显示，离开时自动隐藏。

用法：
    from .hover_scroll import enable_hover_scroll
    enable_hover_scroll(scroll_area)   # QScrollArea / QTreeWidget 等

原理：
- 安装时记录 widget 原有的滚动条策略（如 AsNeeded / AlwaysOff）
- 鼠标进入 → 恢复原策略（AsNeeded 则按需显示，AlwaysOff 仍隐藏）
- 鼠标离开 → 全部设为 AlwaysOff（隐藏）
- 事件过滤器返回 False，不拦截原有 enterEvent/leaveEvent 逻辑
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt


class _HoverScrollFilter(QObject):
    """事件过滤器：鼠标进入时滚动条按需显示，离开时隐藏。"""

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et == QEvent.Type.Enter:
            v = getattr(obj, "_hover_v_policy", None)
            h = getattr(obj, "_hover_h_policy", None)
            if v is not None:
                obj.setVerticalScrollBarPolicy(v)
            if h is not None:
                obj.setHorizontalScrollBarPolicy(h)
        elif et == QEvent.Type.Leave:
            obj.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            obj.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return False  # 不拦截，原有 enterEvent/leaveEvent 继续执行


# 单例：所有 widget 共用一个 filter 实例
_filter: _HoverScrollFilter | None = None


def enable_hover_scroll(widget) -> None:
    """为带滚动条策略的 widget 启用智能隐藏（鼠标离开自动收起）。"""
    global _filter
    if _filter is None:
        _filter = _HoverScrollFilter()
    # 记录原始策略，enter 时恢复（尊重 AlwaysOff 等既有配置）
    widget._hover_v_policy = widget.verticalScrollBarPolicy()
    widget._hover_h_policy = widget.horizontalScrollBarPolicy()
    widget.installEventFilter(_filter)
    # 初始隐藏（启动时鼠标不在区域内）
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
