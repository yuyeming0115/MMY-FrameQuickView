"""模板编辑器对话框（M4）。

表单化编辑模板 JSON：
- 名称 / folder_pattern / frame_pattern / extensions
- 部位 / 方向 / 动作：可添加、删除、排序
- 层级顺序：direction → action 或 action → direction
- layer_order：定义同 ID 叠放顺序，越前越底层
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from ..core.template import Template, save_template


class _StringListEditor(QWidget):
    """带 + / - / ↑ / ↓ 的字符串列表编辑器。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel(title))

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "background:#23262a; border:1px solid #3A3F46; border-radius:4px; color:#E8E4D9;"
        )
        layout.addWidget(self.list_widget, 1)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(6)
        for text, tip, slot in (
            ("＋", "添加", self._add),
            ("－", "删除", self._remove),
            ("↑", "上移", self._up),
            ("↓", "下移", self._down),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFixedWidth(32)
            btn.clicked.connect(slot)
            btn_bar.addWidget(btn)
        btn_bar.addStretch(1)
        layout.addLayout(btn_bar)

    def set_values(self, values: list[str]) -> None:
        self.list_widget.clear()
        self.list_widget.addItems(values)

    def values(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def _add(self) -> None:
        from PySide6.QtWidgets import QListWidgetItem
        item = QListWidgetItem("新项")
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(self.list_widget.count() - 1)
        self.list_widget.editItem(item)

    def _remove(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)

    def _up(self) -> None:
        row = self.list_widget.currentRow()
        if row > 0:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row - 1, item)
            self.list_widget.setCurrentRow(row - 1)

    def _down(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < self.list_widget.count() - 1:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + 1, item)
            self.list_widget.setCurrentRow(row + 1)


class TemplateEditorDialog(QDialog):
    """编辑或新建模板。"""

    saved = Signal()

    def __init__(self, tpl: Template | None = None, parent=None):
        super().__init__(parent)
        self._original_name = tpl.name if tpl else None
        self._tpl = tpl
        self.setWindowTitle("编辑模板" if tpl else "新建模板")
        self.resize(720, 600)
        self.setStyleSheet(
            "QDialog { background: #1E2023; color: #E8E4D9; font-size: 14px; }\n"
            "QLabel { color: #96A1AD; }\n"
            "QLineEdit, QComboBox {\n"
            "  background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;\n"
            "  padding: 5px 8px; color: #E8E4D9; font-size: 14px;\n"
            "}\n"
            "QPushButton {\n"
            "  background: #2A2E33; border: 1px solid #3A3F46; border-radius: 6px;\n"
            "  padding: 5px 12px; color: #E8E4D9; font-size: 14px;\n"
            "}\n"
            "QPushButton:hover { border-color: #D4AF37; }\n"
            "QDialogButtonBox QPushButton { min-width: 70px; }\n"
        )
        self._build_ui()
        if tpl:
            self._load(tpl)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # 基本字段
        form = QVBoxLayout()
        form.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：天命装")
        row.addWidget(self.name_edit, 1)
        form.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("文件夹模式"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("{id}(_{part})?")
        row.addWidget(self.folder_edit, 1)
        form.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("帧命名正则"))
        self.frame_edit = QLineEdit()
        self.frame_edit.setPlaceholderText(r"^(\d{4})\.png$")
        row.addWidget(self.frame_edit, 1)
        row.addWidget(QLabel("扩展名"))
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText(".png")
        self.ext_edit.setMaximumWidth(120)
        row.addWidget(self.ext_edit)
        form.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("层级顺序"))
        self.hierarchy_combo = QComboBox()
        self.hierarchy_combo.addItem("方向 → 动作", ["direction", "action"])
        self.hierarchy_combo.addItem("动作 → 方向", ["action", "direction"])
        row.addWidget(self.hierarchy_combo, 1)
        form.addLayout(row)

        root.addLayout(form)

        # 列表编辑区
        lists = QSplitter()
        self.parts_editor = _StringListEditor("部位 parts")
        self.dirs_editor = _StringListEditor("方向 directions")
        self.acts_editor = _StringListEditor("动作 actions")
        self.layer_editor = _StringListEditor("叠层顺序 layer_order（越前越底层）")
        lists.addWidget(self.parts_editor)
        lists.addWidget(self.dirs_editor)
        lists.addWidget(self.acts_editor)
        lists.addWidget(self.layer_editor)
        lists.setSizes([180, 150, 220, 150])
        root.addWidget(lists, 1)

        note = QLabel(
            "提示：layer_order 决定同 ID 叠层时谁在最底层；通常把 shadow 放在第一位。"
        )
        note.setStyleSheet("color: #96A1AD; font-size: 12px;")
        note.setWordWrap(True)
        root.addWidget(note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _load(self, tpl: Template) -> None:
        self.name_edit.setText(tpl.name)
        self.folder_edit.setText(tpl.folder_pattern)
        self.frame_edit.setText(tpl.frame_pattern)
        self.ext_edit.setText(", ".join(tpl.extensions))
        idx = 0 if tpl.hierarchy == ["direction", "action"] else 1
        self.hierarchy_combo.setCurrentIndex(idx)
        self.parts_editor.set_values(tpl.parts)
        self.dirs_editor.set_values(tpl.directions)
        self.acts_editor.set_values(tpl.actions)
        self.layer_editor.set_values(tpl.layer_order)

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "保存失败", "模板名称不能为空")
            return
        parts = self.parts_editor.values()
        directions = self.dirs_editor.values()
        actions = self.acts_editor.values()
        if not all([parts, directions, actions]):
            QMessageBox.warning(self, "保存失败", "部位、方向、动作列表均不能为空")
            return

        ext_text = self.ext_edit.text().strip()
        extensions = [e.strip() for e in ext_text.replace(",", " ").split() if e.strip()]
        if not extensions:
            extensions = [".png"]

        tpl = Template(
            name=name,
            folder_pattern=self.folder_edit.text().strip() or "{id}(_{part})?",
            parts=parts,
            directions=directions,
            actions=actions,
            hierarchy=self.hierarchy_combo.currentData(),
            layer_order=self.layer_editor.values(),
            frame_pattern=self.frame_edit.text().strip() or r"^(\d{4})\.png$",
            extensions=extensions,
        )
        try:
            save_template(tpl)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"写入文件出错：{exc}")
            return
        self.saved.emit()
        self.accept()

    @classmethod
    def edit(cls, tpl: Template, parent=None) -> bool:
        dlg = cls(tpl, parent)
        return dlg.exec() == QDialog.Accepted

    @classmethod
    def create_new(cls, parent=None) -> bool:
        dlg = cls(None, parent)
        # 给新建模板一个合理默认值
        dlg.name_edit.setText("新模板")
        dlg.folder_edit.setText("{id}(_{part})?")
        dlg.frame_edit.setText(r"^(\d{4})\.png$")
        dlg.ext_edit.setText(".png")
        dlg.parts_editor.set_values(
            ["hair", "body", "weapon", "wings", "ride_front", "ride_back", "shadow", "fills"]
        )
        dlg.dirs_editor.set_values(["E", "N", "NW", "S", "SE"])
        dlg.acts_editor.set_values(
            ["idle", "run", "attack", "skill", "hurt", "block", "dead", "ride_idle", "ride_run"]
        )
        dlg.layer_editor.set_values(["shadow"])
        return dlg.exec() == QDialog.Accepted
