"""导出弹窗（M45 阶段 B/C）：选择导出范围、格式、背景、帧率、尺寸、输出目录。

弹窗只负责「让用户选」，选完用 options() / output_dir() 取结果；真正的导出在 ExportWorker 里。
格式、背景、循环、裁剪、放大这些选择会记住（QSettings 的 export/*），下次打开沿用；
帧率每次默认取预览当前的 FPS，输出目录每次默认取「扫描目录旁边的 _导出 文件夹」。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.export import ExportOptions, actual_fps

_MUTED = "color: #96A1AD;"
_WARN = "color: #E8A33D;"


class ExportDialog(QDialog):
    def __init__(self, parent, *, name: str, subtitle: str, layers: str, fps: int,
                 default_dir: Path, rel_path: str, settings: QSettings,
                 scopes: dict | None = None, scope: str = "current",
                 direction: str = "", action: str = ""):
        """
        name        资源名（弹窗标题），如「503122 · 宝箱怪」
        subtitle    当前方向/动作/帧数，如「SE · idle · 8 帧」
        layers      本次叠了哪些层（给用户导出前确认）
        fps         默认帧率（预览当前的 FPS）
        default_dir 默认输出目录
        rel_path    输出目录下的相对路径（不含扩展名），如「503122_宝箱怪/SE_idle」
        scopes      各导出范围能导出几个：{"direction": 当前方向有几个动作, "all": 整个角色有几个组合}
                    不传 = 只能导出当前动画（兼容阶段 B 的用法）
        scope       默认选中哪个范围："current" / "direction" / "all" / "showcase"
        direction / action   当前的方向和动作（范围选项的文字里要写）
        """
        super().__init__(parent)
        self.setWindowTitle("导出动画")
        self.setMinimumWidth(520)
        self._settings = settings
        self._rel_path = rel_path
        self._folder = rel_path.split("/")[0]            # 批量导出时的资源文件夹名
        self._scopes = scopes or {}

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ---- 顶部：导出的是谁 ----
        title = QLabel(name)
        title.setStyleSheet("color: #D4AF37; font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel(subtitle)
        sub.setStyleSheet(_MUTED)
        lay.addWidget(sub)
        layer_lbl = QLabel("本次叠层：" + layers)
        layer_lbl.setWordWrap(True)
        layer_lbl.setStyleSheet(_MUTED)
        layer_lbl.setToolTip("和预览里看到的完全一样（含显示层开关、穿戴的时装/武器/翅膀/特效）")
        lay.addWidget(layer_lbl)

        form = QFormLayout()
        form.setSpacing(8)

        # ---- 导出范围（阶段 C）----
        # 四个单选互斥。某个范围没有内容可导（如只有 1 个动作时「当前方向全部动作」没有意义）就灰掉。
        n_dir = int(self._scopes.get("direction", 0))
        n_all = int(self._scopes.get("all", 0))
        self._scope_radios: dict[str, QRadioButton] = {}
        scope_box = QVBoxLayout()
        scope_box.setSpacing(4)
        scope_group = QButtonGroup(self)
        for key, text, enabled in (
            ("current", f"当前动画（{direction} · {action}）", True),
            ("direction", f"当前方向全部动作（{direction}，{n_dir} 个文件）", n_dir > 1),
            ("all", f"整个角色（全部方向×动作，{n_all} 个文件）", n_all > 1),
            ("showcase", f"展示合集（{direction} 的 {n_dir} 个动作串成 1 个文件）", n_dir > 1),
        ):
            rb = QRadioButton(text)
            rb.setEnabled(enabled)
            scope_group.addButton(rb)
            scope_box.addWidget(rb)
            self._scope_radios[key] = rb
        if len(self._scopes) > 0:                       # 传了范围信息才显示这一行
            form.addRow("范围", _wrap(scope_box))
        first = self._scope_radios.get(scope)
        (first if first is not None and first.isEnabled() else self._scope_radios["current"]).setChecked(True)

        # ---- 格式 ----
        self.fmt = QComboBox()
        self.fmt.addItem("GIF（兼容性最好，聊天软件/文档都能播）", "gif")
        self.fmt.addItem("APNG（保留半透明，体积较大）", "apng")
        self.fmt.addItem("WebP（无损、保留半透明、体积最小；部分老软件不能播）", "webp")
        form.addRow("格式", self.fmt)

        # ---- 背景 ----
        self.bg = QComboBox()
        self.bg.addItem("深色（和工具里看到的一样）", "#1E2023")
        self.bg.addItem("白色", "#FFFFFF")
        self.bg.addItem("透明", "")
        form.addRow("背景", self.bg)
        self.bg_hint = QLabel()                  # GIF + 透明时提示毛边
        self.bg_hint.setWordWrap(True)
        self.bg_hint.setStyleSheet(_WARN)
        form.addRow("", self.bg_hint)

        # ---- 帧率 + 循环 ----
        fps_row = QHBoxLayout()
        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setValue(max(1, min(60, int(fps))))
        self.fps.setSuffix(" fps")
        fps_row.addWidget(self.fps)
        self.fps_note = QLabel()                 # GIF 实际帧率
        self.fps_note.setStyleSheet(_MUTED)
        fps_row.addWidget(self.fps_note)
        fps_row.addStretch(1)
        self.loop = QCheckBox("无限循环")
        fps_row.addWidget(self.loop)
        form.addRow("帧率", _wrap(fps_row))

        # ---- 尺寸 ----
        size_row = QHBoxLayout()
        self.crop = QRadioButton("按内容裁剪，留白")
        self.pad = QSpinBox()
        self.pad.setRange(0, 200)
        self.pad.setSuffix(" px")
        self.same = QRadioButton("与预览一致")
        self.same.setToolTip("不重新裁剪：按预览当前的「画布对齐」开关原样输出（画布对齐时会有大片空白）")
        grp = QButtonGroup(self)                 # 两个单选互斥
        grp.addButton(self.crop)
        grp.addButton(self.same)
        size_row.addWidget(self.crop)
        size_row.addWidget(self.pad)
        size_row.addSpacing(12)
        size_row.addWidget(self.same)
        size_row.addStretch(1)
        form.addRow("尺寸", _wrap(size_row))
        self.scale = QComboBox()
        for s in (1, 2, 3):
            self.scale.addItem(f"{s}×", s)
        self.scale.setToolTip("放大用最近邻插值：像素边缘清晰不糊，适合小怪")
        form.addRow("放大", self.scale)

        # ---- 输出目录 ----
        out_row = QHBoxLayout()
        self.dir_edit = QLineEdit(str(default_dir))
        out_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        out_row.addWidget(browse)
        form.addRow("输出到", _wrap(out_row))
        self.path_note = QLabel()                # 「将保存为：…」
        self.path_note.setWordWrap(True)
        self.path_note.setStyleSheet(_MUTED)
        form.addRow("", self.path_note)
        lay.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导出")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._restore()
        # 任何选项变了都刷新提示文字
        for rb in self._scope_radios.values():
            rb.toggled.connect(self._refresh)
        for w in (self.fmt, self.bg, self.scale):
            w.currentIndexChanged.connect(self._refresh)
        self.fps.valueChanged.connect(self._refresh)
        self.crop.toggled.connect(self._refresh)
        self.dir_edit.textChanged.connect(self._refresh)
        self._refresh()

    # ---------------- 记住 / 恢复选项 ----------------
    def _restore(self) -> None:
        s = self._settings
        _select(self.fmt, s.value("export/fmt", "gif", type=str))
        _select(self.bg, s.value("export/background", "#1E2023", type=str))
        _select(self.scale, s.value("export/scale", 1, type=int))
        self.loop.setChecked(s.value("export/loop", True, type=bool))
        self.pad.setValue(s.value("export/padding", 8, type=int))
        (self.crop if s.value("export/crop", True, type=bool) else self.same).setChecked(True)

    def _save(self) -> None:
        s = self._settings
        s.setValue("export/fmt", self.fmt.currentData())
        s.setValue("export/background", self.bg.currentData())
        s.setValue("export/scale", self.scale.currentData())
        s.setValue("export/loop", self.loop.isChecked())
        s.setValue("export/padding", self.pad.value())
        s.setValue("export/crop", self.crop.isChecked())
        s.sync()

    # ---------------- 界面联动 ----------------
    def _refresh(self) -> None:
        fmt = self.fmt.currentData()
        transparent = self.bg.currentData() == ""
        if fmt == "gif" and transparent:
            self.bg_hint.setText("GIF 只有「全透明 / 不透明」两档：影子、发光等半透明边缘会有毛边。"
                                 "需要保留半透明请选 APNG。")
            self.bg_hint.show()
        else:
            self.bg_hint.hide()
        if fmt == "gif":
            real = actual_fps("gif", self.fps.value())
            self.fps_note.setText(f"实际 ≈ {real:g} fps（GIF 帧时长只能按 10 毫秒取整）"
                                  if abs(real - self.fps.value()) > 0.05 else "")
        else:
            self.fps_note.setText("")
        self.pad.setEnabled(self.crop.isChecked())
        scope = self.scope()
        if scope == "current":
            self.path_note.setText(f"将保存为：{self.output_path()}")
        else:                                           # 批量：写到「输出目录/资源名/」文件夹里
            n = {"direction": self._scopes.get("direction", 0), "all": self._scopes.get("all", 0),
                 "showcase": 1}[scope]
            self.path_note.setText(f"将保存到文件夹：{self.output_dir() / self._folder}\n"
                                   f"共 {n} 个文件（空帧占位图会自动跳过并在完成后列出）")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择导出目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def accept(self) -> None:
        if not self.dir_edit.text().strip():
            QMessageBox.warning(self, "导出动画", "请先选择输出目录")
            return
        self._save()
        super().accept()

    # ---------------- 给调用方取结果 ----------------
    def options(self) -> ExportOptions:
        bg = self.bg.currentData()
        return ExportOptions(
            fmt=self.fmt.currentData(),
            fps=self.fps.value(),
            loop=self.loop.isChecked(),
            background=bg or None,                       # "" → None（透明）
            crop_padding=self.pad.value() if self.crop.isChecked() else None,
            scale=int(self.scale.currentData()),
        )

    def scope(self) -> str:
        """选中的导出范围："current" / "direction" / "all" / "showcase"。"""
        for key, rb in self._scope_radios.items():
            if rb.isChecked():
                return key
        return "current"

    def output_dir(self) -> Path:
        return Path(self.dir_edit.text().strip())

    def output_path(self) -> Path:
        """完整输出文件路径 = 输出目录 / 资源名 / 方向_动作.扩展名"""
        return self.output_dir() / f"{self._rel_path}.{self.options().ext}"


def _wrap(layout) -> QWidget:
    """QFormLayout 的一行只能放一个控件，把一组横排控件包成一个。"""
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


def _select(combo: QComboBox, data) -> None:
    """按数据值选中下拉项；找不到就保持第一项。"""
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)
