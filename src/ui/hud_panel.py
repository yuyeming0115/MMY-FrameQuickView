"""HUD 信息面板（M32）：B 区画布右下角悬浮的帧数账目卡片。

- 挂载方式与 PartToggles 一致：stack_host 手动定位悬浮子控件（不进布局），
  位置取右下角——右上角已被组视图的显隐 toggle / 穿戴下拉框占用（M26/M28）。
- 半透明深色卡片 + 富文本表格：金色标题、灰色标签、红色异常 ⚠，
  与全局 Modern Dark Flat 主题一致；字号 ≥ 14px（视力适配）。
- 当前选中 (方向,动作) 的行金色高亮；断档 / 帧数不一致的行红字标注。
- M32.2：展开视图一行一个方向（行内「动作 帧」流式排列），只显示帧数不显示
  帧号范围；行数多时内部滚动，高度不超过宿主 75%。
"""
from __future__ import annotations

from ..core.stats import ComboStat, HudStats
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy

# 面板固定宽度（HTML 表格 width=100% 依此铺满）
HUD_WIDTH = 320

_COLOR_LABEL = "#96A1AD"     # 灰色标签（方向·动作）
_COLOR_TEXT = "#E8E4D9"      # 米白正文（帧数）
_COLOR_DIM = "#7A828C"       # 更暗的帧号范围
_COLOR_GOLD = "#D4AF37"      # 金色（标题 / 高亮 / 合计）
_COLOR_RED = "#E24B4A"       # 红色异常（断档 / 不一致）


