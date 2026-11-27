"""主窗口：拖拽 → 扫描 → 左栏分组列表 + 按钮矩阵 + A/B 双区。"""
from __future__ import annotations

import atexit
import os
import weakref
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QSettings, Qt, QTimer, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .core.blankcheck import blank_sequences
from .core import compose      # M45：叠层配方（叠哪些层、穿戴什么）
from .core.export import default_output_dir, is_inside, safe_name   # M45：动画导出
from .core.export.batch import plan_combos, planned_paths             # M45：批量导出计划
from .core.cleanup import (
    CleanupItem,
    cleanup_target_parts,
    collect_action_cleanup_items,
    collect_extra_cleanup_items,
    move_to_trash,
)
from .core.namemap import NameMap, discover_map_file
from .core.scanner import IdGroup, PartData, ScanResult, scan_root
from .core.stats import build_hud_stats
from .core.template import Template, extract_weapon_tag, load_templates
from .ui.anim_view import AnimView
from .ui.button_matrix import ButtonMatrix
from .ui.cleanup_dialog import CleanupConfirmDialog
from .ui.drop_zone import DropZone
from .ui.export_dialog import ExportDialog      # M45：导出弹窗
from .ui.export_worker import BatchExportWorker, ExportWorker      # M45：导出后台线程
from .ui.grid_view import GridView
from .ui.inspector import INSPECTOR_DEFAULT_WIDTH, Inspector   # M44.2a：右栏检查器
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


