"""左栏部件列表：按 ID 分组 + 中文名 + 可搜索 + 分类 chips 过滤 + 缺漏红点 + 内联改名。

- 组头显示 `502019 · 杜如晦`（来自 NameMap，无映射则只显示 ID）
- 过滤框下方一行分类 chips（主角/伙伴/怪物/BOSS/NPC/坐骑/翅膀/特效，来自模板
  categories 规则），单击过滤、再点取消，与搜索框叠加（AND）
- 组头 F2/双击可改中文名，回车写回匹配表 txt
- ↑↓ 键移动、回车打开（QTreeWidget 原生支持）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QLabel, QMenu, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QAbstractItemView, QHeaderView, QWidget,
)

from .hover_scroll import enable_hover_scroll
from .flow_layout import FlowLayout

from ..core.namemap import NameMap
from ..core.scanner import PartData, ScanResult
from ..core.template import Template

GOLD = QColor("#D4AF37")
SUB = QColor("#96A1AD")
TEXT = QColor("#E8E4D9")

CHIP_STYLE = (
    "QPushButton { background: #2A2E33; border: 1px solid #3A3F46; border-radius: 4px;"
    " color: #96A1AD; padding: 2px 10px; font-size: 13px; }"
    "QPushButton:checked { color: #D4AF37; border-color: #D4AF37;"
    " background: rgba(212,175,55,0.12); }"
    "QPushButton:hover { border-color: #D4AF37; color: #D4AF37; }"
)

# 列宽不再硬编码：树第 0 列在 resizeEvent 里跟随容器宽度（容器宽 - 折叠按钮 28 - padding 12 ≈ - 40）。
# 容器允许拖动范围 220~480，列宽自然跟动。
LIST_COL_MIN_WIDTH = 160       # 列宽下限（防止 splitter 拖太小时列被压到 0）
LIST_COL_PADDING = 40           # 容器宽 - 折叠按钮 28 - 内边距 12


class PartList(QFrame):
    part_selected = Signal(object)  # PartData
    group_selected = Signal(str)    # 组 key（点击组头时触发：ID 组=res_id，套装组=父文件夹路径）
    pick_namemap = Signal()         # 点击「📖 选择匹配表」按钮时触发，app 打开文件选择

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: dict[str, PartData] = {}   # folder str -> PartData
        self._namemap: NameMap | None = None
        self._result: ScanResult | None = None
        self._loading = False                    # 防止 itemChanged 递归
        self._fills_check = True                 # fills 警告检测开关（关闭=不显示 🟠）

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

        # 分类 chips 行（M21）：过滤框正下方，流式换行；只显示当前结果存在的分类
        self._category_order: list[str] = []        # 模板 categories 顺序
        self._active_category: str = ""             # "" = 全部
        self._chips: dict[str, QPushButton] = {}    # category(""=全部) -> chip
        self._chips_host = QWidget()
        self._chips_host.setStyleSheet("background: transparent;")
        self._chips_layout = FlowLayout(self._chips_host, margin=0, spacing=4)
        self._chips_host.hide()
        layout.addWidget(self._chips_host)

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
        # 右键菜单：复制 ID / ID·中文名 / 文件夹路径
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
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

    def set_template(self, tpl: Template | None) -> None:
        """模板分类顺序（chips 顺序与模板 categories 配置一致）。"""
        self._category_order = tpl.category_names() if tpl else []

    def set_fills_check(self, enabled: bool) -> None:
        """fills 警告检测开关：关闭后左栏不再显示 🟠 橙点（🔴 红点缺漏不受影响）。

        轻量路径：只更新现有条目的标记文本，不重建树——重建会触发选择变化，
        导致 A/B 区重新解码（资源多时明显卡顿）。
        """
        if self._fills_check == enabled:
            return
        self._fills_check = enabled
        self._refresh_dots()

    def _refresh_dots(self) -> None:
        """遍历现有树条目，仅更新 🔴/🟠 标记（不重建、不触发选择变化）。"""
        if self._result is None:
            return
        grp_by_id = {g.key: g for g in self._result.groups}
        self._loading = True   # 防 setText 触发 itemChanged 走改名回写逻辑
        try:
            for i in range(self.tree.topLevelItemCount()):
                grp_item = self.tree.topLevelItem(i)
                key = grp_item.data(0, Qt.UserRole) or ""
                if not key.startswith("GRP:"):
                    continue
                grp = grp_by_id.get(key[4:])
                if grp is None:
                    continue
                text = grp_item.text(0).replace("  🔴", "").replace("  🟠", "")
                if grp.has_issues:
                    text += "  🔴"
                elif self._fills_check and grp.has_warnings:
                    text += "  🟠"
                grp_item.setText(0, text)
                for j in range(grp_item.childCount()):
                    child = grp_item.child(j)
                    pd = self._parts.get(child.data(0, Qt.UserRole))
                    if pd is None:
                        continue
                    ctext = child.text(0).replace("  🔴", "").replace("  🟠", "")
                    if pd.has_issues:
                        ctext += "  🔴"
                    elif self._fills_check and pd.has_warnings:
                        ctext += "  🟠"
                    child.setText(0, ctext)
        finally:
            self._loading = False

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
                if grp.is_outfit:
                    # 套装组（M23）：显示父文件夹名（去 _部件 后缀）+ 套装标记，不查匹配表
                    header = f"{grp.display_name}（套装·{len(grp.parts)}件）"
                else:
                    header = grp.res_id
                    if self._namemap:
                        # 用组内任意部件的文件夹名做精确查找
                        first = grp.parts[0]
                        header = self._namemap.display(first.name, grp.res_id)
                if grp.has_issues:
                    header += "  🔴"
                elif self._fills_check and grp.has_warnings:
                    header += "  🟠"
                grp_item = QTreeWidgetItem([header])
                grp_item.setForeground(0, QBrush(SUB))
                grp_item.setData(0, Qt.UserRole, "GRP:" + grp.key)
                grp_item.setData(0, Qt.UserRole + 1, grp.category)
                # 组头可选中（叠层显示）；ID 组可编辑（F2 改名写匹配表），套装组不可
                flags = grp_item.flags() | Qt.ItemIsSelectable
                if not grp.is_outfit:
                    flags |= Qt.ItemIsEditable
                grp_item.setFlags(flags)
                self.tree.addTopLevelItem(grp_item)

                # 影子归属：组内同 ID 主件（武器影子/身体影子），套装跨 ID 双影子可区分
                sibs = [(q.res_id, q.part) for q in grp.parts]
                for pd in grp.parts:
                    if pd.is_flat:
                        # 特效层（M25）：显示匹配表中文名（如 游龙「环绕特效」），兜底「特效」
                        cn = (self._namemap.lookup(pd.name, pd.res_id) or "特效") \
                            if self._namemap else "特效"
                    elif self._namemap:
                        cn, _ = self._namemap.part_cn_in(pd.part, pd.res_id, sibs)
                    else:
                        cn = pd.part or "（整体资源）"
                    if grp.is_outfit:
                        # 套装组跨 ID：子项 = `ID 中文`（如 501031005 武器影子）
                        label = f"{pd.res_id} {cn}"
                    else:
                        label = pd.part if pd.part else "（整体资源）"
                        if self._namemap:
                            label = f"{label} · {cn}"
                    if pd.has_issues:
                        label += "  🔴"
                    elif self._fills_check and pd.has_warnings:
                        label += "  🟠"
                    child = QTreeWidgetItem([label])
                    child.setData(0, Qt.UserRole, str(pd.folder))
                    child.setFlags(child.flags() & ~Qt.ItemIsEditable)
                    child.setForeground(0, QBrush(GOLD if pd.part else TEXT))
                    grp_item.addChild(child)
                    self._parts[str(pd.folder)] = pd
                grp_item.setExpanded(False)   # 默认折叠：只显示 ID/角色 组头，点击 ▼ 展开

            self._rebuild_chips()
            self._apply_filter(self.filter_edit.text())
        finally:
            self._loading = False

    # ---------------- 分类 chips ----------------
    def _rebuild_chips(self) -> None:
        """按当前扫描结果重建分类 chips：只列出实际存在的分类（模板顺序）。"""
        for btn in self._chips.values():
            btn.hide()
            btn.deleteLater()
        self._chips.clear()
        self._active_category = ""
        if self._result is None:
            self._chips_host.hide()
            return
        present = {g.category for g in self._result.groups if g.category}
        names = [c for c in self._category_order if c in present]
        if len(names) < 2:      # 分类不足 2 个时隐藏整行，不占左栏空间
            self._chips_host.hide()
            return
        self._make_chip("全部", "")
        for name in names:
            self._make_chip(name, name)
        self._chips_host.show()
        self._sync_chips()

    def _make_chip(self, text: str, category: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(24)
        btn.setStyleSheet(CHIP_STYLE)
        btn.setToolTip(f"只看「{text}」类资源（再点一次取消）")
        btn.clicked.connect(lambda _=False, c=category: self._on_chip_clicked(c))
        self._chips_layout.addWidget(btn)
        self._chips[category] = btn
        return btn

    def _on_chip_clicked(self, category: str) -> None:
        if not category or self._active_category == category:
            self._active_category = ""    # 点「全部」或再点当前 chip → 取消过滤
        else:
            self._active_category = category
        self._sync_chips()
        self._apply_filter(self.filter_edit.text())

    def _sync_chips(self) -> None:
        for cat, btn in self._chips.items():
            btn.setChecked(cat == self._active_category)

    def _refresh_count(self) -> None:
        if self._result is None:
            return
        n_issue = sum(1 for p in self._result.parts if p.has_issues)
        text = (f"共 {len(self._result.parts)} 项 · 按 ID 分组 · {n_issue} 项缺漏"
                + (f" · 忽略 {len(self._result.ignored)}" if self._result.ignored else ""))
        if self._active_category:
            total = self.tree.topLevelItemCount()
            visible = sum(1 for i in range(total)
                          if not self.tree.topLevelItem(i).isHidden())
            text = f"{self._active_category} · {visible}/{total} 组 · " + text
        self.count_label.setText(text)

    def _select_by_key(self, key: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            # 先检查组头本身（key = "GRP:xxxx"）
            if grp_item.data(0, Qt.UserRole) == key:
                self.tree.setCurrentItem(grp_item)
                return
            # 再检查子项（key = "xxxx_part"）
            for j in range(grp_item.childCount()):
                child = grp_item.child(j)
                if child.data(0, Qt.UserRole) == key:
                    self.tree.setCurrentItem(child)
                    return

    def current_key(self) -> str | None:
        """当前选中项的 key（如 GRP:50103101 或 50103101_weapon），无选中返回 None。"""
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _apply_filter(self, text: str) -> None:
        """分类 chips + 搜索文字叠加过滤（AND）。"""
        text = text.strip().lower()
        cat = self._active_category
        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            cat_ok = (not cat) or (grp_item.data(0, Qt.UserRole + 1) or "") == cat
            grp_hit = text in grp_item.text(0).lower()
            visible_children = 0
            for j in range(grp_item.childCount()):
                child = grp_item.child(j)
                hit = cat_ok and ((not text) or grp_hit or text in child.text(0).lower())
                child.setHidden(not hit)
                visible_children += int(hit)
            grp_item.setHidden(
                (not cat_ok)
                or (bool(text) and not grp_hit and visible_children == 0)
            )
        self._refresh_count()

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
        key = item.data(0, Qt.UserRole) or ""
        if not key.startswith("GRP:"):
            return
        grp = next((g for g in self._result.groups if g.key == key[4:]), None)
        # 套装组不可改名（display_name 来自文件夹名，改文件夹名不在功能范围）
        if grp is None or grp.is_outfit:
            return
        text = item.text(0).replace("  🔴", "")
        new_name = text.split(" · ", 1)[1].strip() if " · " in text else ""
        if new_name:
            self._namemap.set_name(grp.res_id, new_name)
        self.refresh_names()

    # ---------------- 右键菜单 ----------------
    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        key = item.data(0, Qt.UserRole)
        if not key:
            return

        res_id: str | None = None
        name: str | None = None
        folder_path: str | None = None
        outfit = None

        if key.startswith("GRP:"):
            # 组头：按 key 查组；res_id / 中文名按组类型取
            grp = next((g for g in self._result.groups if g.key == key[4:]), None) if self._result else None
            if grp is None:
                return
            res_id = grp.res_id
            if grp.is_outfit:
                # 套装组：可复制父文件夹路径与全部部件 ID
                folder_path = grp.key
                outfit = grp
            elif self._namemap and grp.parts:
                name = self._namemap.lookup(grp.parts[0].name, grp.res_id)
        else:
            # 子项：key = 文件夹路径
            pd = self._parts.get(key)
            if pd is None:
                return
            res_id = pd.res_id
            folder_path = str(pd.folder)
            if self._namemap:
                name = self._namemap.lookup(pd.name, pd.res_id)

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2A2E33; border: 1px solid #3A3F46; padding: 4px; }"
            "QMenu::item { color: #E8E4D9; padding: 6px 18px; border-radius: 3px; }"
            "QMenu::item:selected { background: #3A3F46; color: #D4AF37; }"
        )
        if outfit is not None:
            all_ids = ", ".join(dict.fromkeys(p.res_id for p in outfit.parts))
            menu.addAction(f"复制全部 ID（{all_ids}）", lambda: self._copy_text(all_ids))
            if res_id:
                menu.addAction(f"复制主 ID（{res_id}）", lambda: self._copy_text(res_id))
        else:
            menu.addAction(f"复制 ID（{res_id}）", lambda: self._copy_text(res_id or ""))
            if name:
                full = f"{res_id} · {name}"
                menu.addAction(f"复制 ID · 中文名（{full}）", lambda: self._copy_text(full))
        if folder_path:
            menu.addSeparator()
            menu.addAction("复制文件夹路径", lambda: self._copy_text(folder_path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    @staticmethod
    def _copy_text(text: str) -> None:
        QGuiApplication.clipboard().setText(text)
