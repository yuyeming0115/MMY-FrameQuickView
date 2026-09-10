"""HUD 信息面板（M32）：B 区画布右下角悬浮的帧数账目卡片。

- 挂载方式与 PartToggles 一致：stack_host 手动定位悬浮子控件（不进布局），
  位置取右下角——右上角已被组视图的显隐 toggle / 穿戴下拉框占用（M26/M28）。
- 半透明深色卡片 + 富文本表格：金色标题、灰色标签、红色异常 ⚠，
  与全局 Modern Dark Flat 主题一致；字号 ≥ 14px（视力适配）。
- 当前选中 (方向,动作) 的行金色高亮；断档 / 帧数不一致的行红字标注。
- 行数多时内部滚动，高度不超过宿主 75%。
"""
from __future__ import annotations

from ..core.stats import ComboStat, HudStats
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy

# 面板固定宽度（HTML 表格 width=100% 依此铺满）
HUD_WIDTH = 320

_COLOR_LABEL = "#96A1AD"     # 灰色标签（方向·动作）
_COLOR_TEXT = "#E8E4D9"      # 米白正文（帧数）
_COLOR_DIM = "#7A828C"       # 更暗的帧号范围
_COLOR_GOLD = "#D4AF37"      # 金色（标题 / 高亮 / 合计）
_COLOR_RED = "#E24B4A"       # 红色异常（断档 / 不一致）


class HUDPanel(QScrollArea):
    """帧数账目浮层：set_stats(HudStats) 全量刷新，None 隐藏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
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

    # ---------------- 内容渲染 ----------------
    def set_stats(self, stats: HudStats | None) -> None:
        """全量刷新；stats 为 None 或无行时隐藏面板。"""
        if stats is None or not stats.rows:
            self.hide()
            return
        self._label.setText(self._render(stats))
        self._label.adjustSize()
        self.show()
        cur_dir, cur_act = stats.current or (None, None)
        tip_bits = [f"{k} {v}帧" for k, v in stats.totals.items()]
        self.setToolTip(f"各部件帧数小计：\n" + "\n".join(tip_bits)
                        if len(stats.totals) > 1 else "")

    def _render(self, stats: HudStats) -> str:
        cur_dir, cur_act = stats.current or (None, None)
        rows_html: list[str] = []
        for r in stats.rows:
            rows_html.append(self._render_row(r, r.direction == cur_dir and r.action == cur_act))
        n_dirs = len({r.direction for r in stats.rows})
        n_acts = len({r.action for r in stats.rows})
        return (
            f"<div style='color:{_COLOR_GOLD}; font-size:15px;'>{stats.title}</div>"
            f"<div style='color:{_COLOR_LABEL}; font-size:12px; margin:0 0 4px 0;'>{stats.subtitle}</div>"
            "<table width='100%' cellspacing='0' cellpadding='0'>"
            + "".join(rows_html) +
            "<tr>"
            f"<td style='padding-top:5px; color:{_COLOR_GOLD};'>合计</td>"
            f"<td align='right' style='padding-top:5px; color:{_COLOR_GOLD};'>"
            f"{stats.grand} 帧 · {n_dirs}方向 × {n_acts}动作</td>"
            "</tr></table>"
        )

    def _render_row(self, r: ComboStat, current: bool) -> str:
        label = f"{r.direction} · {r.action}" if not r.is_flat else f"{r.direction} · {r.action}"
        if r.first is not None:
            rng = (f"{r.first}–{r.last}" if r.is_flat
                   else f"{r.first:04d}–{r.last:04d}")
        else:
            rng = ""
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
        if rng:
            val += f" <span style='color:{_COLOR_DIM};'>{rng}</span>"
        if annots:
            val += f" <span style='color:{_COLOR_RED};'>⚠ {'，'.join(annots)}</span>"
        return (
            "<tr>"
            f"<td style='color:{lc}; padding:1px 0;'>{label}</td>"
            f"<td align='right' style='color:{tc}; padding:1px 0;'>{val}</td>"
            "</tr>"
        )
