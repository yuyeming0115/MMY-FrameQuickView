"""方向/动作按钮矩阵：B 区左侧纵向堆叠（顶 label + 每行一个按钮）。

不再用横向"扁条"——把按钮排成竖列，让 B 区画布保留最大横向空间。

三态：缺（missing）/ 不适用（unexpected）/ 正常；查漏靠颜色，不用下拉。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QPushButton, QHBoxLayout, QSizePolicy

from ..core.scanner import PartData, IdGroup
from ..core.stats import group_combos, part_combos
from ..core.template import Template

# 默认方向：首次进入某部件/组（direction=None）时高亮的方向。
# 用户 2026-08-19 要求默认 SE（东南）。仅当该部件/组确实拥有 SE 时才生效，
# 否则回退到「第一个可用方向」，避免强制选中缺失方向。
DEFAULT_DIRECTION = "SE"

# 默认动作：首次进入（或当前动作已缺失）时高亮的动作。
# 用户 2026-08-29 要求「打开先看待机」：常规类型（主角/伙伴/怪物/BOSS/NPC/翅膀）
# 优先 idle，坐骑类（mount）优先 ride_idle；都没有则回退字母序第一个。
#
# ⚠ 不能直接取 sorted(可用动作)[0]：主角 SE 方向约定动作是
# [idle, run, attack, skill, hurt, block, dead, ride_idle, ride_run]，
# 字母序第一个是 attack，打开就播攻击动画，不符合直觉。
DEFAULT_ACTION = "idle"
DEFAULT_ACTION_BY_TYPE = {"mount": "ride_idle"}

# M36：空序列角标文本（该 (方向,动作) 抽样首帧全透明 → 角标红色「空」）
BLANK_BADGE = "空"


def pick_default_action(eff_type: str | None, avail: list[str],
                        all_avail: list[str] | None = None) -> str | None:
    """从可用动作里挑默认动作：按类型优先，该动作缺失则回退字母序第一个。

    - avail：约定动作 ∩ 实际拥有（正常 fallback 来源）
    - all_avail：部件实际拥有的**全部**动作。当 avail 为空（约定动作一个都不中，
      例如坐骑影子只有 ride_idle/ride_run，却被当作非坐骑类型查漏），回退到
      实际动作，仍能正确落到 ride_idle，而不是变成 None（B 区不播放）。
    """
    if avail:
        candidates = avail
    elif all_avail:
        candidates = all_avail
    else:
        return None
    preferred = DEFAULT_ACTION_BY_TYPE.get(eff_type or "", DEFAULT_ACTION)
    return preferred if preferred in candidates else candidates[0]

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

# M32.3 角标分色（用户反馈迭代：金→紫）：蓝色 = 方向按钮·动作数；
# 紫色 = 动作按钮·帧数；红色 = 断档/帧数不一致（优先级最高）
BADGE_PURPLE = "QLabel { background: #9C6ADE; color: #FFFFFF; font-size: 11px; font-weight: 500; border-radius: 8px; padding: 0 5px; }"
BADGE_BLUE = "QLabel { background: #4C8FD6; color: #FFFFFF; font-size: 11px; font-weight: 500; border-radius: 8px; padding: 0 5px; }"
BADGE_RED = "QLabel { background: #C74444; color: #FFFFFF; font-size: 11px; font-weight: 500; border-radius: 8px; padding: 0 5px; }"


class BadgeButton(QPushButton):
    """带右上角帧数角标的按钮（M32）：金角标 = 帧数，红角标 = 断档/不一致。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._badge = QLabel(self)
        self._badge.hide()

    def set_badge(self, text: str | None, danger: bool = False,
                  tone: str = "purple") -> None:
        if not text:
            self._badge.hide()
            return
        self._badge.setText(text)
        style = BADGE_RED if danger else (BADGE_BLUE if tone == "blue" else BADGE_PURPLE)
        self._badge.setStyleSheet(style)
        self._badge.adjustSize()
        self._reposition_badge()
        self._badge.show()
        self._badge.raise_()

    def _reposition_badge(self) -> None:
        b = self._badge
        if not b.isVisible() and not b.text():
            return
        b.move(max(1, self.width() - b.width() - 3), 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_badge()


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
                unexpected: set[str] | None = None,
                counts: dict[str, int] | None = None,
                danger: set[str] | None = None,
                badge_tone: str = "purple") -> None:
        unexpected = unexpected or set()
        counts = counts or {}
        danger = danger or set()
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
            btn = BadgeButton(name)
            btn.setCheckable(True)
            btn.setProperty("missing", name in missing)
            if name in unexpected:
                btn.setProperty("unexpected", True)
            btn.setStyleSheet(BTN_STYLE)
            btn.setChecked(name == current)
            if name in counts:
                btn.set_badge(str(counts[name]), danger=name in danger, tone=badge_tone)
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
        self._group: IdGroup | None = None     # 组视图引用（apply_blank 就地刷新用）
        # M36：当前选中项的空序列集合 {(direction, action)}（app 后台抽样后回填，
        # show_part/show_group 重建角标时读取）
        self._blank_seqs: set[tuple[str, str]] = set()

    def set_blank(self, blank: set[tuple[str, str]]) -> None:
        """M36：设置当前选中项的空序列集合（配合 app 重新刷新矩阵生效）。"""
        self._blank_seqs = blank or set()

    def apply_blank(self, blank: set[tuple[str, str]]) -> None:
        """M36：空帧抽样完成后**就地**刷新现存按钮的角标（不重建按钮）。

        异步全量重建会让外部持有的按钮引用/悬停态失效，这里只对现有按钮
        set_badge；数据同样写入 _blank_seqs，下次 show_part/show_group 重建时生效。
        """
        self._blank_seqs = blank or set()
        if self._tpl is None:
            return
        if self._part is not None:
            dir_counts, dir_danger = self._dir_counts_for_part(self._part)
            self._refresh_badges(self.dir_stack, dir_counts, dir_danger, tone="blue")
            direction = self._checked_name(self.dir_stack)
            badges = self._part_badges(self._part, direction)
            self._refresh_badges(self.act_stack, badges["counts"], badges["danger"],
                                 tone="purple")
        elif self._group is not None:
            st = group_combos(self._group, self._tpl)
            db = self._dir_badges(st)
            self._refresh_badges(self.dir_stack, db["counts"], db["danger"], tone="blue")
            direction = self._checked_name(self.dir_stack)
            gb = self._group_badges(st, direction)
            self._refresh_badges(self.act_stack, gb["counts"], gb["danger"], tone="purple")

    @staticmethod
    def _checked_name(stack: ButtonStack) -> str | None:
        return next((n for n, b in stack._buttons.items() if b.isChecked()), None)

    def _refresh_badges(self, stack: ButtonStack, counts: dict, danger: set,
                        tone: str) -> None:
        for name, btn in stack._buttons.items():
            if name in counts:
                btn.set_badge(str(counts[name]), danger=name in danger, tone=tone)
            else:
                btn.set_badge(None)

    def set_template(self, tpl: Template) -> None:
        self._tpl = tpl
        self.dir_stack.rebuild(tpl.directions, set(tpl.directions), None)
        self.act_stack.rebuild(tpl.actions, set(tpl.actions), None)

    def show_part(self, part: PartData, direction: str | None, action: str | None) -> None:
        """按部件扫描结果刷新两列按钮的缺失状态。"""
        self._part = part
        self._group = None
        tpl = self._tpl
        if tpl is None:
            return
        if part.is_flat:
            # 扁平资源（特效类）：方向/动作来自实际数据（虚拟方向 + 序列前缀），
            # 不对照模板列表，也不做缺失标记
            avail = part.available_directions()
            if direction not in avail:
                direction = avail[0] if avail else None
            # M32.1：方向角标 = 该虚拟方向下的序列数
            dir_counts = {d: len(part.available_actions(d)) for d in avail}
            self.dir_stack.rebuild(avail, set(), direction, None,
                                   counts=dir_counts, badge_tone="blue")
            acts = part.available_actions(direction) if direction else []
            if action not in acts:
                action = acts[0] if acts else None
            self.act_stack.rebuild(acts, set(), action, None,
                                   **self._part_badges(part, direction))
            return
        miss_dirs = set(part.missing_directions)
        unexpected_dirs = set(part.unexpected_directions)
        avail = part.available_directions()
        if direction is None:
            # 首次进入：优先默认方向 SE（若部件拥有），否则取第一个可用方向
            direction = DEFAULT_DIRECTION if DEFAULT_DIRECTION in avail else (avail[0] if avail else None)
        elif direction not in avail:
            # M33：保持的方向在新部件不可用（缺失/虚拟方向）→ 回退第一个可用，
            # 不能落在一个无 checked 的矩阵上（B 区将无资源可播）
            direction = avail[0] if avail else None
        # M32.1：方向角标 = 该方向下的动作数；方向内有断档 → 红角标；
        # M36：整方向全部序列为空图 → 角标红色「空」
        dir_counts, dir_danger = self._dir_counts_for_part(part)
        self.dir_stack.rebuild(tpl.directions, miss_dirs, direction, unexpected_dirs,
                               counts=dir_counts, danger=dir_danger, badge_tone="blue")

        miss_acts: set[str] = set()
        if direction:
            # 三态：expected（该类型×方向约定动作）/ present（实际拥有）/ unexpected（不适用）
            # 用 effective_type（覆盖 wings/mount/npc/空），与 scanner 查漏一致
            eff_type = part.effective_type or part.character_type
            expected = set(tpl.expected_actions(eff_type, direction))
            present = set(part.available_actions(direction))
            miss_acts = expected - present          # 约定要有却没有 → 红
            unexpected = set(tpl.actions) - expected  # 本类型不需要 → 灰
            # M33：保持的动作在新方向不可用（缺失或本类型不适用）→ 回退默认；
            # 只判 miss_acts 会漏掉 unexpected（如把角色的 run 带进坐骑），
            # 结果选中一个灰显且无资源的动作
            if action is None or action not in present:
                action = pick_default_action(
                    eff_type, sorted(expected & present), sorted(present))
        else:
            unexpected = set(tpl.actions)
        self.act_stack.rebuild(tpl.actions, miss_acts, action, unexpected,
                               **self._part_badges(part, direction))

    def _dir_counts_for_part(self, part: PartData) -> tuple[dict, set]:
        """方向角标数据：{方向: 动作数}；断档/整方向空图（M36）→ 红。"""
        dir_counts, dir_danger = {}, set()
        for d in part.available_directions():
            acts = part.available_actions(d)
            owned_ads = [ad for ad in (part.action_data(d, a) for a in acts)
                         if ad is not None]
            if owned_ads and all((d, a) in self._blank_seqs for a in acts):
                dir_counts[d] = BLANK_BADGE
                dir_danger.add(d)
            else:
                dir_counts[d] = len(owned_ads)
                if any(ad.gaps for ad in owned_ads):
                    dir_danger.add(d)
        return dir_counts, dir_danger

    def _part_badges(self, part: PartData, direction: str | None) -> dict:
        """M32：当前方向下各动作的帧数角标参数（断档 → 红角标；M36 空序列 → 红「空」）。"""
        counts: dict[str, int] = {}
        danger: set[str] = set()
        if direction:
            for a in part.available_actions(direction):
                ad = part.action_data(direction, a)
                if ad is None:
                    continue
                if (direction, a) in self._blank_seqs:
                    counts[a] = BLANK_BADGE
                    danger.add(a)
                    continue
                counts[a] = ad.count
                if ad.gaps:
                    danger.add(a)
        return {"counts": counts, "danger": danger}

    def show_group(self, group: IdGroup, direction: str | None, action: str | None) -> None:
        """组视图：按钮基于组内所有部件的并集 (方向,动作)。

        缺失标记 = 模板要求但组内无任何部件拥有的组合（快速看出这个 ID 整体缺什么）。
        """
        self._part = None
        self._group = group
        tpl = self._tpl
        if tpl is None:
            return
        # M32：组角标一次算全（并集行），当前方向的动作取对应行
        st = group_combos(group, tpl)
        if group.is_flat:
            # 扁平资源组（特效类）：按钮来自组内并集（虚拟方向 + 序列前缀），无缺失标记
            avail_d: set[str] = set()
            acts_by_dir: dict[str, set[str]] = {}
            for p in group.parts:
                for d in p.available_directions():
                    avail_d.add(d)
                    acts_by_dir.setdefault(d, set()).update(p.available_actions(d))
            if direction not in avail_d:
                direction = sorted(avail_d)[0] if avail_d else None
            # M32.1：方向角标 = 该虚拟方向下的序列数
            self.dir_stack.rebuild(sorted(avail_d), set(), direction, None,
                                   counts={d: len(v) for d, v in acts_by_dir.items()},
                                   badge_tone="blue")
            acts = sorted(acts_by_dir.get(direction, set())) if direction else []
            if action not in acts:
                action = acts[0] if acts else None
            self.act_stack.rebuild(acts, set(), action, None,
                                   **self._group_badges(st, direction))
            return
        avail_d: set[str] = set()
        owned_a: set[str] = set()
        for p in group.parts:
            for d in p.available_directions():
                avail_d.add(d)
                owned_a |= set(p.available_actions(d))
        miss_dirs = set(group.missing_directions)
        unexpected_dirs = set(group.unexpected_directions)
        if direction is None:
            # 首次进入：优先默认方向 SE（若组拥有），否则取第一个可用方向（字母序）
            direction = DEFAULT_DIRECTION if DEFAULT_DIRECTION in avail_d else (sorted(avail_d)[0] if avail_d else None)
        elif direction not in avail_d:
            # M33：保持的方向在新组不可用（缺失/虚拟方向）→ 回退第一个可用
            direction = sorted(avail_d)[0] if avail_d else None
        # M32.1：方向角标 = 该方向下的动作数（组内并集）；方向内有异常行 → 红角标
        # M36：缺失/不适用方向以 scanner 组级结果为准（worldboss 组的方向基准不同）
        self.dir_stack.rebuild(tpl.directions, miss_dirs, direction, unexpected_dirs,
                               **self._dir_badges(st), badge_tone="blue")

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
            # M33：保持的动作在新方向不可用（缺失或本类型不适用）→ 回退默认
            if action is None or action not in owned_a_dir:
                action = pick_default_action(
                    eff_type, sorted(expected & owned_a_dir), sorted(owned_a_dir))
            self.act_stack.rebuild(tpl.actions, miss_acts, action, unexpected,
                                   **self._group_badges(st, direction))
        else:
            self.act_stack.rebuild(tpl.actions, set(), None, set(tpl.actions))

    def _group_badges(self, st, direction: str | None) -> dict:
        """M32：组视图角标参数——与 HUD 同口径（主件帧数；不一致/断档 → 红）。

        M36：空序列（抽样首帧全透明）→ 红色「空」角标。
        """
        counts: dict[str, int] = {}
        danger: set[str] = set()
        if direction:
            for r in st.rows:
                if r.direction != direction:
                    continue
                if (direction, r.action) in self._blank_seqs:
                    counts[r.action] = BLANK_BADGE
                    danger.add(r.action)
                    continue
                counts[r.action] = r.count
                if r.has_issues:
                    danger.add(r.action)
        return {"counts": counts, "danger": danger}

    def _dir_badges(self, st) -> dict:
        """M32.1：组视图方向角标 = 该方向并集动作数；方向内有异常行 → 红。

        M36：整方向全部序列为空图 → 角标红色「空」。
        """
        acts_by_dir: dict[str, set[str]] = {}
        danger: set[str] = set()
        for r in st.rows:
            acts_by_dir.setdefault(r.direction, set()).add(r.action)
            if r.has_issues:
                danger.add(r.direction)
        counts: dict[str, int] = {}
        for d, acts in acts_by_dir.items():
            if acts and all((d, a) in self._blank_seqs for a in acts):
                counts[d] = BLANK_BADGE
                danger.add(d)
            else:
                counts[d] = len(acts)
        return {"counts": counts, "danger": danger}

    def current(self) -> tuple[str | None, str | None]:
        d = next((n for n, b in self.dir_stack._buttons.items() if b.isChecked()), None)
        a = next((n for n, b in self.act_stack._buttons.items() if b.isChecked()), None)
        return d, a
