"""主窗口：拖拽 → 扫描 → 左栏分组列表 + 按钮矩阵 + A/B 双区。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QSettings, Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .core.namemap import NameMap, discover_map_file
from .core.scanner import IdGroup, PartData, ScanResult, scan_root
from .core.template import Template, load_templates
from .ui.anim_view import AnimView
from .ui.button_matrix import ButtonMatrix
from .ui.drop_zone import DropZone
from .ui.grid_view import GridView
from .ui.part_list import PartList
from .ui.template_editor import TemplateEditorDialog

APP_STYLE = """
QMainWindow, QWidget { background: #1E2023; color: #E8E4D9; font-size: 14px; }
QComboBox, QLineEdit {
    background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;
    padding: 4px 8px; color: #E8E4D9; font-size: 14px;
}
QPushButton {
    background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;
    padding: 4px 12px; color: #E8E4D9; font-size: 14px;
}
QPushButton:hover { border-color: #D4AF37; }
QTreeWidget {
    background: #23262a; border: 1px solid #3A3F46; border-radius: 6px;
    font-size: 14px;
}
QTreeWidget::item:selected { background: rgba(212,175,55,0.15); color: #D4AF37; }
QTreeWidget::item { padding: 1px 0; }                /* 行内边距稳定 = 行高稳定 */
QSplitter::handle { background: #3A3F46; }

/* 全局 overlay 滚动条：按需出现、隐藏时不占布局空间 → 切换部件/动作时侧栏不抖动。 */
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent; border: none; margin: 0;
}
QScrollBar:vertical { width: 8px; }
QScrollBar:horizontal { height: 8px; }
QScrollBar::handle {
    background: #4A4F56; border-radius: 4px; min-height: 24px; min-width: 24px;
}
QScrollBar::handle:hover { background: #5A6068; }
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MMY-FrameQuickView")
        self.resize(1280, 800)
        self.setStyleSheet(APP_STYLE)

        self._templates: list[Template] = load_templates()
        self._tpl: Template | None = self._templates[0] if self._templates else None
        self._result: ScanResult | None = None
        self._part: PartData | None = None
        self._group: IdGroup | None = None
        self._namemap = NameMap()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_namemap_changed)
        self._settings = QSettings("MMY", "FrameQuickView")
        self._last_folder: Path | None = None      # 最近一次拖入的目录
        self._last_map_file: Path | None = None    # 最近一次成功加载的匹配表
        # 重启兜底：上次的匹配表/拖入目录如果还存在，自动恢复
        saved = self._load_saved_map_path()
        if saved is not None and saved.exists():
            self._last_map_file = saved
        saved_folder = self._settings.value("last/folder", "")
        if saved_folder and Path(saved_folder).exists():
            self._last_folder = Path(saved_folder)

        self._build_ui()
        if self._tpl:
            self.matrix.set_template(self._tpl)
            self.anim_view.set_available_dirs(set(self._tpl.directions))
        self.statusBar().showMessage("就绪 · 拖入文件夹开始")
        # 重启恢复：自动重新扫描上次拖入的目录
        if self._last_folder is not None and self._tpl is not None:
            self._on_folder_dropped(self._last_folder)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶栏：拖拽区（含 ⚙ 匹配表菜单） + 模板切换 + 编辑/新建（一行）
        top = QHBoxLayout()
        self.drop = DropZone()
        self.drop.folder_dropped.connect(self._on_folder_dropped)
        self.drop.reload_namemap_requested.connect(self._reload_namemap)
        self.drop.pick_namemap_requested.connect(self._pick_map_file)
        top.addWidget(self.drop, 1)  # 占满左侧
        top.addSpacing(8)
        top.addWidget(QLabel("模板"))
        self.tpl_combo = QComboBox()
        self.tpl_combo.addItems([t.name for t in self._templates])
        self.tpl_combo.currentIndexChanged.connect(self._on_template_changed)
        top.addWidget(self.tpl_combo)
        self.edit_btn = QPushButton("✎ 编辑")
        self.edit_btn.clicked.connect(self._on_edit_template)
        top.addWidget(self.edit_btn)
        self.new_btn = QPushButton("＋ 新建")
        self.new_btn.clicked.connect(self._on_new_template)
        top.addWidget(self.new_btn)
        root.addLayout(top)

        # 主体：左栏部件列表 | 右侧（按钮矩阵 + A/B 双区）
        self._splitter = QSplitter()
        splitter = self._splitter
        splitter.setChildrenCollapsible(False)    # 任何一侧不能被拖到 0，避免布局抖动
        splitter.setHandleWidth(4)                # 细手柄，降低视觉权重
        self.part_list = PartList()
        self.part_list.part_selected.connect(self._on_part_selected)
        self.part_list.group_selected.connect(self._on_group_selected)
        # 「📖 选择匹配表」按钮 → 打开文件选择，选完自动刷新左栏中文名
        self.part_list.pick_namemap.connect(self._pick_map_file)
        splitter.addWidget(self.part_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        self._panes = QSplitter()
        panes = self._panes
        panes.setChildrenCollapsible(False)       # A/B 区都不能折叠
        panes.setHandleWidth(4)
        self.grid_view = GridView()
        self.grid_view.frame_clicked.connect(self._on_grid_frame_clicked)
        self.anim_view = AnimView()
        # 方向/动作按钮矩阵注入 B 区画布与控制栏之间
        self.matrix = ButtonMatrix()
        self.matrix.direction_selected.connect(self._on_direction_selected)
        self.matrix.action_selected.connect(self._on_action_selected)
        # 「显向」toggle → 画布 overlay 开关；overlay 方向点击 → 等同方向按钮点击
        self.matrix.overlay_toggled.connect(self.anim_view.set_dir_overlay_enabled)
        self.anim_view.direction_overlay_clicked.connect(self._on_direction_selected)
        self.anim_view.set_matrix_widget(self.matrix)
        panes.addWidget(self.grid_view)
        panes.addWidget(self.anim_view)
        panes.setStretchFactor(0, 5)
        panes.setStretchFactor(1, 4)
        right_layout.addWidget(panes, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        # 左栏宽度锁定（PartList.setFixedWidth），这里给一致的初始比例即可。
        # 3列宽度：优先从 QSettings 恢复，否则用默认值
        # QSettings 返回 QVariantList，需显式转 list[int]（PySide6 类型严格）
        saved_splitter = self._settings.value("layout/splitter", None)
        saved_panes = self._settings.value("layout/panes", None)
        splitter.setSizes([int(x) for x in saved_splitter] if saved_splitter else [220, 1060])
        panes.setSizes([int(x) for x in saved_panes] if saved_panes else [600, 480])

        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

        # 全窗口拖拽：central widget 接收拖放，事件过滤器统一处理
        central.setAcceptDrops(True)
        central.installEventFilter(self)

    # ---------------- 行为 ----------------
    def _on_folder_dropped(self, folder: Path) -> None:
        if self._tpl is None:
            self.statusBar().showMessage("⚠ 无可用模板（templates/ 为空）")
            return
        self._last_folder = folder
        self._result = scan_root(folder, self._tpl, char_type_of=self._namemap.char_type)
        self.drop.set_current(str(folder))
        self._setup_namemap(folder)
        self.part_list.set_namemap(self._namemap)
        self.part_list.load_result(self._result)
        self._group = None
        self._part = None
        if not self._result.parts:
            self.statusBar().showMessage(
                f"ℹ 未识别到符合模板的部件文件夹（忽略 {len(self._result.ignored)} 项）"
            )

    # ---------------- 中文名映射 ----------------
    def _setup_namemap(self, folder: Path) -> None:
        """自动发现匹配表 → 加载 → 自动登记缺失 ID → 文件热更新监听。

        优先级：自动发现（拖入目录向上递归）→ 上次成功路径兜底（持久化在 QSettings）
        → 弹窗让用户选（任意目录拖入都能命中已知匹配表）。

        注意：每次 discover + saved 均失败时**仍会弹一次**，目的是解决
        「上一次的 saved 路径已失效（用户移动/删除了 txt）」的情况。
        用户取消则不再弹——只是当前部件列表没中文名，下次拖入还是会让用户选。
        """
        # 兜底 1：QSettings 持久化的匹配表路径（进程重启/内存清零后自动恢复）
        if self._last_map_file is None:
            self._last_map_file = self._load_saved_map_path()
        map_file = discover_map_file(folder, self._last_map_file)
        if map_file is None:
            # offscreen 测试环境跳过弹窗避免卡死。
            import os
            is_offscreen = os.environ.get('QT_QPA_PLATFORM', '') == 'offscreen'
            if not is_offscreen:
                # 主线程同步弹：阻塞，但只在「saved 为空 + discover 失败」时触发。
                path, _ = QFileDialog.getOpenFileName(
                    self,
                    "选择 ID-中文名匹配表（txt）",
                    "",
                    "文本文件 (*.txt);;所有文件 (*)",
                )
                if path:
                    self._reload_namemap(Path(path))
                    return
            self._namemap = NameMap()
            self.statusBar().showMessage("ℹ 未发现匹配表，可在「文件 → 选择匹配表文件…」指定")
            return
        self._namemap = NameMap()
        self._namemap.load(map_file)
        self._last_map_file = map_file
        self._save_map_path(map_file)
        # 自动登记：表里没有的 ID 追加 `ID\t（待命名）`
        new_ids = self._namemap.register_missing([p.res_id for p in self._result.parts])
        if new_ids:
            self._namemap.load(map_file)  # 重载以包含登记行（保持状态一致）
        # 热更新监听（先清旧监听）
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)
        self._watcher.addPath(str(map_file))
        msg = f"📖 匹配表: {map_file.name}"
        if new_ids:
            msg += f" · 自动登记 {len(new_ids)} 个新 ID（待命名）"
        self.statusBar().showMessage(msg)

    # ---------------- 持久化：跨重启/跨目录也认得中文 ----------------
    def _load_saved_map_path(self) -> Path | None:
        """从 QSettings 读上次成功加载的匹配表路径，用于重启后/跨目录兜底。"""
        p = self._settings.value("last_map_file", "", type=str)
        if not p:
            return None
        path = Path(p)
        return path if path.exists() else None

    def _save_map_path(self, path: Path) -> None:
        """把成功加载过的匹配表路径写入 QSettings（HKCU\\Software\\MMY\\FrameQuickView）。"""
        self._settings.setValue("last_map_file", str(path))
        self._settings.sync()

    def _on_namemap_changed(self, _path: str) -> None:
        if self._namemap.path is None or not self._namemap.path.exists():
            return
        self._namemap.load(self._namemap.path)
        self.part_list.refresh_names()
        # 部分编辑器「原子保存」（删旧建新）会让 watcher 丢失该路径，需重新挂监听；
        # 只重挂当前文件，避免误判其他被监视文件。
        if str(self._namemap.path) not in self._watcher.files():
            self._watcher.addPath(str(self._namemap.path))

    # ---------------- 手动重载 / 指定匹配表 ----------------
    def _reload_namemap(self, manual: Path | None = None) -> None:
        """菜单手动触发：重新加载匹配表并刷新左栏（热更新失效时的兜底）。"""
        if manual is not None:
            map_file = Path(manual)
        else:
            folder = self._last_folder or (self._result.root if self._result else None)
            if folder is None:
                self.statusBar().showMessage("ℹ 先拖入文件夹，再重新加载匹配表")
                return
            map_file = discover_map_file(folder, self._last_map_file)
        if map_file is None or not map_file.exists():
            self.statusBar().showMessage("ℹ 未找到匹配表，列表暂只显示 ID")
            return
        self._namemap = NameMap()
        self._namemap.load(map_file)
        self._last_map_file = map_file
        self._save_map_path(map_file)
        self.part_list.set_namemap(self._namemap)
        self.part_list.refresh_names()
        # 重新挂监听（先清旧）
        for f in self._watcher.files():
            self._watcher.removePath(f)
        self._watcher.addPath(str(map_file))
        # 只刷新中文名，不打断当前选择；角色类型判定如需更新，重新拖入文件夹即可。
        self.statusBar().showMessage(f"📖 匹配表已重载: {map_file.name}（中文名已更新）")

    def _pick_map_file(self, _: object = None) -> None:
        """文件对话框手动指定匹配表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择匹配表文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self._reload_namemap(Path(path))

    # ---------------- 全窗口拖拽 ----------------
    def eventFilter(self, obj, event) -> bool:
        """全窗口拖拽：任意子控件上拖入文件夹都能识别。

        QScrollArea/QTreeWidget 等会自行消费 drag/drop 事件，
        这里在 capture 阶段拦截（返回 True）并转发到 DropZone 的处理逻辑。
        """
        et = event.type()
        if et == QEvent.Type.DragEnter:
            from pathlib import Path as _P
            md = event.mimeData()
            if md.hasUrls():
                for url in md.urls():
                    if url.isLocalFile() and _P(url.toLocalFile()).is_dir():
                        event.acceptProposedAction()
                        return True  # 拦截，阻止子控件默认处理
        elif et == QEvent.Type.Drop:
            from pathlib import Path as _P
            md = event.mimeData()
            for url in md.urls():
                if url.isLocalFile():
                    p = _P(url.toLocalFile())
                    if p.is_dir():
                        self._on_folder_dropped(p)
                        event.acceptProposedAction()
                        return True
        return super().eventFilter(obj, event)

    # ---------------- 选择：单部件 / 同ID组 ----------------
    def _on_part_selected(self, part: PartData) -> None:
        self._part = part
        self._group = None
        self._update_matrix(None, None)
        self._after_matrix_change()

    def _on_group_selected(self, res_id: str) -> None:
        if self._result is None:
            return
        grp = next((g for g in self._result.groups if g.res_id == res_id), None)
        if grp is None:
            return
        self._group = grp
        self._part = None
        self._update_matrix(None, None)
        self._after_matrix_change()

    def _on_direction_selected(self, direction: str) -> None:
        # 切换方向时保持当前动作（该动作在新方向缺失时由 show_part/show_group 兜底）
        _, action = self.matrix.current()
        self._update_matrix(direction, action)
        self._after_matrix_change()

    def _on_action_selected(self, action: str) -> None:
        direction, _ = self.matrix.current()
        self._update_matrix(direction, action)
        self._after_matrix_change()

    def _update_matrix(self, direction: str | None, action: str | None) -> None:
        if self._group is not None:
            self.matrix.show_group(self._group, direction, action)
        elif self._part is not None:
            self.matrix.show_part(self._part, direction, action)

    def _after_matrix_change(self) -> None:
        # 同步当前方向给 overlay，使画布高亮与按钮矩阵一致
        direction, _ = self.matrix.current()
        self.anim_view.set_current_dir(direction)
        self._show_grid()
        self._show_anim()
        self._refresh_status()

    def _on_grid_frame_clicked(self, idx: int) -> None:
        """A 区点击某帧 → B 区跳转并暂停，方便逐帧对照。"""
        self.anim_view.goto_frame(idx)

    def keyPressEvent(self, event) -> None:
        """键盘导航（M5）：←/→ 循环切换方向，↑/↓ 循环切换动作。

        - 走与鼠标点击相同的信号路径，缺失组合由 show_part/show_group 兜底
        - 焦点在输入控件（过滤框/模板下拉）时不拦截，避免干扰文字编辑
        """
        tpl = self._tpl
        if tpl is not None and (self._part is not None or self._group is not None):
            fw = self.focusWidget()
            if not isinstance(fw, (QLineEdit, QComboBox)):
                key = event.key()
                if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                    direction, action = self.matrix.current()
                    dirs, acts = tpl.directions, tpl.actions
                    if key == Qt.Key_Left and direction in dirs:
                        self._on_direction_selected(dirs[(dirs.index(direction) - 1) % len(dirs)])
                        return
                    if key == Qt.Key_Right and direction in dirs:
                        self._on_direction_selected(dirs[(dirs.index(direction) + 1) % len(dirs)])
                        return
                    if key == Qt.Key_Up and action in acts:
                        self._on_action_selected(acts[(acts.index(action) - 1) % len(acts)])
                        return
                    if key == Qt.Key_Down and action in acts:
                        self._on_action_selected(acts[(acts.index(action) + 1) % len(acts)])
                        return
        super().keyPressEvent(event)

    def _current_ad(self, direction: str | None, action: str | None):
        if not direction or not action:
            return None
        if self._part is not None:
            return self._part.action_data(direction, action)
        if self._group is not None:
            # 组模式下帧数/连续性以首个有该组合的部件为准
            for p in self._group.parts:
                ad = p.action_data(direction, action)
                if ad:
                    return ad
        return None

    def _layers_for_current(self) -> list[list[Path]]:
        """按当前 (组/部件) + (方向,动作) 计算各层帧序列。"""
        layers: list[list[Path]] = []
        direction, action = self.matrix.current()
        if not direction or not action:
            return layers
        if self._part is not None:
            ad = self._part.action_data(direction, action)
            return [ad.frames] if ad else layers
        # 组模式：按 layer_rank 排序的 parts 各取 (d,a) 帧，shadow 自动最底
        for p in self._group.parts:
            ad = p.action_data(direction, action)
            layers.append(ad.frames if ad else [])
        return layers

    def _show_grid(self) -> None:
        """按当前 (组/部件) + (方向,动作) 计算各层帧序列，交给 GridView 渲染。"""
        if self._group is None and self._part is None:
            return
        self.grid_view.show_sequence(self._layers_for_current())

    def _show_anim(self) -> None:
        """B 区同步加载当前组合的动画。"""
        if self._group is None and self._part is None:
            return
        self.anim_view.show_sequence(self._layers_for_current())

    def _refresh_status(self) -> None:
        """状态栏：帧数 / 帧号连续性 / 缺漏 / 配套摘要。"""
        if self._part is None and self._group is None:
            return
        direction, action = self.matrix.current()
        segs: list[str] = []
        if self._group is not None:
            segs.append(f"组 {self._group.res_id}（{len(self._group.parts)} 层叠合 · shadow 最底）")
            if self._group.has_issues:
                segs.append("⚠ 配套异常")
        else:
            segs.append(f"部件 {self._part.name}")
        ad = self._current_ad(direction, action)
        if ad and ad.count:
            rng = f"{ad.numbers[0]:04d}–{ad.numbers[-1]:04d}"
            segs.append(f"{ad.count} 帧（{rng}）")
            segs.append("✅ 区间内帧号连续" if ad.continuous else f"⚠ 缺帧 {ad.gaps[:5]}")
        elif direction and action:
            segs.append("当前组合无资源")
        if self._part is not None:
            if self._part.missing_directions:
                segs.append("⚠ 缺方向: " + ", ".join(self._part.missing_directions))
            if self._part.missing_actions:
                segs.append("⚠ 缺动作 " + self._missing_actions_text(self._part.missing_actions))
        elif self._group is not None:
            if self._group.missing_directions:
                segs.append("⚠ 缺方向: " + ", ".join(self._group.missing_directions))
            if self._group.missing_actions:
                segs.append("⚠ 缺动作 " + self._missing_actions_text(self._group.missing_actions))
            if self._group.pairing_issues:
                segs.append("⚠ 配套: " + "；".join(self._group.pairing_issues[:2]))
        self.statusBar().showMessage("　·　".join(segs))

    def _missing_actions_text(self, missing: dict[str, list[str]]) -> str:
        """按方向顺序拼接 `方向: 缺动作…`；方向顺序优先取模板 directions。"""
        order = [d for d in (self._tpl.directions if self._tpl else []) if d in missing] \
            or sorted(missing.keys())
        return "；".join(f"{d}: " + ", ".join(missing[d]) for d in order)

    def _on_template_changed(self, index: int) -> None:
        if 0 <= index < len(self._templates):
            self._tpl = self._templates[index]
            self.matrix.set_template(self._tpl)
            self.anim_view.set_available_dirs(set(self._tpl.directions))
            if self._result and self._result.root:
                self._on_folder_dropped(self._result.root)

    def _on_edit_template(self) -> None:
        if self._tpl is None:
            QMessageBox.information(self, "编辑模板", "当前没有可编辑的模板，请先新建。")
            return
        if TemplateEditorDialog.edit(self._tpl, self):
            self._reload_templates()

    def _on_new_template(self) -> None:
        if TemplateEditorDialog.create_new(self):
            self._reload_templates()

    def _reload_templates(self) -> None:
        """模板保存后重新加载列表，并刷新当前扫描。"""
        self._templates = load_templates()
        self.tpl_combo.blockSignals(True)
        self.tpl_combo.clear()
        self.tpl_combo.addItems([t.name for t in self._templates])
        # 尽量保持当前模板选中
        names = [t.name for t in self._templates]
        if self._tpl and self._tpl.name in names:
            self.tpl_combo.setCurrentIndex(names.index(self._tpl.name))
        elif self._templates:
            self.tpl_combo.setCurrentIndex(0)
        self.tpl_combo.blockSignals(False)
        self._tpl = self._templates[self.tpl_combo.currentIndex()] if self._templates else None
        if self._tpl:
            self.matrix.set_template(self._tpl)
        if self._result and self._result.root and self._tpl:
            self._on_folder_dropped(self._result.root)

    def closeEvent(self, event) -> None:
        """关闭时保存布局宽度 + 最近拖入目录到 QSettings。"""
        self._settings.setValue("layout/splitter", self._splitter.sizes())
        self._settings.setValue("layout/panes", self._panes.sizes())
        if self._last_folder is not None:
            self._settings.setValue("last/folder", str(self._last_folder))
        super().closeEvent(event)
