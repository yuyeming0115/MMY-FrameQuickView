"""左栏部件列表：按 ID 分组 + 中文名 + 可搜索 + 缺漏红点 + 内联改名。

- 组头显示 `502019 · 杜如晦`（来自 NameMap，无映射则只显示 ID）
- 组头 F2/双击可改中文名，回车写回匹配表 txt
- ↑↓ 键移动、回车打开（QTreeWidget 原生支持）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QFrame, QLineEdit, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QAbstractItemView,
)

from ..core.namemap import NameMap
from ..core.scanner import PartData, ScanResult

GOLD = QColor("#D4AF37")
SUB = QColor("#96A1AD")
TEXT = QColor("#E8E4D9")


class PartList(QFrame):
    part_selected = Signal(object)  # PartData

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: dict[str, PartData] = {}   # folder str -> PartData
        self._namemap: NameMap | None = None
        self._result: ScanResult | None = None
        self._loading = False                    # 防止 itemChanged 递归

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 过滤部件 / 中文名…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
        self.tree.itemSelectionChanged.connect(self._on_select)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        self.count_label = QLabel("未加载")
        self.count_label.setStyleSheet("color: #96A1AD; font-size: 12px; padding: 2px 4px;")
        layout.addWidget(self.count_label)

        self.setMinimumWidth(210)
        self.setMaximumWidth(300)

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
                grp_item.setData(0, Qt.UserRole, None)
                # 组头可编辑（改名），不可作为部件选中
                grp_item.setFlags(
                    (grp_item.flags() | Qt.ItemIsEditable) & ~Qt.ItemIsSelectable
                )
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
                grp_item.setExpanded(True)

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

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        key = items[0].data(0, Qt.UserRole)
        if key and key in self._parts:
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
