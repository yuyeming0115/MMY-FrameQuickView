"""方向/动作按钮矩阵：B 区左侧纵向堆叠（顶 label + 每行一个按钮）。

不再用横向"扁条"——把按钮排成竖列，让 B 区画布保留最大横向空间。

三态：缺（missing）/ 不适用（unexpected）/ 正常；查漏靠颜色，不用下拉。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QPushButton, QHBoxLayout, QSizePolicy

from ..core.scanner import PartData, IdGroup
from ..core.template import Template

# 默认方向：首次进入某部件/组（direction=None）时高亮的方向。
# 用户 2026-08-19 要求默认 SE（东南）。仅当该部件/组确实拥有 SE 时才生效，
# 否则回退到「第一个可用方向」，避免强制选中缺失方向。
DEFAULT_DIRECTION = "SE"

BTN_STYLE = """
QPushButton {
    background: #2A2E33; border: 1px solid #3A3F46; color: #E8E4D9;
    border-radius: 6px; padding: 3px 8px; font-size: 14px;
    min-width: 0;                                /* 让按钮自适应 viewport 宽度，不被默认最小宽撑开 */
    min-height: 24px; max-height: 32px;          /* 锁高：防 B 区高度不足时 layout 压缩按钮导致文字被截 */
}
QPushButton:checked {
    border: 2px solid #D4AF37; color: #D4AF37;
}
QPushButton[missing="true"] {
    background: rgba(199,68,68,0.18); border: 1px solid #C74444; color: #E79A9A;
}
/* 该角色类型在本方向「不需要」的动作（如坐骑的 idle/run、NPC 的 attack）→ 灰显 */
QPushButton[unexpected="true"] {
    background: #23262a; border: 1px solid #2f343a; color: #5A636E;
}
"""
LABEL_STYLE = "color: #96A1AD; font-size: 12px; padding: 2px 0 1px 2px; letter-spacing: 1px;"


class ButtonStack(QFrame):
    """一组按钮：上方一行 label（可选右侧 toggle button），下方竖直堆叠一列按钮（B 区左侧占位用）。

    header_btn_text 非 None 时，label 与 toggle 按钮同行显示（如「方向 ⇄ 显向」）。
    """

    selected = Signal(str)

    def __init__(self, label: str, parent=None, header_btn_text: str | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        lbl = QLabel(label)
        lbl.setStyleSheet(LABEL_STYLE)
        self.header_btn: QPushButton | None = None
        if header_btn_text:
            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(4)
            header_row.addWidget(lbl)
            header_row.addStretch(1)
            self.header_btn = QPushButton(header_btn_text)
            self.header_btn.setCheckable(True)
            self.header_btn.setFixedHeight(20)
            self.header_btn.setStyleSheet(
                "QPushButton { background: #2A2E33; border: 1px solid #3A3F46; border-radius: 4px;"
                " color: #96A1AD; padding: 1px 8px; font-size: 11px; }"
                "QPushButton:checked { background: rgba(212,175,55,0.18); border-color: #D4AF37; color: #D4AF37; }"
                "QPushButton:hover { border-color: #D4AF37; color: #D4AF37; }"
            )
            header_row.addWidget(self.header_btn)
            outer.addLayout(header_row)
        else:
            outer.addWidget(lbl)

        self._body = QFrame()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        # 不再 addStretch：stretch 会让 outer layout 把 ButtonStack 压成 minSize，
        # 导致 matrix_layout 把 dir_stack/act_stack 都缩到 ~22px，按钮堆成几像素。
        # 改为让 layout 按"内容 + 滚动条"自然撑开。
        outer.addWidget(self._body)

        self._buttons: dict[str, QPushButton] = {}

    def rebuild(self, names: list[str], missing: set[str], current: str | None,
                unexpected: set[str] | None = None) -> None:
        unexpected = unexpected or set()
        for btn in self._buttons.values():
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        # 全清（含 stretch/残留 spacer）
        while self._layout.count() > 0:
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for name in names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setProperty("missing", name in missing)
            if name in unexpected:
                btn.setProperty("unexpected", True)
            btn.setStyleSheet(BTN_STYLE)
            btn.setChecked(name == current)
            btn.clicked.connect(lambda _=False, n=name: self.selected.emit(n))
            self._layout.addWidget(btn)
            self._buttons[name] = btn

    def set_current(self, name: str) -> None:
        for n, btn in self._buttons.items():
            btn.setChecked(n == name)


class ButtonMatrix(QFrame):
    """方向组 + 动作组，两组纵向堆叠，供 B 区左侧使用。"""

    direction_selected = Signal(str)
    action_selected = Signal(str)
    overlay_toggled = Signal(bool)   # 「显向」开关

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dir_stack = ButtonStack("方向", header_btn_text="显向")
        self.act_stack = ButtonStack("动作")
        layout.addWidget(self.dir_stack)
        layout.addWidget(self.act_stack)
        self.dir_stack.selected.connect(self.direction_selected)
        self.act_stack.selected.connect(self.action_selected)
        if self.dir_stack.header_btn is not None:
            self.dir_stack.header_btn.toggled.connect(self.overlay_toggled)

        # 宽度由父级 QScrollArea 的 viewport 管理（anim_view.LEFT_PANEL_WIDTH 决定），
        # 这里不再 setFixedWidth——避免 widgetResizable=True 时 widget 与 viewport 宽度不一致被裁边。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        # 与画布左侧对齐留少量内边距
        self.setStyleSheet("ButtonMatrix { padding: 0 4px 0 4px; }")

        self._tpl: Template | None = None
        self._part: PartData | None = None

    def set_template(self, tpl: Template) -> None:
        self._tpl = tpl
        self.dir_stack.rebuild(tpl.directions, set(tpl.directions), None)
        self.act_stack.rebuild(tpl.actions, set(tpl.actions), None)

    def show_part(self, part: PartData, direction: str | None, action: str | None) -> None:
        """按部件扫描结果刷新两列按钮的缺失状态。"""
        self._part = part
        tpl = self._tpl
        if tpl is None:
            return
        miss_dirs = set(part.missing_directions)
        if direction is None:
            # 首次进入：优先默认方向 SE（若部件拥有），否则取第一个可用方向
            avail = part.available_directions()
            direction = DEFAULT_DIRECTION if DEFAULT_DIRECTION in avail else (avail[0] if avail else None)
        elif direction in miss_dirs:
            avail = part.available_directions()
            direction = avail[0] if avail else None
        self.dir_stack.rebuild(tpl.directions, miss_dirs, direction)

        miss_acts: set[str] = set()
        if direction:
            # 三态：expected（该类型×方向约定动作）/ present（实际拥有）/ unexpected（不适用）
            # 用 effective_type（覆盖 wings/mount/npc/空），与 scanner 查漏一致
            eff_type = part.effective_type or part.character_type
            expected = set(tpl.expected_actions(eff_type, direction))
            present = set(part.available_actions(direction))
            miss_acts = expected - present          # 约定要有却没有 → 红
            unexpected = set(tpl.actions) - expected  # 本类型不需要 → 灰
            if action is None or action in miss_acts:
                avail = sorted(expected & present)
                action = avail[0] if avail else None
        else:
            unexpected = set(tpl.actions)
        self.act_stack.rebuild(tpl.actions, miss_acts, action, unexpected)

    def show_group(self, group: IdGroup, direction: str | None, action: str | None) -> None:
        """组视图：按钮基于组内所有部件的并集 (方向,动作)。

        缺失标记 = 模板要求但组内无任何部件拥有的组合（快速看出这个 ID 整体缺什么）。
        """
        self._part = None
        tpl = self._tpl
        if tpl is None:
            return
        avail_d: set[str] = set()
        owned_a: set[str] = set()
        for p in group.parts:
            for d in p.available_directions():
                avail_d.add(d)
                owned_a |= set(p.available_actions(d))
        miss_dirs = set(tpl.directions) - avail_d
        if direction is None:
            # 首次进入：优先默认方向 SE（若组拥有），否则取第一个可用方向（字母序）
            direction = DEFAULT_DIRECTION if DEFAULT_DIRECTION in avail_d else (sorted(avail_d)[0] if avail_d else None)
        elif direction in miss_dirs:
            direction = sorted(avail_d)[0] if avail_d else None
        self.dir_stack.rebuild(tpl.directions, miss_dirs, direction)

        if direction:
            # 组级三态：以「类型 × 方向」基准对照组内并集拥有（见 scanner._group_parts）
            # 用 effective_type（覆盖 wings/mount/npc），与 scanner 组级查漏一致
            eff_type = group.effective_type or group.character_type
            expected = set(tpl.expected_actions(eff_type, direction))
            owned_a_dir: set[str] = set()
            for p in group.parts:
                owned_a_dir |= set(p.available_actions(direction))
            miss_acts = set(group.missing_actions.get(direction, [])) or (expected - owned_a_dir)
            unexpected = set(tpl.actions) - expected
            if action is None or action in miss_acts:
                avail = sorted(expected & owned_a_dir)
                action = avail[0] if avail else None
            self.act_stack.rebuild(tpl.actions, miss_acts, action, unexpected)
        else:
            self.act_stack.rebuild(tpl.actions, set(), None, set(tpl.actions))

    def current(self) -> tuple[str | None, str | None]:
        d = next((n for n, b in self.dir_stack._buttons.items() if b.isChecked()), None)
        a = next((n for n, b in self.act_stack._buttons.items() if b.isChecked()), None)
        return d, a
