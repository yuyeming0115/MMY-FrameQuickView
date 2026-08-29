"""主窗口：拖拽 → 扫描 → 左栏分组列表 + 按钮矩阵 + A/B 双区。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QSettings, Qt, QTimer
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
        # M24：资源文件夹变更监听（外部增删文件 → 防抖后自动重扫）
        self._dir_watcher = QFileSystemWatcher(self)
        self._dir_watcher.directoryChanged.connect(self._on_tree_changed)
        self._rescan_timer = QTimer(self)               # 防抖：静默 1s 后重扫一次
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.timeout.connect(self._auto_rescan)
        self._settings = QSettings("MMY", "FrameQuickView")
        self._auto_refresh = bool(self._settings.value("checks/auto_refresh", True, type=bool))
        self._last_folder: Path | None = None      # 最近一次拖入的目录
        self._last_map_file: Path | None = None    # 最近一次成功加载的匹配表
        # 组视图右侧 toggle 中用户隐藏的部件层（QSettings 记忆）
        self._hidden_parts: set[str] = set(self._settings.value("layering/hidden_parts", [], type=list))
        # M26：全局特效库（所有 flat 特效，无论物理位置）与套装穿戴选择
        self._fx_library: list[PartData] = []
        self._fx_by_key: dict[str, PartData] = {}
        self._dressed_fx: dict[str, str] = self._load_dressed_fx()
        # M28：全局翅膀库（所有 wings 部件，无论物理位置）与套装穿戴选择
        self._wing_library: list[PartData] = []
        self._wing_by_key: dict[str, PartData] = {}
        self._dressed_wings: dict[str, str] = self._load_dressed_wings()
        # M28：真正的特效层下标（穿戴的翅膀也算 flat 层，但不需要微调 → 排除）
        self._fx_layer_indices: set[int] = set()
        # fills 警告检测开关：NPC/翅膀/主角/坐骑等无 fills 部件的资源可关闭降噪（QSettings 记忆）
        self._fills_check = bool(self._settings.value("checks/fills", True, type=bool))
        # 重启兜底：上次的匹配表/拖入目录如果还存在，自动恢复
        saved = self._load_saved_map_path()
        if saved is not None and saved.exists():
            self._last_map_file = saved
        saved_folder = self._settings.value("last/folder", "")
        if saved_folder and Path(saved_folder).exists():
            self._last_folder = Path(saved_folder)

        self._build_ui()
        self.part_list.set_fills_check(self._fills_check)   # 启动时同步左栏橙点开关
        self.part_list.set_template(self._tpl)              # 分类 chips 顺序来源
        if self._tpl:
            self.matrix.set_template(self._tpl)
            self.anim_view.set_available_dirs(set(self._tpl.directions))
        self.statusBar().showMessage("就绪 · 拖入文件夹开始")
        # 恢复 A区「原图/自适应」模式
        saved_fit = self._settings.value("grid/fit_mode", False)
        if saved_fit is not None and bool(saved_fit) != self.grid_view._mode_btn.isChecked():
            self.grid_view._mode_btn.toggle()
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
        self.drop.auto_refresh_act.setChecked(self._auto_refresh)
        self.drop.auto_refresh_toggled.connect(self._on_auto_refresh_toggled)
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
        # fills 警告检测开关：无 fills 部件的资源（NPC/翅膀/主角/坐骑等）可关闭降噪
        self.fills_btn = QPushButton("🟠 fills 检测")
        self.fills_btn.setCheckable(True)
        self.fills_btn.setChecked(self._fills_check)
        self.fills_btn.setToolTip(
            "fills 部件缺漏警示开关：\n"
            "开启 = 缺 fills 时左栏橙点 + 状态栏橙色提示\n"
            "关闭 = 不再提示（无 fills 部件的资源建议关闭）"
        )
        self.fills_btn.setStyleSheet(
            "QPushButton:checked { color: #E8A33D; border-color: #E8A33D; }"
        )
        self.fills_btn.toggled.connect(self._on_fills_check_toggled)
        top.addWidget(self.fills_btn)
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
        self.anim_view.part_toggles_signal().connect(self._on_part_toggled)
        self.anim_view.fx_offset_changed.connect(self._on_fx_offset_changed)
        self.anim_view.fx_dressed_signal().connect(self._on_fx_dressed)  # M26 穿戴特效
        self.anim_view.wing_dressed_signal().connect(self._on_wing_dressed)  # M28 穿戴翅膀
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
        # M26：全局特效库——所有 flat 特效，无论物理位置（套装目录内 / 最外层
        # 独立文件夹），供任意套装穿戴，无需复制到每个套装目录。
        self._fx_library = list(self._result.fx_library)
        self._fx_by_key = {p.name: p for p in self._fx_library}
        # M28：全局翅膀库——所有 wings 部件，无论物理位置，供任意套装穿戴
        self._wing_library = list(self._result.wing_library)
        self._wing_by_key = {p.name: p for p in self._wing_library}
        self.drop.set_current(str(folder))
        self._setup_namemap(folder)
        self.part_list.set_namemap(self._namemap)
        self.part_list.load_result(self._result)
        # 恢复上次选中的资源项（优先用 QSettings 记忆，否则默认第一项）
        saved_sel = self._settings.value("last/selection", "")
        if saved_sel:
            self.part_list._select_by_key(saved_sel)
        self._group = None
        self._part = None
        self._hidden_parts = set(self._settings.value("layering/hidden_parts", [], type=list))
        if not self._result.parts:
            self.statusBar().showMessage(
                f"ℹ 未识别到符合模板的部件文件夹（忽略 {len(self._result.ignored)} 项）"
            )
        self._setup_dir_watcher(folder)

    # ---------------- M24：文件夹变更自动刷新 ----------------
    # watch 上限：超过则降级为「root + 套装父目录 + 部件目录」级监听
    #（部件增删可感知，帧级变更不感知），避免大库（万级目录）耗尽句柄。
    WATCH_LIMIT = 2000

    def _collect_watch_dirs(self, root: Path) -> list[Path]:
        """递归收集 root 下全部子目录（含 root）；超上限降级为部件级。"""
        all_dirs: list[Path] = [root]
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                for c in d.iterdir():
                    if c.is_dir():
                        all_dirs.append(c)
                        stack.append(c)
            except (PermissionError, OSError):
                continue
            if len(all_dirs) > self.WATCH_LIMIT:
                break
        if len(all_dirs) <= self.WATCH_LIMIT:
            return all_dirs
        # 降级：root + 已识别部件所在目录链（父目录变化能感知部件增删）
        keep = {root}
        if self._result is not None:
            for p in self._result.parts:
                keep.add(p.folder)
                keep.add(p.folder.parent)
        return sorted(keep)

    def _setup_dir_watcher(self, folder: Path) -> None:
        """扫描后（重）建目录监听：新增的目录也要纳入（如新导出的方向/动作）。"""
        if not self._auto_refresh or not folder.is_dir():
            return
        old = self._dir_watcher.directories()
        if old:
            self._dir_watcher.removePaths(old)
        dirs = [str(d) for d in self._collect_watch_dirs(folder)]
        failed = self._dir_watcher.addPaths(dirs)
        if failed:                                   # 竞态中已删除的目录：清掉避免警告
            self._dir_watcher.removePaths(failed)

    def _on_tree_changed(self, _path: str) -> None:
        if not self._auto_refresh or self._result is None:
            return
        if not self._result.root.is_dir():
            return                                  # 拖入目录整体被删：等用户重新拖入
        self._rescan_timer.start()                  # 防抖：静默 1s 后重扫

    def _auto_rescan(self) -> None:
        """自动重扫：保持选中项 / 方向 / 动作 / B 区播放状态。"""
        if self._result is None or not self._result.root.is_dir():
            return
        key = self.part_list.current_key()
        direction, action = self.matrix.current()
        paused = not self.anim_view._play_btn.isChecked()
        idx = self.anim_view._index
        root = self._result.root
        self._on_folder_dropped(root)
        if key:
            self.part_list._select_by_key(key)      # 已删除则保持默认第一项
        if self._part is not None or self._group is not None:
            self._update_matrix(direction, action)  # 新数据缺失时由矩阵兜底
            if paused:
                self.anim_view.set_resume_state(True, idx)
            self._after_matrix_change()
        self.statusBar().showMessage("📡 检测到文件变更，已自动刷新")

    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        """自动刷新开关（QSettings 记忆）；关闭时移除全部目录监听。"""
        self._auto_refresh = checked
        self._settings.setValue("checks/auto_refresh", checked)
        self._settings.sync()
        if not checked:
            self._rescan_timer.stop()
            old = self._dir_watcher.directories()
            if old:
                self._dir_watcher.removePaths(old)
        elif self._result is not None:
            self._setup_dir_watcher(self._result.root)
            self.statusBar().showMessage("📡 已开启自动刷新（检测外部文件变更）")

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
        self.anim_view.hide_part_toggles()
        self._update_matrix(None, None)
        self._after_matrix_change()

    def _on_group_selected(self, key: str) -> None:
        if self._result is None:
            return
        grp = next((g for g in self._result.groups if g.key == key), None)
        if grp is None:
            return
        self._group = grp
        self._part = None
        # 右侧逐部件显隐 toggle（按 layer_order 从底到顶给中文名）；
        # 套装组内重名部件（如两个 shadow）用 `部件·ID` 区分 key
        parts = {self._toggle_key(p, grp): self._toggle_label(p, grp)
                 for p in grp.parts}
        self.anim_view.show_part_toggles(parts, self._hidden_parts)
        # M26：注入全局特效库下拉框，并恢复该套装上次穿戴的特效
        self.anim_view.set_fx_library(
            [(p.name, self._fx_display_name(p)) for p in self._fx_library],
            self._dressed_fx.get(grp.key, ""),
        )
        # M28：注入全局翅膀库下拉框，并恢复该套装上次穿戴的翅膀
        self.anim_view.set_wing_library(
            [(p.name, self._wing_display_name(p)) for p in self._wing_library],
            self._dressed_wings.get(grp.key, ""),
        )
        self._update_matrix(None, None)
        self._after_matrix_change()

    def _toggle_key(self, p, grp) -> str:
        """显隐 toggle 的 key：部件名；组内重名时追加 `·ID`（与 _layers_for_current 一致）。"""
        base = p.part or p.name
        same = [q for q in grp.parts if (q.part or q.name) == base]
        return base if len(same) == 1 else f"{base}·{p.res_id}"

    def _toggle_label(self, p, grp) -> str:
        base = p.part or p.name
        key = self._toggle_key(p, grp)
        if p.is_flat:
            # 特效层：显示匹配表中文名（如 游龙「环绕特效」），无映射兜底「特效」
            if self._namemap is not None:
                cn = self._namemap.lookup(p.name, p.res_id) or "特效"
            else:
                cn = "特效"
            return cn
        if self._namemap is None:
            cn, owner = p.part or "", None
        else:
            sibs = [(q.res_id, q.part) for q in grp.parts]
            cn, owner = self._namemap.part_cn_in(p.part, p.res_id, sibs)
        if key == base:
            return cn or base
        # 组内重名：影子归属主体后已可区分（武器影子/身体影子），其余重名补 `·ID`
        if owner is not None:
            return cn
        return f"{cn or base}·{p.res_id}"

    def _on_part_toggled(self, part: str, visible: bool) -> None:
        if visible:
            self._hidden_parts.discard(part)
        else:
            self._hidden_parts.add(part)
        self._settings.setValue("layering/hidden_parts", sorted(self._hidden_parts))
        self._settings.sync()
        self._after_matrix_change()

    def _on_fx_dressed(self, fx_key: str) -> None:
        """M26：穿戴特效下拉框切换 → 记到当前套装并刷新预览。

        选择按套装 key 记忆（QSettings），切换套装时自动恢复；
        偏移按特效名存取，同一特效调一次所有套装复用。
        """
        if self._group is None:
            return
        self._dressed_fx[self._group.key] = fx_key
        self._save_dressed_fx(self._group.key, fx_key)
        if fx_key:
            fx = self._fx_by_key.get(fx_key)
            name = self._fx_display_name(fx) if fx is not None else fx_key
            tip = " ｜ Ctrl+方向键微调位置"
        else:
            name, tip = "无", ""
        gname = self._group.display_name or self._group.key
        self.statusBar().showMessage(f"✨ 套装「{gname}」穿戴特效：{name}{tip}", 3000)
        self._after_matrix_change()

    def _on_wing_dressed(self, wing_key: str) -> None:
        """M28：穿戴翅膀下拉框切换 → 记到当前套装并刷新预览。

        与特效同机制：选择按套装 key 记忆（QSettings），切换套装自动恢复。
        区别：翅膀按 (方向,动作) 取帧，方向动作本来就对齐 → **不提供偏移微调**。
        """
        if self._group is None:
            return
        self._dressed_wings[self._group.key] = wing_key
        self._save_dressed_wings(self._group.key, wing_key)
        if wing_key:
            wing = self._wing_by_key.get(wing_key)
            name = self._wing_display_name(wing) if wing is not None else wing_key
        else:
            name = "无"
        gname = self._group.display_name or self._group.key
        self.statusBar().showMessage(f"🕊 套装「{gname}」穿戴翅膀：{name}", 3000)
        self._after_matrix_change()

    def _on_fx_offset_changed(self, part_key: str, dx: int, dy: int) -> None:
        """特效偏移微调回调：保存到 QSettings 并刷新显示。"""
        self._set_fx_offset(part_key, dx, dy)
        self.statusBar().showMessage(
            f"✨ 特效偏移 [{part_key}] → ({dx:+d}, {dy:+d}) ｜ "
            f"提示：Ctrl+方向键微调", 3000)
        # 重新加载动画以应用新偏移（不重新解码文件，只重合成）
        self._show_anim()

    def _on_fills_check_toggled(self, checked: bool) -> None:
        """fills 警告检测开关：即时刷新左栏橙点与状态栏（QSettings 记忆）。"""
        self._fills_check = checked
        self._settings.setValue("checks/fills", checked)
        self._settings.sync()
        self.part_list.set_fills_check(checked)
        self._refresh_status()

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

    def _layers_for_current(self) -> tuple[
        list[list[Path]], list[bool], dict[int, tuple[int, int]], list[str]
    ]:
        """按当前 (组/部件) + (方向,动作) 算各层帧序列 + 特效 mask + 偏移 + part keys。

        返回 (layers, flat_mask, fx_offsets, part_keys)：
        - flat_mask[i]=True 表示第 i 层是特效层
        - fx_offsets[i] = (dx, dy) 用户微调偏移（仅特效层有值）
        - part_keys[i] = 第 i 层 key（与 layers 严格同序，偏移微调靠它定位）

        M26：组模式把「穿戴」的全局特效追加为顶层 flat 层。part_keys 在此一并
        产出，避免调用方与合成链各算一份导致顺序错位（微调会作用到错误的层）。
        """
        layers: list[list[Path]] = []
        flat_mask: list[bool] = []
        fx_offsets: dict[int, tuple[int, int]] = {}
        part_keys: list[str] = []
        direction, action = self.matrix.current()
        if not direction or not action:
            return layers, flat_mask, fx_offsets, part_keys
        if self._part is not None:
            ad = self._part.action_data(direction, action)
            if ad:
                layers = [ad.frames]
                flat_mask = [self._part.is_flat]
                part_keys = [self._part.name]
                if self._part.is_flat:
                    fx_offsets = {0: self._get_fx_offset(self._part.name)}
            # M28：单部件视图下，特效自身就是可微调层
            self._fx_layer_indices = {0} if self._part.is_flat else set()
            return layers, flat_mask, fx_offsets, part_keys
        # 组模式：按 layer_rank 排序的 parts 各取 (d,a) 帧
        for p in self._group.parts:
            key = self._toggle_key(p, self._group)
            if key in self._hidden_parts:
                continue
            ad = p.action_data(direction, action)
            if ad is None and p.is_flat:
                ad = next((a for col in p.matrix.values() for a in col.values()), None)
            idx = len(layers)
            layers.append(ad.frames if ad else [])
            flat_mask.append(p.is_flat)
            part_keys.append(key)
            if p.is_flat:
                fx_offsets[idx] = self._get_fx_offset(key)
        # M28：记录「真正的特效层」下标（组内 flat 部件）——穿戴的翅膀虽也是
        # flat 层，但按 (方向,动作) 取帧、对齐本就准确，不参与 Ctrl+方向键微调。
        self._fx_layer_indices = {i for i, f in enumerate(flat_mask) if f}
        # M28：先追加穿戴的翅膀（在角色之上）→ 再追加穿戴特效（置顶最上层）
        self._append_dressed_wings(layers, flat_mask, fx_offsets, part_keys)
        idx = self._append_dressed_fx(layers, flat_mask, fx_offsets, part_keys)
        if idx is not None:
            self._fx_layer_indices.add(idx)   # 穿戴的特效可微调
        return layers, flat_mask, fx_offsets, part_keys

    def _append_dressed_fx(
        self,
        layers: list[list[Path]],
        flat_mask: list[bool],
        fx_offsets: dict[int, tuple[int, int]],
        part_keys: list[str],
    ) -> int | None:
        """M26：把当前套装「穿戴」的特效追加为顶层 flat 层（原地修改传入列表）。"""
        if self._group is None:
            return None
        return self._append_dressed_part(
            layers, flat_mask, fx_offsets, part_keys,
            self._dressed_fx.get(self._group.key, ""), self._fx_by_key,
        )

    def _append_dressed_wings(
        self,
        layers: list[list[Path]],
        flat_mask: list[bool],
        fx_offsets: dict[int, tuple[int, int]],
        part_keys: list[str],
    ) -> int | None:
        """M28：把当前套装「穿戴」的翅膀追加为顶层 flat 层（原地修改传入列表）。

        与特效同机制，但翅膀是**常规资源**（有方向/动作层级）→ 按当前
        (方向,动作) 取帧，因此方向动作天然对齐，**不提供偏移微调**。
        """
        if self._group is None:
            return None
        return self._append_dressed_part(
            layers, flat_mask, fx_offsets, part_keys,
            self._dressed_wings.get(self._group.key, ""), self._wing_by_key,
        )

    def _append_dressed_part(
        self,
        layers: list[list[Path]],
        flat_mask: list[bool],
        fx_offsets: dict[int, tuple[int, int]],
        part_keys: list[str],
        dress_key: str,
        by_key: dict[str, PartData],
    ) -> int | None:
        """M26/M28：把「穿戴」的全局资源追加为顶层 flat 层（原地修改传入列表）。

        - key 取自 dressed 映射[套装key]；为空或找不到 → 不追加
        - 该资源若已在本组内（旧目录结构自动并入）→ 跳过，避免重复叠加
        - **取帧方式**：flat 资源（特效）取自身序列；常规资源（翅膀）按当前
          (方向,动作) 取帧，缺失时退回该资源的第一个可用组合
        - 一律 flat_mask=True：穿戴的是独立资源，需按 bbox 与角色居中对齐
        - 偏移按**资源名**存取 → 同一资源调一次偏移，所有套装复用
        - 返回追加到的层下标（未追加返回 None），供调用方标记可微调的特效层
        """
        if not dress_key:
            return None
        p = by_key.get(dress_key)
        if p is None:
            return None
        if any(q.name == p.name for q in self._group.parts):
            return None                      # 已在组内，不重复叠加
        direction, action = self.matrix.current()
        if p.is_flat:
            ad = next((a for col in p.matrix.values() for a in col.values()), None)
        else:
            ad = p.action_data(direction, action)
            if ad is None:
                ad = next((a for col in p.matrix.values() for a in col.values()), None)
        if ad is None or not ad.frames:
            return None
        idx = len(layers)
        layers.append(ad.frames)
        flat_mask.append(True)
        part_keys.append(p.name)
        fx_offsets[idx] = self._get_fx_offset(p.name)
        return idx

    def _load_dressed_fx(self) -> dict[str, str]:
        """M26：从 QSettings 读取各套装的穿戴特效选择 {套装key: 特效key}。"""
        self._settings.beginGroup("layering/dressed_fx")
        out = {k: self._settings.value(k, "", type=str)
               for k in self._settings.childKeys()}
        self._settings.endGroup()
        return out

    def _save_dressed_fx(self, group_key: str, fx_key: str) -> None:
        """M26：保存某套装的穿戴特效选择；空串表示不穿戴。"""
        self._settings.beginGroup("layering/dressed_fx")
        self._settings.setValue(group_key, fx_key)
        self._settings.endGroup()
        self._settings.sync()

    def _fx_display_name(self, p) -> str:
        """M26：特效在穿戴下拉框里的显示名（匹配表中文名，无映射兜底文件夹名）。"""
        if self._namemap is not None:
            cn = self._namemap.lookup(p.name, p.res_id)
            if cn:
                return cn
        return p.name

    def _wing_display_name(self, p) -> str:
        """M28：翅膀在穿戴下拉框里的显示名（中文名·翅膀，无映射兜底文件夹名）。"""
        cn = self._namemap.lookup(p.name, p.res_id) if self._namemap is not None else None
        return f"{cn}·翅膀" if cn else p.name

    def _load_dressed_wings(self) -> dict[str, str]:
        """M28：从 QSettings 读取各套装的穿戴翅膀选择 {套装key: 翅膀key}。"""
        self._settings.beginGroup("layering/dressed_wings")
        out = {k: self._settings.value(k, "", type=str)
               for k in self._settings.childKeys()}
        self._settings.endGroup()
        return out

    def _save_dressed_wings(self, group_key: str, wing_key: str) -> None:
        """M28：保存某套装的穿戴翅膀选择；空串表示不穿戴。"""
        self._settings.beginGroup("layering/dressed_wings")
        self._settings.setValue(group_key, wing_key)
        self._settings.endGroup()
        self._settings.sync()

    def _get_fx_offset(self, key: str) -> tuple[int, int]:
        """从 QSettings 读取某部件的特效偏移量，默认 (0,0)。"""
        val = self._settings.value(f"layering/fx_offset/{key}", "0,0", type=str)
        try:
            dx, dy = val.split(",")
            return int(dx.strip()), int(dy.strip())
        except (ValueError, AttributeError):
            return 0, 0

    def _set_fx_offset(self, key: str, dx: int, dy: int) -> None:
        """保存特效偏移量到 QSettings。"""
        self._settings.setValue(f"layering/fx_offset/{key}", f"{dx},{dy}")
        self._settings.sync()

    def _show_grid(self) -> None:
        if self._group is None and self._part is None:
            return
        layers, flat_mask, fx_offsets, _keys = self._layers_for_current()
        self.grid_view.show_sequence(layers, flat_mask, fx_offsets)

    def _show_anim(self) -> None:
        """B 区同步加载当前组合的动画。"""
        if self._group is None and self._part is None:
            return
        # M26：part_keys 由 _layers_for_current 一并产出，天然与 layers 同序
        layers, flat_mask, fx_offsets, part_keys = self._layers_for_current()
        self.anim_view.set_fx_part_keys(part_keys)
        # M28：告知画布哪些层是真特效（Ctrl+方向键微调目标），穿戴翅膀会被排除
        self.anim_view.set_fx_layer_indices(self._fx_layer_indices)
        self.anim_view.show_sequence(layers, flat_mask, fx_offsets)

    def _refresh_status(self) -> None:
        """状态栏：帧数 / 帧号连续性 / 缺漏 / 配套摘要。"""
        if self._part is None and self._group is None:
            return
        direction, action = self.matrix.current()
        segs: list[str] = []
        if self._group is not None:
            gname = self._group.display_name or self._group.res_id
            segs.append(f"组 {gname}（{len(self._group.parts)} 层叠合 · shadow 最底）")
            fxs = [p for p in self._group.parts if p.is_flat]
            if fxs:
                fx_ad = next((a for p in fxs for col in p.matrix.values() for a in col.values()), None)
                if fx_ad:
                    segs.append(f"✨ 特效层 {fx_ad.count} 帧·置顶")
            if self._group.has_issues:
                segs.append("⚠ 配套异常")
        else:
            segs.append(f"部件 {self._part.name}")
        ad = self._current_ad(direction, action)
        if ad and ad.count:
            is_flat = (self._part is not None and self._part.is_flat) or \
                      (self._group is not None and self._group.is_flat)
            if is_flat:
                rng = f"{ad.numbers[0]}–{ad.numbers[-1]}"       # 特效帧号不补零
            else:
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
            if self._part.extra_actions:
                segs.append("⚠ 多余动作 " + self._extra_actions_text(self._part.extra_actions))
            if self._fills_check and self._part.has_warnings:
                segs.append("🟠 " + self._warnings_text())
        elif self._group is not None:
            if self._group.missing_directions:
                segs.append("⚠ 缺方向: " + ", ".join(self._group.missing_directions))
            if self._group.missing_actions:
                segs.append("⚠ 缺动作 " + self._missing_actions_text(self._group.missing_actions))
            if self._group.extra_actions:
                segs.append("⚠ 多余动作 " + self._extra_actions_text(self._group.extra_actions))
            if self._group.pairing_issues:
                segs.append("⚠ 配套: " + "；".join(self._group.pairing_issues[:2]))
            if self._fills_check and self._group.has_warnings:
                segs.append("🟠 " + self._warnings_text())
        self.statusBar().showMessage("　·　".join(segs))

    def _warnings_text(self) -> str:
        """组级/部件级警示摘要（橙色，目前为 fills 缺失）。"""
        grp, part = self._group, self._part
        bits: list[str] = []
        if grp is not None:
            if grp.missing_fills:
                bits.append(f"缺 fills 部件（{', '.join(grp.missing_fills)}）")
            if grp.fills_warning_directions:
                bits.append("fills 缺方向: " + ", ".join(grp.fills_warning_directions))
            if grp.fills_warning_actions:
                bits.append("fills 缺动作 " + self._missing_actions_text(grp.fills_warning_actions))
            filled_parts = [p for p in grp.parts if p.has_warnings]
            for p in filled_parts:
                details = []
                if p.warning_directions:
                    details.append("方向 " + ", ".join(p.warning_directions))
                if p.warning_actions:
                    details.append("动作 " + self._missing_actions_text(p.warning_actions))
                if details:
                    bits.append("fills 缺 " + " / ".join(details))
        elif part is not None:
            if part.warning_directions:
                bits.append(f"fills 缺方向: {', '.join(part.warning_directions)}")
            if part.warning_actions:
                bits.append("fills 缺动作 " + self._missing_actions_text(part.warning_actions))
        return "；".join(bits)

    def _missing_actions_text(self, missing: dict[str, list[str]]) -> str:
        """按方向顺序拼接 `方向: 缺动作…`；方向顺序优先取模板 directions。"""
        order = [d for d in (self._tpl.directions if self._tpl else []) if d in missing] \
            or sorted(missing.keys())
        return "；".join(f"{d}: " + ", ".join(missing[d]) for d in order)

    def _extra_actions_text(self, extra: dict[str, list[str]]) -> str:
        """按方向顺序拼接 `方向: 多余动作…`；方向顺序优先取模板 directions。"""
        order = [d for d in (self._tpl.directions if self._tpl else []) if d in extra] \
            or sorted(extra.keys())
        return "；".join(f"{d}: " + ", ".join(extra[d]) for d in order)

    def _on_template_changed(self, index: int) -> None:
        if 0 <= index < len(self._templates):
            self._tpl = self._templates[index]
            self.matrix.set_template(self._tpl)
            self.part_list.set_template(self._tpl)
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
            self.part_list.set_template(self._tpl)
        if self._result and self._result.root and self._tpl:
            self._on_folder_dropped(self._result.root)

    def closeEvent(self, event) -> None:
        """关闭时保存布局宽度 + 最近拖入目录 + 选中项 + A区模式到 QSettings。"""
        self._settings.setValue("layout/splitter", self._splitter.sizes())
        self._settings.setValue("layout/panes", self._panes.sizes())
        if self._last_folder is not None:
            self._settings.setValue("last/folder", str(self._last_folder))
        sel = self.part_list.current_key()
        if sel:
            self._settings.setValue("last/selection", sel)
        # A区「原图/自适应」模式
        self._settings.setValue("grid/fit_mode", self.grid_view._mode_btn.isChecked())
        super().closeEvent(event)