class HUDPanel(QScrollArea):
    """帧数账目浮层：set_stats(HudStats) 全量刷新，None 隐藏。

    M32.1 紧凑模式（用户反馈：完整面板过高遮挡角色）：
    - 默认只显示 标题 + 当前(方向·动作) + 合计（约 3 行）
    - 鼠标悬停自动展开完整账目表格，移开自动收起
    - 高度变化发 size_changed 信号，宿主（anim_view）据此重新锚定右下角
    """

    size_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stats: HudStats | None = None
        self._expanded = False
        self.setFixedWidth(HUD_WIDTH)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea { background: rgba(42,46,51,0.92);"
            " border: 1px solid #3A3F46; border-radius: 8px; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        # 滚动条按需出现、窄条样式（与全局 overlay 滚动条一致的视觉权重）
        self.setStyleSheet(self.styleSheet() +
            "QScrollBar:vertical { background: transparent; border: none;"
            " margin: 0; width: 6px; }"
            "QScrollBar::handle:vertical { background: #4A4F56; border-radius: 3px;"
            " min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,"
            " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; border: none; }"
        )
        self._label = QLabel()
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._label.setStyleSheet(
            "QLabel { background: transparent; color: #E8E4D9;"
            " font-size: 14px; padding: 8px 10px; }"
        )
        self.setWidget(self._label)
        self.setWidgetResizable(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    # ---------------- 悬停展开 / 收起（M32.1） ----------------
    def enterEvent(self, event) -> None:
        self._set_expanded(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_expanded(False)
        super().leaveEvent(event)

    def _set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        if self._stats is not None:
            self._refresh()
            self.size_changed.emit()

    # ---------------- 内容渲染 ----------------
    def set_stats(self, stats: HudStats | None) -> None:
        """全量刷新；stats 为 None 或无行时隐藏面板。"""
        self._stats = stats
        if stats is None or not stats.rows:
            self.hide()
            return
        self._refresh()
        tip_bits = [f"{k} {v}帧" for k, v in stats.totals.items()]
        self.setToolTip("鼠标悬停展开完整账目\n各部件帧数小计：\n" + "\n".join(tip_bits)
                        if len(stats.totals) > 1 else "鼠标悬停展开完整账目")

    def _refresh(self) -> None:
        stats = self._stats
        if stats is None:
            return
        self._label.setText(self._render(stats))
        self._label.adjustSize()
        self.show()

    def _render(self, stats: HudStats) -> str:
        header = (
            f"<div style='color:{_COLOR_GOLD}; font-size:15px;'>{stats.title}</div>"
            f"<div style='color:{_COLOR_LABEL}; font-size:12px; margin:0 0 4px 0;'>{stats.subtitle}</div>"
        )
        if not self._expanded:
            return header + self._render_compact(stats)
        return header + self._render_expanded(stats)

    def _render_expanded(self, stats: HudStats) -> str:
        """展开视图（M32.2）：一行一个方向，行内「动作 帧」流式排列。

        用户反馈完整列表一行一组合太长（25 行遮挡画布），改为按方向归组后
        25 行 → 方向数行（通常 5~6 行），方向内动作横向排布自动折行。
        """
        cur_dir, cur_act = stats.current or (None, None)
        dirs: list[str] = []
        by_dir: dict[str, list[ComboStat]] = {}
        for r in stats.rows:
            if r.direction not in by_dir:
                by_dir[r.direction] = []
                dirs.append(r.direction)
            by_dir[r.direction].append(r)
        body: list[str] = []
        for d in dirs:
            rows = by_dir[d]
            d_issues = any(r.has_issues for r in rows)
            if d == cur_dir:
                dc = _COLOR_GOLD
            elif d_issues:
                dc = _COLOR_RED
            else:
                dc = _COLOR_LABEL
            chips: list[str] = []
            for r in rows:
                cur = (r.direction == cur_dir and r.action == cur_act)
                if cur:
                    c, weight = _COLOR_GOLD, "font-weight:700; "
                elif r.has_issues:
                    c, weight = _COLOR_RED, ""
                else:
                    c, weight = _COLOR_TEXT, ""
                chip = (f"<span style='color:{c}; {weight}'>{r.action} "
                        f"<span style='color:{_COLOR_DIM};'>{r.count}</span></span>")
                if r.has_issues:
                    chip += self._render_chip_annot(r)
                chips.append(chip)
            n_d = sum(r.count for r in rows)
            body.append(
                "<tr>"
                f"<td valign='top' style='color:{dc}; white-space:nowrap;"
                f" padding:2px 10px 2px 0;'>{d}"
                f"<div style='color:{_COLOR_DIM}; font-size:11px;'>{n_d}帧</div></td>"
                f"<td style='padding:2px 0;'>{' · '.join(chips)}</td>"
                "</tr>"
            )
        n_dirs = len(dirs)
        n_acts = len({r.action for r in stats.rows})
        return ("<table width='100%' cellspacing='0' cellpadding='0'>"
                + "".join(body)
                + self._render_total(stats, f"{stats.grand} 帧 · {n_dirs}方向 × {n_acts}动作")
                + "</table>")

    def _render_chip_annot(self, r: ComboStat) -> str:
        """异常 chip 的红字标注：断档帧号 / 不一致部件（截断到 2 条防折行爆炸）。"""
        annots: list[str] = []
        if r.gaps:
            annots.append(f"缺帧{r.gaps[:2]}{'…' if len(r.gaps) > 2 else ''}")
        annots.extend(r.mismatch[:2])
        if not annots:
            return "<span style='color:" + _COLOR_RED + ";'>⚠</span>"
        return "<span style='color:" + _COLOR_RED + ";'>⚠" + "，".join(annots) + "</span>"

    def _render_compact(self, stats: HudStats) -> str:
        """紧凑模式：当前组合一行 + 合计一行。"""
        cur_dir, cur_act = stats.current or (None, None)
        row = next((r for r in stats.rows
                    if r.direction == cur_dir and r.action == cur_act), None)
        n_issues = sum(1 for r in stats.rows if r.has_issues)
        total = f"{stats.grand} 帧"
        if n_issues:
            total += f" <span style='color:{_COLOR_RED};'>⚠{n_issues}</span>"
        if row is None:
            return "<table width='100%' cellspacing='0' cellpadding='0'>" \
                + self._render_total(stats, total) + "</table>"
        body = self._render_row(row, True)
        return "<table width='100%' cellspacing='0' cellpadding='0'>" \
            + body + self._render_total(stats, total) + "</table>"

    def _render_total(self, stats: HudStats, val: str) -> str:
        return (
            "<tr>"
            f"<td style='padding-top:5px; color:{_COLOR_GOLD};'>合计</td>"
            f"<td align='right' style='padding-top:5px; color:{_COLOR_GOLD};'>{val}</td>"
            "</tr>"
        )

    def _render_row(self, r: ComboStat, current: bool) -> str:
        label = f"{r.direction} · {r.action}" if not r.is_flat else f"{r.direction} · {r.action}"
        # M32.2：不再显示帧号范围（用户反馈），范围信息状态栏已有
        annots: list[str] = []
        if r.source:
            annots.append(f"←{r.source}")
        if r.gaps:
            annots.append(f"缺帧{r.gaps[:3]}{'…' if len(r.gaps) > 3 else ''}")
        annots.extend(r.mismatch)
        if current:
            lc, tc = _COLOR_GOLD, _COLOR_GOLD
        elif r.has_issues:
            lc, tc = _COLOR_RED, _COLOR_RED
        else:
            lc, tc = _COLOR_LABEL, _COLOR_TEXT
        val = f"{r.count} 帧"
        if annots:
            val += f" <span style='color:{_COLOR_RED};'>⚠ {'，'.join(annots)}</span>"
        return (
            "<tr>"
            f"<td style='color:{lc}; padding:1px 0;'>{label}</td>"
            f"<td align='right' style='color:{tc}; padding:1px 0;'>{val}</td>"
            "</tr>"
        )