class _BlankCheckWorker(QThread):
    """M36：空帧抽样后台线程——对 (方向,动作) 序列首帧做 alpha 检测。

    每序列仅解码首帧（≤ 约 30 次解码）；结果 (key, gen, 空序列集合) 回主线程后
    由 app 校验代数与 key 是否仍有效（过期结果直接丢弃）。
    """

    blank_ready = Signal(str, int, object)   # (选中项 key, 代数, {(direction, action)})

    def __init__(self, matrix: dict, key: str, gen: int = 0, parent=None):
        super().__init__(parent)
        self._matrix = matrix
        self._key = key
        self._gen = gen

    def run(self) -> None:
        self.blank_ready.emit(self._key, self._gen, blank_sequences(self._matrix))


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
        # M38：跨 ID 穿戴（时装/头发/武器），按当前组 key 记忆（QSettings）
        self._dressed_body: dict[str, str] = self._load_dressed_look("body")
        self._dressed_hair: dict[str, str] = self._load_dressed_look("hair")
        self._dressed_weapon: dict[str, str] = self._load_dressed_look("weapon")
        # M28：真正的特效层下标（穿戴的翅膀也算 flat 层，但不需要微调 → 排除）
        self._fx_layer_indices: set[int] = set()
        self._export_worker: ExportWorker | BatchExportWorker | None = None   # M45：正在进行的导出（同一时间只做一个）
        self._export_progress: QProgressDialog | None = None                 # M45：批量导出的进度条
        # fills 警告检测开关（M37 起默认关：BOSS/主角等无 fills 部件的资源满屏 🟠 降噪；
        # QSettings 记忆，用户开过则保持其选择）
        self._fills_check = bool(self._settings.value("checks/fills", False, type=bool))
        # M34：固定画布对齐模式（跨视图角色位置不漂移；QSettings 记忆）
        # M39 起默认开：部件/穿戴均按原始渲染画布对齐，开启是更符合直觉的基准
        self._canvas_align = bool(self._settings.value("display/canvas_align", True, type=bool))
        # M36：空帧抽样缓存 {选中项key: {(方向,动作)}}（会话级内存缓存，二次选中零 IO）
        self._blank_cache: dict[str, set] = {}
        self._blank_workers: list[_BlankCheckWorker] = []
        self._blank_generation = 0
        # M30：快捷文件夹（收藏 + 最近拖入），QSettings 持久化
        self._quick_favs: list[str] = []
        self._quick_recents: list[str] = []
        # 重启兜底：上次的匹配表/拖入目录如果还存在，自动恢复
        saved = self._load_saved_map_path()
        if saved is not None and saved.exists():
            self._last_map_file = saved
        saved_folder = self._settings.value("last/folder", "")
        if saved_folder and Path(saved_folder).exists():
            self._last_folder = Path(saved_folder)

        self._build_ui()
        # M44.2a 兜底：程序退出时一定停掉所有后台线程。
        # closeEvent 里已经会停，但如果窗口没被 close()（很多测试脚本、或程序被直接结束），
        # Python 退出时线程还在跑，Qt 会报「QThread: Destroyed while thread is still running」
        # 并强制崩溃（测试断言全过却显示失败，偶发）。atexit 会在 Python 退出前执行。
        # 用 weakref（弱引用）：只是「记得」这个窗口，不会因此让窗口一直占着内存不释放。
        _ref = weakref.ref(self)

        def _stop_on_exit() -> None:
            win = _ref()
            if win is None:
                return                      # 窗口早已被释放，没什么要停的
            try:
                win._shutdown_workers()
            except Exception:               # 退出阶段 Qt 对象可能已被销毁，忽略即可
                pass
        atexit.register(_stop_on_exit)
        self._load_quick_folders()                          # M30：恢复快捷文件夹 chips
        self.part_list.set_fills_check(self._fills_check)   # 启动时同步左栏橙点开关
        self.part_list.set_template(self._tpl)              # 分类 chips 顺序来源
        if self._tpl:
            self.matrix.set_template(self._tpl)
            self.anim_view.set_available_dirs(set(self._tpl.directions))
        self.statusBar().showMessage("就绪 · 拖入文件夹开始")
        # 恢复 HUD 信息面板开关（M32，默认开）
        self.anim_view.set_hud_visible(
            bool(self._settings.value("hud/visible", True, type=bool)))
        # 恢复显示层面板开关（M36.2，默认开）
        self.anim_view.set_layer_panel_visible(
            bool(self._settings.value("display/layer_panel", True, type=bool)))
        self._layer_section.set_expanded(self.anim_view._layer_btn.isChecked())   # M44.2a
        # 恢复 A区「原图/自适应」模式
        # M44.1 修复：Windows 注册表把布尔值存成字符串 "true"/"false"，
        # 旧写法 bool("false") 结果是 True → 只要保存过一次，重启后总是自适应。
        # 加 type=bool 让 QSettings 自己正确转换。
        saved_fit = bool(self._settings.value("grid/fit_mode", False, type=bool))
        if saved_fit:
            self.grid_view._mode_btn.setChecked(True)    # 两段式按钮互斥，原图会自动弹起
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
        # M30：快捷文件夹 chips（点击切换 / 星标收藏 / 移除）
        self.drop.quick_folder_clicked.connect(self._on_quick_folder_clicked)
        self.drop.quick_folder_star_toggled.connect(self._on_quick_star_toggled)
        self.drop.quick_folder_removed.connect(self._on_quick_removed)
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
        # M34：固定画布对齐——A/B 区按原始渲染画布显示、角色不平移，
        # 「有无武器/不同部件组合」跨视图比对时角色位置纹丝不动
        self.canvas_btn = QPushButton("📐 画布对齐")
        self.canvas_btn.setCheckable(True)
        self.canvas_btn.setChecked(self._canvas_align)
        self.canvas_btn.setToolTip(
            "固定画布对齐模式：\n"
            "开启 = A/B 区按原始渲染画布显示，角色不随内容（如武器）平移，\n"
            "切换部件/套装时角色位置纹丝不动，便于跨视图比对对齐\n"
            "关闭 = 默认「并集 bbox」模式（内容自适应裁剪，画面更紧凑）"
        )
        # M34.1：开启态金底深字（与 A 区「自适应」选中样式同规格）——
        # 深色主题下默认 checked 无任何视觉差异，用户反馈两态不易分辨
        self.canvas_btn.setStyleSheet(
            "QPushButton:checked { color: #1E2023; background: #D4AF37;"
            " border-color: #D4AF37; font-weight: 600; }"
        )
        self.canvas_btn.toggled.connect(self._on_canvas_align_toggled)
        top.addWidget(self.canvas_btn)
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
        self.part_list.clean_extra_requested.connect(self._on_part_list_clean_extra_requested)
        self.part_list.export_requested.connect(self._on_part_list_export_requested)   # M45
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
        self.grid_view.frame_copied.connect(lambda msg: self.statusBar().showMessage(msg, 4000))
        self.anim_view = AnimView()
        # 方向/动作按钮矩阵注入 B 区画布与控制栏之间
        self.matrix = ButtonMatrix()
        self.matrix.direction_selected.connect(self._on_direction_selected)
        self.matrix.action_selected.connect(self._on_action_selected)
        self.matrix.delete_action_requested.connect(self._on_delete_action_requested)
        self.matrix.clean_extra_requested.connect(self._on_matrix_clean_extra_requested)
        # 「显向」toggle → 画布 overlay 开关；overlay 方向点击 → 等同方向按钮点击
        self.matrix.overlay_toggled.connect(self.anim_view.set_dir_overlay_enabled)
        self.anim_view.direction_overlay_clicked.connect(self._on_direction_selected)
        self.anim_view.set_matrix_widget(self.matrix)
        self.anim_view.part_toggles_signal().connect(self._on_part_toggled)
        self.anim_view.fx_offset_changed.connect(self._on_fx_offset_changed)
        self.anim_view.fx_dressed_signal().connect(self._on_fx_dressed)  # M26 穿戴特效
        self.anim_view.wing_dressed_signal().connect(self._on_wing_dressed)  # M28 穿戴翅膀
        self.anim_view.look_dressed_signal().connect(self._on_look_dressed)  # M38 穿戴时装/头发/武器
        # M32：HUD 信息面板开关持久化
        self.anim_view.hud_toggled.connect(self._on_hud_toggled)
        # M36.2：显示层面板开关持久化
        self.anim_view.layer_panel_toggled.connect(self._on_layer_panel_toggled)
        # M45：B 区标题行「⤓ 导出」
        self.anim_view.export_requested.connect(self._on_export_requested)
        panes.addWidget(self.grid_view)
        panes.addWidget(self.anim_view)

        # M44 阶段 2a：右栏检查器——方向/动作按钮 + 显示层/穿戴，从 B 区里搬出来放最右边一列，
        # B 区画布只放动画本身（不再被按钮列和显示层面板挡住）
        self.inspector = Inspector()
        matrix_w, toggles, placeholder = self.anim_view.dock_side_panels()
        self.matrix.set_horizontal(True)              # 右栏够宽：方向列、动作列左右并排
        self._matrix_section = self.inspector.add_section("方向 × 动作", matrix_w)
        self._layer_section = self.inspector.add_section("显示层 / 穿戴", toggles, placeholder)
        # 折叠「方向 × 动作」区块：只记住状态（下次打开沿用）
        self._matrix_section.set_expanded(
            bool(self._settings.value("inspector/matrix_open", True, type=bool)))
        self._matrix_section.toggled.connect(self._on_matrix_section_toggled)
        # 折叠「显示层 / 穿戴」区块 = 原来底栏的「☰ 显示层」开关（按钮已隐藏，逻辑照旧）：
        # 点区块标题 → 拨动那个开关；开关变化（启动恢复除外）→ 同步区块标题
        self._layer_section.toggled.connect(self.anim_view._layer_btn.setChecked)
        self.anim_view.layer_panel_toggled.connect(self._layer_section.set_expanded)
        panes.addWidget(self.inspector)

        panes.setStretchFactor(0, 5)
        panes.setStretchFactor(1, 4)
        panes.setStretchFactor(2, 0)                  # 拉宽窗口时右栏不跟着变宽，多出来的给 A/B
        right_layout.addWidget(panes, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # 左栏宽度锁定（PartList.setFixedWidth），这里给一致的初始比例即可。
        # 3列宽度：优先从 QSettings 恢复，否则用默认值
        # QSettings 返回 QVariantList，需显式转 list[int]（PySide6 类型严格）
        saved_splitter = self._settings.value("layout/splitter", None)
        saved_panes = self._settings.value("layout/panes", None)
        splitter.setSizes([int(x) for x in saved_splitter] if saved_splitter else [220, 1060])
        # M44.2a：现在是 A | B | 右栏 三段。旧版本只存了 A、B 两段 → 保留用户的 A/B 宽度，右栏用默认宽
        sizes = [int(x) for x in saved_panes] if saved_panes else []
        if len(sizes) == 2:
            sizes.append(INSPECTOR_DEFAULT_WIDTH)
        if len(sizes) != 3:
            sizes = [400, 480, INSPECTOR_DEFAULT_WIDTH]   # B 区是看动画的主舞台，默认给宽一点
        panes.setSizes(sizes)

        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

        # 全窗口拖拽：central widget 接收拖放，事件过滤器统一处理
        central.setAcceptDrops(True)
        central.installEventFilter(self)

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """M44.1：播放控制快捷键。

        | 按键 | 作用 |
        | 空格 | 播放 / 暂停 |
        | ,  . | 上一帧 / 下一帧（自动暂停） |
        | Home / End | 第一帧 / 最后一帧 |

        为什么用 QShortcut 而不是写在 keyPressEvent 里：
        点过方向/动作按钮后焦点停在那个按钮上，空格会被按钮自己吃掉（等于再点一次按钮），
        keyPressEvent 根本收不到。QShortcut 会先于按钮拿到按键。

        在过滤框、F2 改名框、模板下拉里打字时不受影响：
        这些输入控件会优先接管按键（Qt 的 ShortcutOverride 机制），快捷键不会触发。
        已知取舍：焦点在左栏列表时，Home/End 不再跳到列表首尾，而是跳到首/尾帧。
        """
        bindings = [
            ("Space", self.anim_view.toggle_play),
            (",", lambda: self.anim_view.step_frame(-1)),
            (".", lambda: self.anim_view.step_frame(+1)),
            ("Home", lambda: self.anim_view.goto_edge(last=False)),
            ("End", lambda: self.anim_view.goto_edge(last=True)),
            ("Ctrl+E", self._on_export_requested),        # M45：导出当前动画
        ]
        self._shortcuts: list[QShortcut] = []
        for keys, handler in bindings:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.WindowShortcut)    # 整个主窗口内有效（弹出的对话框里不生效）
            sc.activated.connect(handler)
            self._shortcuts.append(sc)          # 保存引用，防止被 Python 回收后快捷键失效

    # ---------------- 行为 ----------------
    def _on_folder_dropped(self, folder: Path, auto: bool = False) -> None:
        if self._tpl is None:
            self.statusBar().showMessage("⚠ 无可用模板（templates/ 为空）")
            return
        self._last_folder = folder
        self._blank_generation += 1
        from .core.atlas import clear_atlas_cache
        clear_atlas_cache()
        self._result = scan_root(folder, self._tpl, char_type_of=self._namemap.char_type)
        # M26：全局特效库——所有 flat 特效，无论物理位置（套装目录内 / 最外层
        # 独立文件夹），供任意套装穿戴，无需复制到每个套装目录。
        self._fx_library = list(self._result.fx_library)
        self._fx_by_key = {p.name: p for p in self._fx_library}
        # M28：全局翅膀库——所有 wings 部件，无论物理位置，供任意套装穿戴
        self._wing_library = list(self._result.wing_library)
        self._wing_by_key = {p.name: p for p in self._wing_library}
        self.drop.set_current(str(folder))
        self.drop.set_current_folder(folder)   # M31：chip 激活态高亮当前目录
        self._setup_namemap(folder)
        self.part_list.set_namemap(self._namemap)
        # 先清空上一轮选择状态，再重建列表：load_result 会自动选中第一项并同步
        # 触发 part_selected 信号设置 _part；若在其后清空 _part/_group，会把刚
        # 建立的选中状态抹掉——表现为「界面显示 idle 但点方向/动作按钮无反应，
        # 需重新点选左栏子项才恢复」（2026-09-01 修复）。
        self._group = None
        self._part = None
        self.part_list.load_result(self._result)
        # 恢复上次选中的资源项（优先用 QSettings 记忆；若与自动选中的第一项相同，
        # setCurrentItem 不触发信号，但 _part 已由 load_result 的自动选中设置好）
        saved_sel = self._settings.value("last/selection", "")
        if saved_sel:
            self.part_list._select_by_key(saved_sel)
        self._hidden_parts = set(self._settings.value("layering/hidden_parts", [], type=list))
        # M39：左栏按最长条目自适应加宽（只加宽不收窄），默认完整展示名字。
        # 自动重扫（auto=True）不触发——避免覆盖用户手动拖窄的宽度。
        if not auto:
            w = self.part_list.preferred_width()
            sizes = self._splitter.sizes()
            if w and w > sizes[0]:
                self._splitter.setSizes([w, max(1, sum(sizes) - w)])
        if not self._result.parts:
            self.statusBar().showMessage(
                f"ℹ 未识别到符合模板的部件文件夹（忽略 {len(self._result.ignored)} 项）"
            )
        self._setup_dir_watcher(folder)
        self._record_quick_folder(folder)

    # ---------------- M30：快捷文件夹（收藏 + 最近） ----------------
    QUICK_RECENT_MAX = 5     # 最近列表上限（收藏不计入、不被挤掉）
    QUICK_FAV_MAX = 8        # 收藏上限

    def _load_quick_folders(self) -> None:
        """从 QSettings 读收藏/最近列表；已不存在的目录自动剔除。

        注意：QSettings `type=list` 对单元素列表返回 str 而非 list（Qt 特性），
        必须先归一化，否则只剩 1 个目录时会被逐字符过滤成空。
        """
        def norm(v) -> list[str]:
            if v is None:
                return []
            return [v] if isinstance(v, str) else list(v)
        favs = [p for p in norm(self._settings.value("folders/favs", [], type=list))
                if Path(p).is_dir()]
        recents = [p for p in norm(self._settings.value("folders/recents", [], type=list))
                   if Path(p).is_dir() and p not in favs]
        self._quick_favs = favs
        self._quick_recents = recents
        self._refresh_quick_chips()

    def _refresh_quick_chips(self) -> None:
        self.drop.set_quick_folders(
            [Path(p) for p in self._quick_favs],
            [Path(p) for p in self._quick_recents],
        )

    def _save_quick_folders(self) -> None:
        self._settings.setValue("folders/favs", self._quick_favs)
        self._settings.setValue("folders/recents", self._quick_recents)
        self._settings.sync()

    def _record_quick_folder(self, folder: Path) -> None:
        """拖入成功后记入「最近」：去重、最新在前、超上限挤掉最旧；收藏不动。"""
        s = str(folder)
        if s in self._quick_favs:
            self._refresh_quick_chips()
            return
        if s in self._quick_recents:
            self._quick_recents.remove(s)
        self._quick_recents.insert(0, s)
        del self._quick_recents[self.QUICK_RECENT_MAX:]
        self._save_quick_folders()
        self._refresh_quick_chips()

    def _on_quick_folder_clicked(self, folder: Path) -> None:
        if not folder.is_dir():
            self.statusBar().showMessage(f"⚠ 快捷目录已不存在: {folder}")
            return
        self._on_folder_dropped(folder)
        self.statusBar().showMessage(f"📁 已切换到快捷目录: {folder}")

    def _on_quick_star_toggled(self, folder: Path, star: bool) -> None:
        s = str(folder)
        if star:
            if s in self._quick_favs:
                self._quick_favs.remove(s)
            self._quick_favs.insert(0, s)
            del self._quick_favs[self.QUICK_FAV_MAX:]
            if s in self._quick_recents:
                self._quick_recents.remove(s)
        else:
            if s in self._quick_favs:
                self._quick_favs.remove(s)
            if s not in self._quick_recents:
                self._quick_recents.insert(0, s)
                del self._quick_recents[self.QUICK_RECENT_MAX:]
        self._save_quick_folders()
        self._refresh_quick_chips()

    def _on_quick_removed(self, folder: Path) -> None:
        s = str(folder)
        if s in self._quick_favs:
            self._quick_favs.remove(s)
        if s in self._quick_recents:
            self._quick_recents.remove(s)
        self._save_quick_folders()
        self._refresh_quick_chips()
        self.statusBar().showMessage(f"🗑 已从快捷列表移除: {folder.name}")

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
        self._on_folder_dropped(root, auto=True)
        if key:
            self.part_list._select_by_key(key)      # 已删除则保持默认第一项
        if self._part is not None or self._group is not None:
            self._update_matrix(direction, action)  # 新数据缺失时由矩阵兜底
            if paused:
                self.anim_view.set_resume_state(True, idx)
            self._after_matrix_change()
        elif self._result is not None and not self._result.parts:
            self.grid_view.show_sequence([])
            self.anim_view.show_sequence([])
            self.anim_view.hide_part_toggles()
            self.anim_view.update_hud(None)
            if self._tpl is not None:
                self.matrix.set_template(self._tpl)
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
        # M33：切换部件保持当前方向/动作（直到用户主动改变）；
        # 新部件缺失该组合时由 show_part 兜底回退默认
        direction, action = self.matrix.current()
        self._update_matrix(direction, action)
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
        # M36：worldboss 变体不参与叠层（无 toggle），除非组内只有 worldboss
        # M37：显示层 = 本组 + 同形象其他 ID 组部件（跨 ID 时装+头发+武器组合）
        display = self._display_parts(grp)
        parts = {self._toggle_key(p, display): self._toggle_label(p, display)
                 for p in display}
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
        # M38/M42：穿戴时装/头发/武器——当前显示（含形象组合）缺哪类才提供哪类的下拉；
        # 未手动指定时，weapon 槽位按武器标签（如「斧」/「剑」）自动推断默认武器
        # M44.2：只有「模块化角色」（显示部件里有身体/头发/武器任一）才提供这三个下拉。
        # 怪物/NPC/伙伴这类纯 ID 整包资源、单独的翅膀/坐骑/特效，穿主角的时装武器没有意义，
        # 以前会一口气列出 5 个下拉框挤占画布（鳄鱼 503094 实测截图）。
        have = {p.part for p in display}
        modular = self._is_modular_look(display)
        for slot in self._LOOK_SLOTS:
            offer = modular and slot not in have        # 是模块化角色，且自己没有这个部位
            items = self._look_library(slot) if offer else []
            cur = self._effective_dressed_look(slot, grp) if offer else ""
            getattr(self.anim_view, f"set_{slot}_library")(items, cur)
        # M33：切换组保持当前方向/动作（直到用户主动改变）；
        # 新组缺失该组合时由 show_group 兜底回退默认
        direction, action = self.matrix.current()
        self._update_matrix(direction, action)
        self._after_matrix_change()

    def _toggle_key(self, p, parts: list) -> str:
        """显隐 toggle 的 key：部件名；重名时追加 `·ID`。（M45：逻辑在 core/compose.py）"""
        return compose.toggle_key(p, parts)

    def _toggle_label(self, p, parts: list) -> str:
        base = p.part or p.name
        key = self._toggle_key(p, parts)
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
            sibs = [(q.res_id, q.part) for q in parts]
            cn, owner = self._namemap.part_cn_in(p.part, p.res_id, sibs)
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

    # ---------------- M38：穿戴时装/头发/武器（跨 ID 形象组） ----------------
    _LOOK_SLOTS = compose.LOOK_SLOTS      # ("body", "hair", "weapon")，定义在 core/compose.py

    def _is_modular_look(self, display: list[PartData]) -> bool:
        """M44.2：是不是「模块化角色」（有身体/头发/武器部件）。（M45：逻辑在 core/compose.py）"""
        return compose.is_modular_look(display)

    _LOOK_SLOT_CN = {"body": "时装", "hair": "头发", "weapon": "武器"}

    def _load_dressed_look(self, slot: str) -> dict[str, str]:
        """M38：从 QSettings 读取各组的穿戴选择 {组key: 形象组key}。"""
        self._settings.beginGroup(f"layering/dressed_{slot}")
        out = {k: self._settings.value(k, "", type=str)
               for k in self._settings.childKeys()}
        self._settings.endGroup()
        return out

    def _save_dressed_look(self, slot: str, group_key: str, look_key: str) -> None:
        self._settings.beginGroup(f"layering/dressed_{slot}")
        self._settings.setValue(group_key, look_key)
        self._settings.endGroup()
        self._settings.sync()

    def _group_by_key(self, key: str) -> IdGroup | None:
        return compose.group_by_key(key, self._compose_ctx())

    def _look_group_label(self, grp: IdGroup) -> str:
        """可穿戴形象组的下拉显示名。（M45：逻辑在 core/compose.py）"""
        return compose.look_group_label(grp, self._compose_ctx())

    def _look_library(self, slot: str) -> list[tuple[str, str]]:
        """M38：可穿戴的形象组列表 [(组key, 显示名)]。（M45：逻辑在 core/compose.py）"""
        return compose.look_library(slot, self._compose_ctx())

    def _auto_weapon_key(self, grp: IdGroup) -> str:
        """M42：按名字里的武器标签自动推断默认武器。（M45：逻辑在 core/compose.py）"""
        if self._result is None:
            return ""
        return compose.auto_weapon_key(grp, self._compose_ctx())

    def _effective_dressed_look(self, slot: str, grp: IdGroup) -> str:
        """M38/M42：某组在某槽位实际生效的穿戴组 key。（M45：逻辑在 core/compose.py）"""
        return compose.resolve_dressed(slot, grp, self._look_for(grp), self._compose_ctx())

    def _dressed_look_parts(self, display: list[PartData]) -> list[PartData]:
        """M38：当前组穿戴的时装/头发/武器部件（空帧抽样用）。（M45：逻辑在 core/compose.py）"""
        return compose.dressed_look_parts(self._group, display,
                                          self._look_for(self._group), self._compose_ctx())

    def _on_look_dressed(self, slot: str, look_key: str) -> None:
        """M38：穿戴/脱下时装/头发/武器 → 按当前组 key 记忆并刷新。"""
        if self._group is None or slot not in self._LOOK_SLOTS:
            return
        self._save_dressed_look(slot, self._group.key, look_key)
        setattr(self, f"_dressed_{slot}", self._load_dressed_look(slot))
        if look_key:
            grp = self._group_by_key(look_key)
            name = self._look_group_label(grp) if grp else look_key
            gname = self._group.display_name or self._group.res_id
            self.statusBar().showMessage(
                f"👤 {gname} 穿戴{self._LOOK_SLOT_CN[slot]}：{name}", 3000)
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

    def _on_canvas_align_toggled(self, checked: bool) -> None:
        """M34：画布对齐开关——重算 A/B 区显示（QSettings 记忆）。

        M34.1：文案 + 金底高亮 + 状态栏三重反馈，两态一眼可辨。
        注意 setChecked() 会触发 toggled：启动恢复选中态时本回调先于
        「就绪」状态栏执行，随后被覆盖，无副作用。
        """
        self._canvas_align = checked
        self._settings.setValue("display/canvas_align", checked)
        self._settings.sync()
        self.canvas_btn.setText("✓ 画布对齐" if checked else "📐 画布对齐")
        self.statusBar().showMessage(
            "📐 画布对齐：开——角色跨视图位置固定不动（比对武器/部件组合）"
            if checked else
            "📐 画布对齐：关——恢复内容并集自适应裁剪", 4000)
        if self._part is not None or self._group is not None:
            self._show_grid()
            self._show_anim()

    def _on_direction_selected(self, direction: str) -> None:
        # 切换方向时保持当前动作（该动作在新方向缺失时由 show_part/show_group 兜底）
        _, action = self.matrix.current()
        self._update_matrix(direction, action)
        self._after_matrix_change()

    def _on_action_selected(self, action: str) -> None:
        direction, _ = self.matrix.current()
        self._update_matrix(direction, action)
        self._after_matrix_change()

    def _after_matrix_change(self) -> None:
        # 同步当前方向给 overlay，使画布高亮与按钮矩阵一致
        direction, _ = self.matrix.current()
        self.anim_view.set_current_dir(direction)
        self._show_grid()
        self._show_anim()
        self._refresh_status()
        self._update_hud()

    # ---------------- M32：HUD 信息面板 ----------------
    def _update_hud(self) -> None:
        """HUD 帧数账目：随选择/方向/动作/显隐变化全量刷新（纯内存，零 IO）。"""
        if self._part is None and self._group is None:
            self.anim_view.update_hud(None)
            return
        direction, action = self.matrix.current()
        if self._part is not None:
            stats = build_hud_stats(self._part, self._tpl)
            cn = self._namemap.lookup(self._part.name, self._part.res_id) \
                if self._namemap is not None else None
            stats.title = cn or self._part.name
            # M36：部位显示走中文名映射（worldboss → 世界BOSS）
            part_label = (self._namemap.part_cn(self._part.part)
                          if self._namemap is not None else (self._part.part or "整体"))
            stats.subtitle = f"ID {self._part.res_id} · {part_label}"
        else:
            stats = build_hud_stats(self._group, self._tpl)
            gname = self._group.display_name or self._group.res_id
            cn = self._namemap.lookup(gname, self._group.res_id) \
                if self._namemap is not None else None
            stats.title = cn or gname
            n = len(self._render_parts(self._group))
            if self._group.is_outfit:
                stats.subtitle = f"套装 · {n}件"
            elif self._group.is_variant:
                stats.subtitle = f"ID {self._group.res_id} · 世界BOSS"
            else:
                stats.subtitle = f"ID {self._group.res_id} · {n}部件"
        stats.current = (direction, action)
        self.anim_view.update_hud(stats)

    def _on_hud_toggled(self, visible: bool) -> None:
        self._settings.setValue("hud/visible", visible)
        self._settings.sync()

    def _on_matrix_section_toggled(self, expanded: bool) -> None:
        """M44.2a：右栏「方向 × 动作」区块折叠/展开 → 记住（QSettings）。"""
        self._settings.setValue("inspector/matrix_open", expanded)
        self._settings.sync()

    def _on_layer_panel_toggled(self, visible: bool) -> None:
        """M36.2：显示层面板开关持久化（QSettings）。"""
        self._settings.setValue("display/layer_panel", visible)
        self._settings.sync()

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
            for p in self._render_parts(self._group):
                ad = p.action_data(direction, action)
                if ad:
                    return ad
        return None

    def _render_parts(self, grp: IdGroup) -> list[PartData]:
        """M36：组视图参与叠层的部件（排除 worldboss 变体）。（M45：逻辑在 core/compose.py）"""
        return compose.render_parts(grp)

    def _look_split(self, grp: IdGroup) -> tuple[str, str] | None:
        """M37：组的「形象前缀 + 类型词」。（M45：逻辑在 core/compose.py）"""
        return compose.look_split(grp, self._compose_ctx())

    def _display_parts(self, grp: IdGroup) -> list[PartData]:
        """M37：组视图实际显示的部件（含同形象跨 ID 组合）。（M45：逻辑在 core/compose.py）"""
        return compose.display_parts(grp, self._compose_ctx())

    # ---------------- M36：空帧抽样（全透明占位图检测） ----------------
    def _sync_blank_check(self) -> None:
        """把当前选中项的空序列集合回填给按钮矩阵；未命中缓存则启动后台抽样。"""
        key = self._blank_key()
        if key is None:
            self.matrix.set_blank(set())
            return
        cached = self._blank_cache.get(key)
        if cached is not None:
            self.matrix.set_blank(cached)
            return
        self.matrix.set_blank(set())
        self._start_blank_worker(key)

    def _blank_key(self) -> str | None:
        if self._part is not None:
            return str(self._part.folder)
        if self._group is not None:
            return self._group.key
        return None

    def _current_blank(self) -> set:
        """当前选中项的空序列集合（未抽样/抽样中返回空集）。"""
        key = self._blank_key()
        return self._blank_cache.get(key, set()) if key else set()

    def _blank_target_matrix(self) -> dict:
        """抽样目标 matrix：单部件用自身；组用显示部件并集（M37 形象组合 + M38 穿戴，与叠层口径一致）。"""
        if self._part is not None:
            return self._part.matrix
        if self._group is None:
            return {}
        display = self._display_parts(self._group)
        union: dict[str, dict] = {}
        for p in display + self._dressed_look_parts(display):
            for d, col in p.matrix.items():
                row = union.setdefault(d, {})
                for a, ad in col.items():
                    row.setdefault(a, ad)
        return union

    def _start_blank_worker(self, key: str) -> None:
        # 同 key 已有抽样在跑 → 等它的结果即可（避免重复解码与重复回填）
        if any(getattr(w, "_key", None) == key for w in self._blank_workers
               if w.isRunning()):
            return
        target = self._blank_target_matrix()
        if not target:
            return
        worker = _BlankCheckWorker(target, key, self._blank_generation)
        worker.blank_ready.connect(self._on_blank_ready)
        worker.finished.connect(self._on_blank_worker_finished)
        self._blank_workers.append(worker)
        worker.start()

    def _on_blank_ready(self, key: str, gen: int, blank: set) -> None:
        if gen != self._blank_generation:
            return
        self._blank_cache[key] = blank
        if key == self._blank_key():
            # 仍停留在该选中项 → **就地**刷新角标与状态栏（不重建按钮、不动 A/B 区，
            # 避免外部按钮引用/悬停态失效，也避免重复解码）
            self.matrix.apply_blank(blank)
            self._refresh_status()

    def _on_blank_worker_finished(self) -> None:
        w = self.sender()
        if w in self._blank_workers:
            self._blank_workers.remove(w)
        w.deleteLater()

    def _owning_group(self, part: PartData) -> IdGroup | None:
        if self._result is None:
            return None
        return next((g for g in self._result.groups if part in g.parts), None)

    def _update_matrix(self, direction: str | None, action: str | None) -> None:
        # M36：先回填空帧角标数据（缓存命中同步；未命中置空并后台抽样）
        self._sync_blank_check()
        if self._group is not None:
            part_groups = (
                {str(p.folder): g for g in self._result.groups for p in g.parts}
                if self._result else {}
            )
            self.matrix.show_group(
                self._group, direction, action,
                display_parts=self._display_parts(self._group),
                part_groups=part_groups,
            )
        elif self._part is not None:
            self.matrix.show_part(
                self._part, direction, action,
                group=self._owning_group(self._part),
            )

    # ---------------- M45：叠层配方（逻辑在 core/compose.py）----------------
    def _compose_ctx(self) -> compose.ComposeContext:
        """把「全局资料」打包给 compose：扫描结果、模板、匹配表、特效库、翅膀库。

        每次调用现打包（很便宜，只是引用），这样重扫目录、换模板、重载匹配表后
        不会拿到旧数据。
        """
        return compose.ComposeContext(
            groups=self._result.groups if self._result is not None else [],
            tpl=self._tpl,
            namemap=self._namemap,
            fx_by_key=self._fx_by_key,
            wing_by_key=self._wing_by_key,
        )

    def _look_for(self, grp: IdGroup | None) -> compose.LookConfig:
        """把某个组的「穿搭快照」从界面状态里收集出来交给 compose。

        - 时装/头发/武器：只放「真的记住过」的槽位（没记过 = 交给自动推断，见 LookConfig 说明）
        - 翅膀/特效：记住的资源名，没有就是 ""
        - 显示层关掉的部件、特效微调偏移（按需从 QSettings 读）
        """
        key = grp.key if grp is not None else ""
        dressed: dict[str, str] = {}
        for slot in self._LOOK_SLOTS:
            store = getattr(self, f"_dressed_{slot}")
            if key in store:
                dressed[slot] = store[key]
        return compose.LookConfig(
            dressed_look=dressed,
            wing=self._dressed_wings.get(key, ""),
            fx=self._dressed_fx.get(key, ""),
            hidden_parts=set(self._hidden_parts),
            fx_offset_of=self._get_fx_offset,
        )

    def _layers_for_current(self) -> tuple[
        list[list[Path]], list[bool], dict[int, tuple[int, int]], list[str]
    ]:
        """按当前 (组/部件) + (方向,动作) 算各层帧序列 + 特效 mask + 偏移 + part keys。

        返回 (layers, flat_mask, fx_offsets, part_keys)：
        - flat_mask[i]=True 表示第 i 层是特效层
        - fx_offsets[i] = (dx, dy) 用户微调偏移（仅特效层有值）
        - part_keys[i] = 第 i 层 key（与 layers 严格同序，偏移微调靠它定位）

        M45：具体怎么叠搬到了 core/compose.py 的 build_stack；这里只做三件事：
        ① 读按钮矩阵当前的方向/动作 ② 收集穿搭和全局资料 ③ 调 build_stack。
        顺带更新 self._fx_layer_indices（Ctrl+方向键微调要用）。
        """
        direction, action = self.matrix.current()
        if not direction or not action:
            return [], [], {}, []
        target = self._part if self._part is not None else self._group
        stack = compose.build_stack(target, direction, action,
                                    self._look_for(self._group), self._compose_ctx())
        self._fx_layer_indices = stack.fx_layer_indices
        return stack.as_tuple()

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
        self.grid_view.show_sequence(layers, flat_mask, fx_offsets,
                                     canvas_align=self._canvas_align)

    def _show_anim(self) -> None:
        """B 区同步加载当前组合的动画。"""
        if self._group is None and self._part is None:
            return
        # M26：part_keys 由 _layers_for_current 一并产出，天然与 layers 同序
        layers, flat_mask, fx_offsets, part_keys = self._layers_for_current()
        self.anim_view.set_fx_part_keys(part_keys)
        # M28：告知画布哪些层是真特效（Ctrl+方向键微调目标），穿戴翅膀会被排除
        self.anim_view.set_fx_layer_indices(self._fx_layer_indices)
        self.anim_view.show_sequence(layers, flat_mask, fx_offsets,
                                     canvas_align=self._canvas_align)

    def _refresh_status(self) -> None:
        """状态栏：帧数 / 帧号连续性 / 缺漏 / 配套摘要。"""
        if self._part is None and self._group is None:
            return
        direction, action = self.matrix.current()
        segs: list[str] = []
        if self._group is not None:
            gname = self._group.display_name or self._group.res_id
            core_n = len(self._render_parts(self._group))
            # M44.2a 修复：「单部件 / N 层叠合」要按**实际显示**的部件数判断（含 M37 形象组合）。
            # 以前只看组自己的部件：头发组 501111001 本身 1 个部件，但形象组合又带出 4 个，
            # 状态栏却写「单部件 · 形象组合 +4 部件」，自相矛盾（实测截图）
            shown = self._display_parts(self._group)
            if self._group.is_variant:
                segs.append(f"变体 {gname}（{core_n} 层）")
            elif len(shown) <= 1:
                # M44.1：单部件资源（如 503122 宝箱怪）没有叠层，
                # 不再显示「1 层叠合 · shadow 最底」这种无意义的信息
                segs.append(f"组 {gname}（单部件）")
            else:
                # 只有显示的部件里真的有影子时才提示「shadow 最底」
                has_shadow = any(p.part == "shadow" for p in shown)
                segs.append(
                    f"组 {gname}（{len(shown)} 层叠合"
                    + (" · shadow 最底" if has_shadow else "")
                    + "）"
                )
            # M37：形象组合生效时提示叠加了多少跨 ID 部件
            extra = len(self._display_parts(self._group)) - core_n
            if extra > 0:
                segs.append(f"👤 形象组合 +{extra} 部件")
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
            if (direction, action) in self._current_blank():
                segs.append("⚠ 空帧（图片全透明，占位图）")
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

    # ---------------- M43：多余动作识别与安全清理（移入回收站） ----------------
    def _stop_all_workers_and_caches(self) -> None:
        self._rescan_timer.stop()
        old_dirs = self._dir_watcher.directories()
        if old_dirs:
            self._dir_watcher.removePaths(old_dirs)
        self.grid_view._stop_worker()
        self.anim_view._stop_worker()
        for w in list(self._blank_workers):
            if w.isRunning():
                w.requestInterruption()
                w.wait()
        from .core.atlas import clear_atlas_cache
        clear_atlas_cache()

    def _current_cleanup_targets(self) -> tuple[list[PartData], IdGroup | None]:
        if self._part is not None:
            return ([] if self._part.is_flat else [self._part]), self._owning_group(self._part)
        if self._group is not None:
            return cleanup_target_parts(self._display_parts(self._group)), self._group
        return [], None

    def _execute_trash_items(self, items: list[CleanupItem], trash_fn=None) -> tuple[int, int]:
        """执行移入回收站并自动重扫刷新（无模态弹窗，供交互入口与测试复用）。"""
        if not items:
            return 0, 0
        all_paths = [path for item in items for path in item.paths]
        self._blank_generation += 1
        self._stop_all_workers_and_caches()
        try:
            moved, failed = move_to_trash(all_paths, trash_fn=trash_fn)
        finally:
            self._rescan_timer.stop()
        moved_set = {os.path.normcase(os.path.abspath(str(x))) for x in moved}
        ok_items = sum(
            1 for item in items
            if all(os.path.normcase(os.path.abspath(str(x))) in moved_set for x in item.paths)
        )
        if moved:
            self._blank_cache.clear()
            self._auto_rescan()
        elif self._result is not None and self._auto_refresh:
            self._setup_dir_watcher(self._result.root)
        if failed:
            first_path, first_err = failed[0]
            self.statusBar().showMessage(
                f"⚠ 已移入回收站 {ok_items} 项，{len(failed)} 个路径失败：{first_path.name}（{first_err}）",
                6000,
            )
        elif ok_items:
            self.statusBar().showMessage(
                f"🗑 已将 {ok_items} 项移入回收站（可从系统回收站还原）",
                5000,
            )
        return ok_items, len(failed)

    def _confirm_and_trash(self, items: list[CleanupItem], title: str) -> None:
        if not items:
            self.statusBar().showMessage("ℹ 当前无可清理的磁盘动作资源", 3000)
            return
        dlg = CleanupConfirmDialog(items, title=title, parent=self)
        if dlg.exec() != CleanupConfirmDialog.DialogCode.Accepted:
            return
        chosen = dlg.selected_items()
        if not chosen:
            return
        _ok_cnt, fail_cnt = self._execute_trash_items(chosen)
        if fail_cnt:
            QMessageBox.warning(
                self,
                "部分清理失败",
                f"有 {fail_cnt} 个文件或目录未能移入回收站（可能正被外部程序占用）。",
            )

    def _on_delete_action_requested(self, direction: str, action: str) -> None:
        if self._tpl is None:
            return
        targets, grp = self._current_cleanup_targets()
        items = collect_action_cleanup_items(targets, direction, action, self._tpl, grp)
        self._confirm_and_trash(items, f"移入回收站：删除 {direction}/{action}")

    def _on_matrix_clean_extra_requested(self) -> None:
        if self._tpl is None:
            return
        targets, grp = self._current_cleanup_targets()
        items = [
            it for p in targets
            for it in collect_extra_cleanup_items([p], self._tpl, self._owning_group(p) or grp)
        ]
        self._confirm_and_trash(items, "清理当前项多余动作")

    def _on_part_list_clean_extra_requested(self, key: str) -> None:
        if self._tpl is None or self._result is None:
            return
        if key.startswith("GRP:"):
            grp = next((g for g in self._result.groups if g.key == key[4:]), None)
            if grp is None or grp.is_flat:
                return
            targets = cleanup_target_parts(grp.parts)
            items = collect_extra_cleanup_items(targets, self._tpl, grp)
            label = grp.display_name or grp.res_id
        else:
            pd = self.part_list._parts.get(key)
            if pd is None or pd.is_flat:
                return
            items = collect_extra_cleanup_items([pd], self._tpl, self._owning_group(pd))
            label = pd.name
        self._confirm_and_trash(items, f"清理多余动作 · {label}")

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

    # ---------------- M45：导出当前动画（GIF / APNG）----------------
    def _export_name(self, target) -> str:
        """导出文件夹用的资源名，如「503122_宝箱怪」「501521005_女侠客」。"""
        if hasattr(target, "parts"):                       # 组
            label = self._look_group_label(target)          # 中文名，没有就是组名/ID
            if label.startswith(target.res_id):             # 套装组名本来就带 ID
                return label
            return f"{target.res_id}_{label}" if label != target.res_id else target.res_id
        cn = self._namemap.lookup(target.name, target.res_id) if self._namemap else None
        return f"{target.name}_{cn}" if cn else target.name    # 单部件

    def _layer_display_names(self, keys: list[str]) -> list[str]:
        """把叠层的层名换成给人看的中文名（导出弹窗「本次叠层」用）。"""
        names: dict[str, str] = {}
        if self._group is not None:
            display = self._display_parts(self._group)
            names = {self._toggle_key(p, display): self._toggle_label(p, display) for p in display}
        out = []
        for k in keys:
            if k in names:
                out.append(names[k])
            else:                                           # 穿戴进来的部件：按文件夹名查匹配表
                cn = self._namemap.lookup(k, k.split("_")[0]) if self._namemap else None
                out.append(cn or k)
        return out

    def _export_job(self) -> dict | None:
        """收集「要导出什么」：当前选中项 + 方向/动作 + 叠层配方。没法导出时返回 None 并提示原因。"""
        if self._group is None and self._part is None:
            self.statusBar().showMessage("⤓ 导出：请先在左栏选中一个资源", 4000)
            return None
        direction, action = self.matrix.current()
        if not direction or not action:
            self.statusBar().showMessage("⤓ 导出：请先选择方向和动作", 4000)
            return None
        target = self._part if self._part is not None else self._group
        # 和预览走同一份叠层代码（core/compose.py），保证导出 = 预览
        stack = compose.build_stack(target, direction, action,
                                    self._look_for(self._group), self._compose_ctx())
        n = max((len(layer) for layer in stack.layers), default=0)
        if n == 0:
            self.statusBar().showMessage(f"⤓ 导出：{direction} · {action} 没有帧", 4000)
            return None
        return {"target": target, "direction": direction, "action": action,
                "stack": stack, "frames": n, "name": self._export_name(target)}

    def _look_snapshot(self, grp) -> compose.LookConfig:
        """给「后台线程」用的穿搭快照。

        平时 LookConfig 里的特效偏移是「用到哪个才去 QSettings 读」。批量导出在后台线程里跑，
        不能在线程里读界面线程的 QSettings，所以这里先在主线程把所有特效的偏移一次读好，
        后台线程只查这个字典。
        """
        look = self._look_for(grp)
        names = set(self._fx_by_key)
        if self._result is not None:
            names |= {p.name for p in self._result.parts if p.is_flat}
        offsets = {n: self._get_fx_offset(n) for n in names}
        look.fx_offset_of = lambda key, _o=offsets: _o.get(key, (0, 0))
        return look

    def _on_part_list_export_requested(self, key: str) -> None:
        """左栏右键「⤓ 导出该资源全部动画」：先选中这一项（刷新叠层和方向动作），再按「整个角色」导出。"""
        if self.part_list.current_key() != key:
            self.part_list._select_by_key(key)
        self._on_export_requested("all")

    def _on_export_requested(self, scope: str = "current") -> None:
        """点「⤓ 导出」或按 Ctrl+E：弹出导出弹窗 → 检查输出位置 → 后台导出。

        scope 是弹窗默认选中的范围："current"（当前动画）/ "all"（整个角色，左栏右键用）。
        """
        if self._export_worker is not None and self._export_worker.isRunning():
            self.statusBar().showMessage("⤓ 上一个导出还没完成，请稍等", 4000)
            return
        job = self._export_job()
        if job is None:
            return
        root = self._last_folder or (self._result.root if self._result else Path.home())
        d, a = job["direction"], job["action"]
        # 各范围能导出几个（弹窗里显示、灰掉没意义的选项）：和预览用同一份「有哪些方向×动作」
        ctx, look = self._compose_ctx(), self._look_snapshot(self._group)
        all_items = plan_combos(job["target"], look, ctx)
        dir_items = [c for c in all_items if c[0] == d]
        dlg = ExportDialog(
            self,
            name=job["name"].replace("_", " · ", 1),
            subtitle=f"{d} · {a} · {job['frames']} 帧",
            layers=" · ".join(self._layer_display_names(job["stack"].part_keys)),
            fps=self.anim_view._fps,
            default_dir=default_output_dir(Path(root)),
            rel_path=f"{safe_name(job['name'])}/{safe_name(f'{d}_{a}')}",
            settings=self._settings,
            scopes={"direction": len(dir_items), "all": len(all_items)},
            scope=scope, direction=d, action=a,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        if dlg.scope() != "current":
            self._start_batch_export(job, dlg.scope(), all_items, dir_items, look, ctx, opts,
                                     dlg.output_dir(), Path(root))
            return
        out_path = dlg.output_path()
        # 输出到扫描目录里面：会触发自动刷新重扫（M24），APNG 还可能被当成资源扫进来
        if is_inside(out_path.parent, Path(root)):
            ans = QMessageBox.question(
                self, "导出动画",
                f"输出位置在正在浏览的资源目录里面：\n{out_path.parent}\n\n"
                "导出后会触发自动刷新，导出的文件也可能被当成资源扫进来。\n仍要导出到这里吗？")
            if ans != QMessageBox.Yes:
                return
        if out_path.exists():
            ans = QMessageBox.question(self, "导出动画", f"文件已存在，覆盖吗？\n{out_path}")
            if ans != QMessageBox.Yes:
                return
        # 「按内容裁剪」时一律按原始画布渲染再裁：不同方向/动作之间角色位置不跳；
        # 「与预览一致」时按预览当前的画布对齐开关
        canvas_align = True if opts.crop_padding is not None else self._canvas_align
        self._start_export(job["stack"], opts, out_path, canvas_align)

    def _start_export(self, stack, opts, out_path: Path, canvas_align: bool) -> None:
        self._export_worker = ExportWorker(stack, opts, out_path, canvas_align, self)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self.statusBar().showMessage(f"⤓ 正在导出 {out_path.name} …")
        self._export_worker.start()

    def _on_export_done(self, path: str, info: dict) -> None:
        w, h = info["size"]
        msg = f"{info['frames']} 帧 · {w}×{h} · {info['fps']:g} fps"
        self.statusBar().showMessage(f"⤓ 已导出 {Path(path).name}（{msg}）", 8000)
        box = QMessageBox(self)
        box.setWindowTitle("导出完成")
        box.setText(f"已导出：\n{path}\n\n{msg}")
        open_btn = box.addButton("打开文件夹", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _on_export_failed(self, reason: str) -> None:
        self.statusBar().showMessage(f"⤓ 导出失败：{reason}", 8000)
        QMessageBox.warning(self, "导出失败", reason)

    # ---------------- M45 阶段 C：批量导出 ----------------
    def _start_batch_export(self, job: dict, scope: str, all_items: list, dir_items: list,
                            look: compose.LookConfig, ctx: compose.ComposeContext, opts,
                            out_dir: Path, root: Path) -> None:
        """批量导出：检查输出位置 / 已存在的文件 → 后台逐个导出 → 进度条 → 完成报告。"""
        showcase = scope == "showcase"
        items = all_items if scope == "all" else dir_items
        if not items:
            self.statusBar().showMessage("⤓ 导出：没有可导出的方向×动作", 4000)
            return
        name = job["name"]
        # 输出到扫描目录里面：会触发自动刷新重扫（M24），导出的文件还可能被当成资源扫进来
        if is_inside(out_dir, root):
            ans = QMessageBox.question(
                self, "导出动画",
                f"输出位置在正在浏览的资源目录里面：\n{out_dir}\n\n"
                "导出期间会反复触发自动刷新，导出的文件也可能被当成资源扫进来。\n仍要导出到这里吗？")
            if ans != QMessageBox.Yes:
                return
        exist = [p for p in planned_paths(items, out_dir, name, opts.ext, showcase) if p.exists()]
        if exist:
            ans = QMessageBox.question(
                self, "导出动画",
                f"有 {len(exist)} 个文件已存在，例如：\n{exist[0]}\n\n覆盖这些文件吗？")
            if ans != QMessageBox.Yes:
                return
        # 「按内容裁剪」时一律按原始画布渲染再裁（同单个导出）
        canvas_align = True if opts.crop_padding is not None else self._canvas_align

        worker = BatchExportWorker(job["target"], items, look, ctx, opts, out_dir, name,
                                   canvas_align, showcase, self)
        self._export_worker = worker
        # 进度条：可取消。取消 = 请求线程在「下一项开始前」停下，已写好的文件保留
        n = 1 if showcase else len(items)
        progress = QProgressDialog("准备导出…", "取消", 0, len(items), self)
        progress.setWindowTitle("导出动画")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(worker.requestInterruption)
        self._export_progress = progress
        worker.progress.connect(self._on_batch_progress)
        worker.report_ready.connect(self._on_batch_done)
        worker.failed.connect(self._on_batch_failed)
        self.statusBar().showMessage(f"⤓ 正在批量导出 {name}（{n} 个文件）…")
        progress.show()
        worker.start()

    def _on_batch_progress(self, done: int, total: int, label: str) -> None:
        dlg = self._export_progress
        # 用户点了「取消」后不再更新（否则 setValue 可能把已关掉的进度条又弹出来）
        if dlg is None or dlg.wasCanceled():
            return
        # ⚠ 顺序很重要：先改文字，最后才 setValue。
        # 模态进度条的 setValue 内部会顺手处理排队中的事件，导出恰好在这时结束的话，
        # 「导出完成」会先被处理 → 进度条被关掉、self._export_progress 变成 None，
        # 之后再改文字就会报 AttributeError（Codex 跑测试时实际出现过）。
        dlg.setLabelText(f"正在导出（{min(done + 1, total)} / {total}）：{label}")
        dlg.setMaximum(max(1, total))
        dlg.setValue(min(done, total))

    def _close_batch_progress(self) -> None:
        if self._export_progress is not None:
            try:
                self._export_progress.canceled.disconnect()  # 先断开：close() 会误触发「取消」
            except RuntimeError:                             # 已经没有连接（比如取消时已断开）
                pass
            self._export_progress.close()
            self._export_progress.deleteLater()
            self._export_progress = None

    def _on_batch_done(self, rep) -> None:
        """批量导出结束（完成或中途取消）：弹出报告——成功/跳过/失败各几个，明细和「打开文件夹」。"""
        self._close_batch_progress()
        self.statusBar().showMessage(f"⤓ 批量导出：{rep.summary()}", 10000)
        box = QMessageBox(self)
        box.setWindowTitle("导出完成" if not rep.cancelled else "导出已取消")
        box.setIcon(QMessageBox.Warning if rep.failed else QMessageBox.Information)
        box.setText(f"{rep.summary()}\n\n输出文件夹：\n{rep.folder}")
        if rep.skipped or rep.failed:
            box.setDetailedText(rep.details())               # 点「显示详细信息」展开，不占屏幕
        open_btn = box.addButton("打开文件夹", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and rep.folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(rep.folder)))

    def _on_batch_failed(self, reason: str) -> None:
        self._close_batch_progress()
        self._on_export_failed(reason)

    def _shutdown_workers(self) -> None:
        """停掉所有后台线程：关窗（closeEvent）和程序退出（atexit 兜底）都调用这里。

        - 导出线程：先让它停下，不写半截文件（M45）
        - A/B 区解码线程：先「请它停」（解码循环每帧都会检查，能尽快结束），再等它真正结束
        - 空帧抽样线程、文件监听、图集缓存：交给已有的 _stop_all_workers_and_caches
        可以重复调用：已经停了的线程会直接跳过。
        """
        if self._export_worker is not None and self._export_worker.isRunning():
            self._export_worker.requestInterruption()
            self._export_worker.wait()
        for w in (self.grid_view._worker, self.anim_view._worker):
            if w is not None and w.isRunning():
                w.requestInterruption()
        self._stop_all_workers_and_caches()

    def closeEvent(self, event) -> None:
        """关闭时保存布局宽度 + 最近拖入目录 + 选中项 + A区模式到 QSettings。"""
        self._shutdown_workers()      # 停掉所有后台线程（导出、A/B 区解码、空帧抽样）
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
