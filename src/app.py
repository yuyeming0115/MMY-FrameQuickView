"""主窗口：拖拽 → 扫描 → 左栏分组列表 + 按钮矩阵 + A/B 双区。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .core.namemap import NameMap, discover_map_file
from .core.scanner import IdGroup, PartData, ScanResult, scan_root
from .core.template import Template, load_templates
from .ui.anim_view import AnimView
from .ui.button_matrix import ButtonMatrix
from .ui.drop_zone import DropZone
from .ui.grid_view import GridView
from .ui.part_list import PartList

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
QSplitter::handle { background: #3A3F46; }
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

        self._build_ui()
        if self._tpl:
            self.matrix.set_template(self._tpl)
        self.statusBar().showMessage("就绪 · 拖入文件夹开始")

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶栏：标题 + 模板切换 + 编辑/新建（M4）
        top = QHBoxLayout()
        title = QLabel("MMY-FrameQuickView")
        title.setStyleSheet("font-weight: 700; font-size: 16px; color: #E8E4D9;")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(QLabel("模板"))
        self.tpl_combo = QComboBox()
        self.tpl_combo.addItems([t.name for t in self._templates])
        self.tpl_combo.currentIndexChanged.connect(self._on_template_changed)
        top.addWidget(self.tpl_combo)
        for text in ("✎ 编辑", "＋ 新建"):
            btn = QPushButton(text)
            btn.clicked.connect(self._todo_template_editor)
            top.addWidget(btn)
        root.addLayout(top)

        # 拖拽区
        self.drop = DropZone()
        self.drop.folder_dropped.connect(self._on_folder_dropped)
        root.addWidget(self.drop)

        # 主体：左栏部件列表 | 右侧（按钮矩阵 + A/B 双区）
        splitter = QSplitter()
        self.part_list = PartList()
        self.part_list.part_selected.connect(self._on_part_selected)
        self.part_list.group_selected.connect(self._on_group_selected)
        splitter.addWidget(self.part_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)
        self.matrix = ButtonMatrix()
        self.matrix.direction_selected.connect(self._on_direction_selected)
        self.matrix.action_selected.connect(self._on_action_selected)
        right_layout.addWidget(self.matrix)

        panes = QSplitter()
        self.grid_view = GridView()
        self.anim_view = AnimView()
        panes.addWidget(self.grid_view)
        panes.addWidget(self.anim_view)
        panes.setStretchFactor(0, 5)
        panes.setStretchFactor(1, 4)
        right_layout.addWidget(panes, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

    # ---------------- 行为 ----------------
    def _on_folder_dropped(self, folder: Path) -> None:
        if self._tpl is None:
            self.statusBar().showMessage("⚠ 无可用模板（templates/ 为空）")
            return
        self._result = scan_root(folder, self._tpl)
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
        """自动发现匹配表 → 加载 → 自动登记缺失 ID → 文件热更新监听。"""
        map_file = discover_map_file(folder)
        if map_file is None:
            self._namemap = NameMap()
            self.statusBar().showMessage("ℹ 未发现 *匹配表*.txt，列表暂只显示 ID")
            return
        self._namemap = NameMap()
        self._namemap.load(map_file)
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

    def _on_namemap_changed(self, _path: str) -> None:
        if self._namemap.path:
            self._namemap.load(self._namemap.path)
            # 文件被外部保存时部分编辑器会删旧建新，需要重新挂监听
            if not self._watcher.files() and self._namemap.path.exists():
                self._watcher.addPath(str(self._namemap.path))
            self.part_list.refresh_names()

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
        self._update_matrix(direction, None)
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
        self._show_grid()
        self._refresh_status()

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

    def _show_grid(self) -> None:
        """按当前 (组/部件) + (方向,动作) 计算各层帧序列，交给 GridView 渲染。"""
        if self._group is None and self._part is None:
            return
        direction, action = self.matrix.current()
        if not direction or not action:
            self.grid_view.show_sequence([])
            return
        if self._part is not None:
            ad = self._part.action_data(direction, action)
            layers = [ad.frames] if ad else []
            self.grid_view.show_sequence(layers)
            return
        # 组模式：按 layer_rank 排序的 parts 各取 (d,a) 帧，shadow 自动最底
        layers = []
        for p in self._group.parts:
            ad = p.action_data(direction, action)
            layers.append(ad.frames if ad else [])
        self.grid_view.show_sequence(layers)

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
            miss = sorted({a for v in self._part.missing_actions.values() for a in v})
            if miss:
                segs.append("⚠ 缺动作: " + ", ".join(miss[:4]) + (" …" if len(miss) > 4 else ""))
        elif self._group is not None and self._group.pairing_issues:
            segs.append("⚠ 配套: " + "；".join(self._group.pairing_issues[:2]))
        self.statusBar().showMessage("　·　".join(segs))

    def _on_template_changed(self, index: int) -> None:
        if 0 <= index < len(self._templates):
            self._tpl = self._templates[index]
            self.matrix.set_template(self._tpl)
            if self._result and self._result.root:
                self._on_folder_dropped(self._result.root)

    def _todo_template_editor(self) -> None:
        QMessageBox.information(self, "模板编辑器", "模板编辑器将在 M4 实现。\n当前可直接编辑 templates/*.json。")
