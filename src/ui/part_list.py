"""左栏部件列表：按 ID 分组 + 中文名 + 可搜索 + 缺漏红点 + 内联改名。

- 组头显示 `502019 · 杜如晦`（来自 NameMap，无映射则只显示 ID）
- 组头 F2/双击可改中文名，回车写回匹配表 txt
- ↑↓ 键移动、回车打开（QTreeWidget 原生支持）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QAbstractItemView, QHeaderView,
)

from .hover_scroll import enable_hover_scroll

from ..core.namemap import NameMap
from ..core.scanner import PartData, ScanResult

GOLD = QColor("#D4AF37")
SUB = QColor("#96A1AD")
TEXT = QColor("#E8E4D9")

# 列宽不再硬编码：树第 0 列在 resizeEvent 里跟随容器宽度（容器宽 - 折叠按钮 28 - padding 12 ≈ - 40）。
# 容器允许拖动范围 220~480，列宽自然跟动。
LIST_COL_MIN_WIDTH = 160       # 列宽下限（防止 splitter 拖太小时列被压到 0）
LIST_COL_PADDING = 40           # 容器宽 - 折叠按钮 28 - 内边距 12


class PartList(QFrame):
    part_selected = Signal(object)  # PartData
    group_selected = Signal(str)    # res_id（点击组头时触发，用于同ID叠层显示）
    pick_namemap = Signal()         # 点击「📖 选择匹配表」按钮时触发，app 打开文件选择

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: dict[str, PartData] = {}   # folder str -> PartData
        self._namemap: NameMap | None = None
        self._result: ScanResult | None = None
        self._loading = False                    # 防止 itemChanged 递归

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 过滤框 + 折叠/展开 toggle
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 过滤部件 / 中文名…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 1)

        self.collapse_btn = QPushButton("▶")
        self.collapse_btn.setFixedWidth(28)
        self.collapse_btn.setToolTip("折叠 / 展开 所有 ID 组")
        self.collapse_btn.setStyleSheet(
            "QPushButton { background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;"
            " color: #96A1AD; padding: 4px 0; font-size: 13px; }"
            "QPushButton:hover { border-color: #D4AF37; color: #D4AF37; }"
        )
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        filter_row.addWidget(self.collapse_btn)

        # 「📖 选择匹配表」按钮：手动指定 ID-中文名映射 txt，
        # 兜底自动发现失败 / QSettings 失效的情况，比顶部菜单更直观。
        self.map_btn = QPushButton("📖")
        self.map_btn.setFixedWidth(28)
        self.map_btn.setToolTip("选择 ID-中文名匹配表（手动指定 txt）")
        self.map_btn.setStyleSheet(
            "QPushButton { background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;"
            " color: #96A1AD; padding: 4px 0; font-size: 13px; }"
            "QPushButton:hover { border-color: #D4AF37; color: #D4AF37; }"
        )
        self.map_btn.clicked.connect(lambda: self.pick_namemap.emit())
        filter_row.addWidget(self.map_btn)
        layout.addLayout(filter_row)

        self._all_expanded = False  # 默认折叠（▶ 状态）；rebuild 后所有 ID 组收起

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
        self.tree.itemSelectionChanged.connect(self._on_select)
        self.tree.itemChanged.connect(self._on_item_changed)
        # 滚动条按需出现；overlay 样式下隐藏时不占布局空间，避免切换条目时宽度回弹。
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        enable_hover_scroll(self.tree)  # 鼠标离开自动隐藏滚动条
        self.tree.setUniformRowHeights(True)        # 行高一致 = sizeHint 不抖
        self.tree.setExpandsOnDoubleClick(False)
        # 锁定第 0 列宽度 = 不再随「最长可见条目」自动 resize 导致侧栏扩缩。
        header = self.tree.header()
        header.setStretchLastSection(False)              # 关键：关闭末列自动拉伸，Fixed 才真正生效
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        # 初始化时按当前容器宽（此时 = 220，setMinimumWidth 触发）算一列宽，
        # 后续真正拖 splitter 时由 resizeEvent 同步刷新。
        header.resizeSection(0, max(LIST_COL_MIN_WIDTH, self.width() - LIST_COL_PADDING))
        layout.addWidget(self.tree, 1)

        self.count_label = QLabel("未加载")
        self.count_label.setStyleSheet("color: #96A1AD; font-size: 12px; padding: 2px 4px;")
        layout.addWidget(self.count_label)

        # 左栏宽度允许 splitter 拖动：220~480 区间。
        #   - setMinimumWidth(220) 防 splitter 拖太小看不到 ID；
        #   - setMaximumWidth(480) 防左栏过大挤压右栏画布；
        #   - 树内列宽由 resizeEvent 同步跟随容器宽 = self.width() - 40。
        # 之前 setFixedWidth(220) 锁死导致长 ID+中文（如"50142004 · 月河郡主(琴)·发"）出省略号。
        self.setMinimumWidth(220)
        self.setMaximumWidth(480)

    def resizeEvent(self, event) -> None:
        """容器宽度变化（splitter 拖动）时，同步树第 0 列宽 = 容器宽 - 折叠按钮 28 - padding 12。"""
        super().resizeEvent(event)
        new_w = max(LIST_COL_MIN_WIDTH, self.width() - LIST_COL_PADDING)
        header = self.tree.header()
        if header.sectionSize(0) != new_w:
            header.resizeSection(0, new_w)

    # ---------------- 数据 ----------------
    def set_namemap(self, namemap: NameMap) -> None:
        self._namemap = namemap

    def load_result(self, result: ScanResult) -> None:
        self._result = result
        self._rebuild()
        # 默认选中第一个部件
        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            if grp_item.childCount() > 0:
                self.tree.setCurrentItem(grp_item.child(0))
                break

    def refresh_names(self) -> None:
        """匹配表热更新后刷新显示（保持当前选中）。"""
        current = None
        items = self.tree.selectedItems()
        if items:
            current = items[0].data(0, Qt.UserRole)
        self._rebuild()
        if current:
            self._select_by_key(current)

    def _rebuild(self) -> None:
        if self._result is None:
            return
        self._loading = True
        try:
            self.tree.clear()
            self._parts.clear()

            for grp in self._result.groups:
                header = grp.res_id
                if self._namemap:
                    # 用组内任意部件的文件夹名做精确查找
                    first = grp.parts[0]
                    header = self._namemap.display(first.name, grp.res_id)
                if grp.has_issues:
                    header += "  🔴"
                grp_item = QTreeWidgetItem([header])
                grp_item.setForeground(0, QBrush(SUB))
                grp_item.setData(0, Qt.UserRole, "GRP:" + grp.res_id)
                # 组头可选中（叠层显示）且可编辑（改名）
                grp_item.setFlags(grp_item.flags() | Qt.ItemIsEditable | Qt.ItemIsSelectable)
                self.tree.addTopLevelItem(grp_item)

                for pd in grp.parts:
                    label = pd.part if pd.part else "（整体资源）"
                    if self._namemap:
                        label = f"{label} · {self._namemap.part_cn(pd.part)}"
                    if pd.has_issues:
                        label += "  🔴"
                    child = QTreeWidgetItem([label])
                    child.setData(0, Qt.UserRole, str(pd.folder))
                    child.setFlags(child.flags() & ~Qt.ItemIsEditable)
                    child.setForeground(0, QBrush(GOLD if pd.part else TEXT))
                    grp_item.addChild(child)
                    self._parts[str(pd.folder)] = pd
                grp_item.setExpanded(False)   # 默认折叠：只显示 ID/角色 组头，点击 ▼ 展开

            n_issue = sum(1 for p in self._result.parts if p.has_issues)
            self.count_label.setText(
                f"共 {len(self._result.parts)} 项 · 按 ID 分组 · {n_issue} 项缺漏"
                + (f" · 忽略 {len(self._result.ignored)}" if self._result.ignored else "")
            )
            self._apply_filter(self.filter_edit.text())
        finally:
            self._loading = False

    def _select_by_key(self, key: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            for j in range(grp_item.childCount()):
                child = grp_item.child(j)
                if child.data(0, Qt.UserRole) == key:
                    self.tree.setCurrentItem(child)
                    return

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            grp_hit = text in grp_item.text(0).lower()
            visible_children = 0
            for j in range(grp_item.childCount()):
                child = grp_item.child(j)
                hit = (not text) or grp_hit or text in child.text(0).lower()
                child.setHidden(not hit)
                visible_children += int(hit)
            grp_item.setHidden(bool(text) and not grp_hit and visible_children == 0)

    def _toggle_collapse(self) -> None:
        """一键折叠 / 展开所有 ID 组。"""
        if self._all_expanded:
            self.tree.collapseAll()
            self.collapse_btn.setText("▶")
        else:
            self.tree.expandAll()
            self.collapse_btn.setText("▼")
        self._all_expanded = not self._all_expanded

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        key = items[0].data(0, Qt.UserRole)
        if not key:
            return
        if key.startswith("GRP:"):
            self.group_selected.emit(key[4:])
        elif key in self._parts:
            self.part_selected.emit(self._parts[key])

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        """组头内联改名：`502019 · 杜如晦` → 取 · 后的部分写回匹配表。"""
        if self._loading or self._namemap is None or self._result is None:
            return
        # 只处理组头（UserRole 为 None 且有 res_id）
        res_id = None
        for grp in self._result.groups:
            if grp.res_id in item.text(0).split(" · ")[0]:
                res_id = grp.res_id
                break
        if res_id is None:
            return
        text = item.text(0).replace("  🔴", "")
        new_name = text.split(" · ", 1)[1].strip() if " · " in text else ""
        if new_name:
            self._namemap.set_name(res_id, new_name)
        self.refresh_names()
