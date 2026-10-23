"""拖拽区：接收文件夹拖入 + 匹配表菜单入口 + 快捷文件夹 chips（M30/M41）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget,
)

# 快捷 chip 样式：普通(最近) / 常驻(金色) / 激活态(当前目录)
_CHIP_STYLE = (
    "QToolButton { padding: 2px 10px; border: 1px solid #3A3F46; border-radius: 10px;"
    " background: #2A2E33; color: #E8E4D9; font-size: 14px; }"
    "QToolButton:hover { border-color: #D4AF37; }"
)
_CHIP_PIN_STYLE = _CHIP_STYLE.replace(
    "color: #E8E4D9;", "color: #D4AF37;"
).replace(
    "border: 1px solid #3A3F46;", "border: 1px solid rgba(212,175,55,0.5);"
)
# 激活态（当前浏览中的目录）：金边 + 金字 + 淡金底，比常驻态更醒目
_CHIP_ACTIVE_STYLE = (
    "QToolButton { padding: 2px 10px; border: 1px solid #D4AF37; border-radius: 10px;"
    " background: rgba(212,175,55,0.18); color: #D4AF37; font-size: 14px; font-weight: bold; }"
    "QToolButton:hover { background: rgba(212,175,55,0.32); }"
)
# 「📌 常驻」开关按钮：未常驻灰、常驻金底深字（一眼分辨）
_PIN_BTN_STYLE = (
    "QToolButton { color: #96A1AD; font-size: 13px; padding: 2px 9px;"
    " border: 1px solid #3A3F46; border-radius: 10px; background: #2A2E33; }"
    "QToolButton:hover { border-color: #D4AF37; color: #D4AF37; }"
    "QToolButton:checked { color: #1E2023; background: #D4AF37; border-color: #D4AF37;"
    " font-weight: 600; }"
    "QToolButton:disabled { color: #4A4F56; border-color: #2E3238; background: transparent; }"
)


class DropZone(QFrame):
    folder_dropped = Signal(Path)
    reload_namemap_requested = Signal()   # 重新加载匹配表
    pick_namemap_requested = Signal()     # 选择匹配表文件…
    auto_refresh_toggled = Signal(bool)   # 自动刷新（外部变更）开关
    # M30 快捷文件夹 chips
    quick_folder_clicked = Signal(Path)             # 点击 chip → 切换到该目录
    quick_folder_pin_toggled = Signal(Path, bool)   # 📌 常驻 / 取消常驻（M41）
    quick_folder_removed = Signal(Path)             # 从快捷列表移除
    pin_current_toggled = Signal(Path, bool)        # 顶栏「📌 常驻」按钮（当前目录）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self._current_folder: Path | None = None   # M31：当前浏览中的目录（chip 激活态）
        self._current_pinned = False               # M41：当前目录是否常驻
        self._label = QLabel("⬇ 拖入部件文件夹 / 父级目录（任意位置均可拖入）")
        self._label.setObjectName("dropHint")

        first_row = QHBoxLayout()
        first_row.setContentsMargins(0, 0, 0, 0)
        first_row.setSpacing(6)
        first_row.addWidget(self._label)
        first_row.addStretch(1)

        # M41：「📌 常驻」开关——把当前目录设为常驻，永不被新的历史记录挤掉
        self._pin_btn = QToolButton()
        self._pin_btn.setText("📌 常驻")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setEnabled(False)            # 未加载目录时不可用
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setStyleSheet(_PIN_BTN_STYLE)
        self._pin_btn.setToolTip(
            "把当前文件夹设为「常驻」：\n"
            "· 排在快捷列表最前，永远不会被新的历史记录挤掉\n"
            "· 再次点击取消常驻，回到最近列表\n"
            "（也可以右键任意 chip →「📌 设为常驻」）"
        )
        self._pin_btn.clicked.connect(self._on_pin_btn_clicked)
        first_row.addWidget(self._pin_btn)

        # ⚙ 菜单按钮：合并原「文件」菜单的两项功能
        self._menu_btn = QToolButton()
        self._menu_btn.setText("⚙ ID")
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setStyleSheet(
            "QToolButton { color: #96A1AD; font-size: 16px; padding: 2px 6px;"
            " border: none; background: transparent; }"
            "QToolButton:hover { color: #E8E4D9; }"
            "QToolButton::menu-indicator { image: none; }"
        )
        menu = QMenu(self._menu_btn)
        menu.setStyleSheet(
            "QMenu { background: #2A2E33; color: #E8E4D9; border: 1px solid #3A3F46; }"
            "QMenu::item:selected { background: #D4AF37; color: #1E2023; }"
        )
        act_reload = menu.addAction("🔄 重新加载匹配表")
        act_reload.triggered.connect(self.reload_namemap_requested.emit)
        act_pick = menu.addAction("📁 选择匹配表文件…")
        act_pick.triggered.connect(self.pick_namemap_requested.emit)
        menu.addSeparator()
        self.auto_refresh_act = menu.addAction("📡 自动刷新（检测外部文件变更）")
        self.auto_refresh_act.setCheckable(True)
        self.auto_refresh_act.setChecked(True)
        self.auto_refresh_act.setToolTip(
            "开启后，外部新增/删除文件约 1 秒后自动重扫并保持当前选择与播放状态"
        )
        self.auto_refresh_act.toggled.connect(self.auto_refresh_toggled.emit)
        self._menu_btn.setMenu(menu)
        first_row.addWidget(self._menu_btn)

        # M30：快捷文件夹 chips 行（无内容时隐藏，不占布局空间）
        self._chips_widget = QWidget(self)
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(0, 3, 0, 0)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch(1)
        self._chips_widget.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)
        outer.addLayout(first_row)
        outer.addWidget(self._chips_widget)

        self._label.setStyleSheet("color: #96A1AD; font-size: 14px;")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #3A3F46; border-radius: 6px;"
            " background: rgba(255,255,255,0.02); }"
        )

    # ---------------- M30/M41：快捷文件夹 chips ----------------
    def set_quick_folders(self, pinned: list[Path], recents: list[Path]) -> None:
        """重建 chips 行：常驻（📌金框）在前，最近在后；当前目录 chip 金色高亮。

        每个 chip：左键点击切换目录；右键菜单 = 常驻/取消常驻、移除。
        """
        while self._chips_layout.count():              # 全清（含 stretch），下面重建
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for p in pinned:
            self._chips_layout.addWidget(self._make_chip(p, pinned=True))
        for p in recents:
            self._chips_layout.addWidget(self._make_chip(p, pinned=False))
        self._chips_layout.addStretch(1)
        has_any = bool(pinned or recents)
        self._chips_widget.setVisible(has_any)

    def set_current_folder(self, folder: Path | None) -> None:
        """更新「当前浏览中」目录并刷新 chip 激活态（不动列表内容）。"""
        if self._current_folder == folder:
            return
        self._current_folder = folder
        self._restyle_chips()

    def set_pinned(self, pinned: bool) -> None:
        """同步顶栏「📌 常驻」按钮状态（不触发信号，避免回环）。"""
        self._current_pinned = pinned
        blocked = self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(pinned)
        self._pin_btn.blockSignals(blocked)
        self._pin_btn.setEnabled(self._current_folder is not None)
        self._pin_btn.setToolTip(
            ("当前文件夹已常驻（不会被新的历史记录挤掉）\n再次点击取消常驻"
             if pinned else
             "把当前文件夹设为「常驻」：\n"
             "· 排在快捷列表最前，永远不会被新的历史记录挤掉\n"
             "· 再次点击取消常驻，回到最近列表\n"
             "（也可以右键任意 chip →「📌 设为常驻」）")
        )

    def _on_pin_btn_clicked(self) -> None:
        if self._current_folder is None:
            self.set_pinned(False)
            return
        self.pin_current_toggled.emit(self._current_folder,
                                      self._pin_btn.isChecked())

    def _restyle_chips(self) -> None:
        """按 _current_folder / 常驻标记重新套用各 chip 样式（不动列表内容）。"""
        for btn in self._chips_widget.findChildren(QToolButton):
            folder = getattr(btn, "_folder", None)
            if folder is None:
                continue
            pinned = bool(getattr(btn, "_pinned", False))
            active = (
                self._current_folder is not None
                and str(folder) == str(self._current_folder)
            )
            btn.setStyleSheet(_CHIP_ACTIVE_STYLE if active else
                              (_CHIP_PIN_STYLE if pinned else _CHIP_STYLE))

    def _make_chip(self, folder: Path, pinned: bool) -> QToolButton:
        btn = QToolButton(self._chips_widget)
        btn.setText(("📌 " if pinned else "") + folder.name)
        btn.setToolTip(("📌 常驻（不会被新历史挤掉）　" if pinned else "") + str(folder))
        btn._folder = folder
        btn._pinned = pinned
        active = (self._current_folder is not None
                  and str(folder) == str(self._current_folder))
        btn.setStyleSheet(_CHIP_ACTIVE_STYLE if active else
                          (_CHIP_PIN_STYLE if pinned else _CHIP_STYLE))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, f=folder: self.quick_folder_clicked.emit(f))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, b=btn, f=folder: self._show_chip_menu(b, f)
        )
        return btn

    def _show_chip_menu(self, btn: QToolButton, folder: Path) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2A2E33; color: #E8E4D9; border: 1px solid #3A3F46; }"
            "QMenu::item:selected { background: #D4AF37; color: #1E2023; }"
        )
        is_pinned = bool(getattr(btn, "_pinned", False))
        act_pin = menu.addAction("📌 取消常驻" if is_pinned else "📌 设为常驻（不被新历史挤掉）")
        act_pin.triggered.connect(
            lambda: self.quick_folder_pin_toggled.emit(folder, not is_pinned)
        )
        act_del = menu.addAction("🗑 从列表移除")
        act_del.triggered.connect(lambda: self.quick_folder_removed.emit(folder))
        menu.exec(btn.mapToGlobal(btn.rect().center()))

    # ---------------- 原有行为 ----------------
    def set_current(self, text: str) -> None:
        self._label.setText(f"⬇ 拖入部件文件夹 / 父级目录　·　当前: {text}")
        self._label.setStyleSheet("color: #E8E4D9; font-size: 14px;")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_dir():
                    self.folder_dropped.emit(p)
                    event.acceptProposedAction()
                    return
