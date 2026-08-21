"""B 区：GIF 动画预览（M3）+ 左侧方向/动作按钮矩阵 + 底部整行控制条。

布局（M8）：
- 外层 QVBoxLayout：
    - 内嵌 QHBoxLayout：左 = ButtonMatrix 纵列 | 右 = canvas
    - 底部 = 控制条（QHBoxLayout），跨左+右整行
- 100% 原尺寸、透明无边框、并集 bbox 对齐
- 播放 / 暂停、FPS 滑杆 (1–60)、上一帧 / 下一帧、循环模式
- 默认透明融入 UI，可切换棋盘格背景（方便检查 alpha 毛边）
- A 区点击某帧 → goto_frame(idx) 跳转并暂停

显向 overlay（M8）：
- 3x3 网格（中心留给动画帧），8 个方向热区：NW/N/NE/W/E/SW/S/SE
- 沉浸式：仅 hover / 拖拽时显示该方向热区，离开画布后全部隐藏
- 点击某方向 = 触发 direction_overlay_clicked(direction)，等同按钮矩阵的方向点击
- 按住并拖拽：以画布中心为原点计算角度，按 45° 分桶连续切换方向 → 模拟 idle 旋转
- 不在当前模板 directions 内的方向（如 W/NE/SW）不响应点击/拖拽
"""
from __future__ import annotations

import math

import numpy as np
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QStackedLayout, QVBoxLayout, QWidget,
)

from .hover_scroll import enable_hover_scroll
from .worker import DecodeWorker


FPS_MIN, FPS_MAX = 1, 60

# 左侧按钮矩阵列宽度（与 button_matrix.ButtonMatrix.setFixedWidth 保持一致）
# 改这里记得同步。
LEFT_PANEL_WIDTH = 96

# 8 方向网格槽位：(方向名, (row, col))；中心 (1,1) 留空放动画
OVERLAY_SLOTS: list[tuple[str, tuple[int, int]]] = [
    ("NW", (0, 0)), ("N", (0, 1)), ("NE", (0, 2)),
    ("W",  (1, 0)),               ("E",  (1, 2)),
    ("SW", (2, 0)), ("S", (2, 1)), ("SE", (2, 2)),
]

# 屏幕 y 向下时，atan2(dy, dx) 角度 → 方向映射（按 45° 分桶，中心偏移 22.5°）
ANGLE_TO_DIR = ["E", "SE", "S", "SW", "W", "NW", "N", "NE"]


class _MatrixScrollArea(QScrollArea):
    """QScrollArea 子类：viewport 尺寸变化时同步 widget 宽度，避免 widget 比 viewport 宽被裁边。

    setWidgetResizable(True) + 在 resizeEvent 里 setFixedWidth(viewport.width()) 双保险：
    offscreen / 某些主题下 widgetResizable 不生效导致 widget 比 viewport 宽被裁边，
    这里主动同步 widget 宽度到 viewport。
    """

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = self.widget()
        if w is not None:
            vp_w = self.viewport().width()
            if vp_w > 0:
                w.setFixedWidth(vp_w)


def _img_to_pixmap(img) -> QPixmap:
    arr = np.asarray(img.convert("RGBA"))
    h, w = arr.shape[:2]
    qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class _AnimCanvas(QLabel):
    """带可选棋盘格背景 + 显向 overlay 的画布，居中绘制当前帧。"""

    direction_overlay_clicked = Signal(str)  # 点击/拖拽某方向热区时发出

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: transparent; border: none;")
        self._pixmap: QPixmap | None = None
        self._checker = False
        self._cell = 16

        # 显向 overlay 状态
        self._overlay_enabled = False          # 「显向」toggle 控制；False 时鼠标事件完全透明
        self._overlay_dirs: set[str] = set()   # 当前 tpl.directions，只响应这些方向
        self._current_dir: str | None = None   # 当前选中的方向（overlay 高亮显示）
        self._hover_dir: str | None = None     # 鼠标 hover 所在方向槽位（None = 未 hover 到任何槽）
        self._drag_origin: QPointF | None = None
        self._drag_last_dir: str | None = None
        self.setMouseTracking(False)           # 由 _on_overlay_enabled_changed 按需开启

    # ---------------- 配置 API ----------------
    def set_checker(self, enabled: bool) -> None:
        self._checker = enabled
        self.update()

    def set_dir_overlay_enabled(self, enabled: bool) -> None:
        """「显向」toggle：True 时鼠标 hover/拖拽可看到方向热区，False 时画布完全透明。"""
        self._overlay_enabled = enabled
        self.setMouseTracking(enabled)
        if not enabled:
            self._hover_dir = None
            self._drag_origin = None
            self._drag_last_dir = None
        self.update()

    def set_available_dirs(self, dirs: set[str]) -> None:
        """同步当前模板 directions：只响应这些方向的热区点击/拖拽。"""
        self._overlay_dirs = set(dirs)
        self.update()

    def set_current_dir(self, direction: str | None) -> None:
        """同步当前选中方向：overlay 中该方向以金色高亮。"""
        self._current_dir = direction
        self.update()

    def set_frame(self, pix: QPixmap | None) -> None:
        self._pixmap = pix
        self.update()

    # ---------------- 内部：角度 ↔ 方向 ----------------
    @staticmethod
    def _angle_to_dir(angle_deg: float) -> str:
        """把 atan2(dy, dx) 角度（0=右、90=下、180=左、270=上）映射到 8 方向。"""
        idx = int(((angle_deg + 22.5) % 360) / 45) % 8
        return ANGLE_TO_DIR[idx]

    def _slot_dir_at(self, pos: QPointF) -> str | None:
        """鼠标位置 → 3x3 网格槽位对应的方向；中心槽返回 None。"""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        col = int(pos.x() / w * 3)
        row = int(pos.y() / h * 3)
        if row == 1 and col == 1:
            return None
        for d, (r, c) in OVERLAY_SLOTS:
            if (r, c) == (row, col):
                return d
        return None

    def _slot_rect(self, direction: str) -> QRectF | None:
        """方向 → 3x3 网格中该槽位的 QRectF。"""
        for d, (r, c) in OVERLAY_SLOTS:
            if d == direction:
                w, h = self.width(), self.height()
                return QRectF(w / 3 * c, h / 3 * r, w / 3, h / 3)
        return None

    # ---------------- 事件 ----------------
    def enterEvent(self, event) -> None:
        if self._overlay_enabled:
            self.setMouseTracking(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_dir = None
        self._drag_origin = None
        self._drag_last_dir = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._overlay_enabled and event.button() == Qt.LeftButton:
            pos = event.position()
            self._drag_origin = pos
            # 拖拽起始角度
            center = QPointF(self.width() / 2, self.height() / 2)
            dx = pos.x() - center.x()
            dy = pos.y() - center.y()
            if dx * dx + dy * dy > 1e-6:
                angle = math.degrees(math.atan2(dy, dx))
                self._drag_last_dir = self._angle_to_dir(angle)
            else:
                self._drag_last_dir = None
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._overlay_enabled:
            super().mouseMoveEvent(event)
            return
        pos = event.position()
        if self._drag_origin is not None:
            # 拖拽模式：以画布中心为原点算角度
            center = QPointF(self.width() / 2, self.height() / 2)
            dx = pos.x() - center.x()
            dy = pos.y() - center.y()
            # 拖拽距离足够才响应（防抖）
            if dx * dx + dy * dy > 36:  # >6px
                angle = math.degrees(math.atan2(dy, dx))
                new_dir = self._angle_to_dir(angle)
                if new_dir != self._drag_last_dir and new_dir in self._overlay_dirs:
                    self.direction_overlay_clicked.emit(new_dir)
                    self._drag_last_dir = new_dir
                self._hover_dir = self._drag_last_dir
                self.update()
        else:
            # hover 模式
            new_hover = self._slot_dir_at(pos)
            if new_hover != self._hover_dir:
                self._hover_dir = new_hover
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._overlay_enabled and event.button() == Qt.LeftButton:
            pos = event.position()
            if self._drag_origin is not None:
                # 区分「点击」与「拖拽」：位移 < 8px 视为点击
                dx = pos.x() - self._drag_origin.x()
                dy = pos.y() - self._drag_origin.y()
                if dx * dx + dy * dy < 64:
                    slot = self._slot_dir_at(pos)
                    if slot and slot in self._overlay_dirs:
                        self.direction_overlay_clicked.emit(slot)
            self._drag_origin = None
            self._drag_last_dir = None
            self.update()
        super().mouseReleaseEvent(event)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if self._checker:
            w, h = self.width(), self.height()
            c1, c2 = QColor(40, 40, 40), QColor(60, 60, 60)
            for y in range(0, h, self._cell):
                for x in range(0, w, self._cell):
                    brush = QBrush(c1 if ((x // self._cell) + (y // self._cell)) % 2 == 0 else c2)
                    painter.fillRect(x, y, self._cell, self._cell, brush)

        if self._pixmap is not None:
            x = (self.width() - self._pixmap.width()) // 2
            y = (self.height() - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)

        # 显向 overlay：仅在「显向」开启 且 (hover 或 拖拽) 时绘制
        if self._overlay_enabled and (self._hover_dir or self._drag_origin is not None):
            painter.setRenderHint(QPainter.Antialiasing, True)
            self._paint_dir_overlay(painter)

        painter.end()

    def _paint_dir_overlay(self, painter: QPainter) -> None:
        """绘制 3x3 方向热区（半透明虚线框 + 方向文字）；hover 或拖拽经过的方向更高亮。"""
        w, h = self.width(), self.height()
        font = painter.font()
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)

        active_dir = self._drag_last_dir or self._hover_dir

        for d, (r, c) in OVERLAY_SLOTS:
            rect = self._slot_rect(d)
            if rect is None:
                continue
            is_active = (d == active_dir)
            is_avail = d in self._overlay_dirs
            is_current = (d == self._current_dir)

            # 背景填充：当前/hover 状态决定透明度
            if is_active:
                bg = QColor(212, 175, 55, 70)          # 金色高亮（hover/拖拽）
            elif is_current:
                bg = QColor(212, 175, 55, 40)          # 金色弱高亮（当前方向）
            else:
                bg = QColor(150, 161, 173, 18)         # 极弱灰背景
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

            # 边框：虚线灰/实线金
            pen = QPen()
            if is_active or is_current:
                pen.setColor(QColor(212, 175, 55, 200))
                pen.setStyle(Qt.SolidLine)
                pen.setWidth(2)
            else:
                pen.setColor(QColor(150, 161, 173, 110 if is_avail else 50))
                pen.setStyle(Qt.DashLine)
                pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

            # 文字：方向名；不可用方向灰显
            if is_active or is_current:
                text_color = QColor(232, 228, 217, 240)
            elif is_avail:
                text_color = QColor(232, 228, 217, 160)
            else:
                text_color = QColor(90, 99, 110, 110)
            painter.setPen(QPen(text_color))
            painter.drawText(rect, Qt.AlignCenter, d)


class PartToggles(QFrame):
    """B 区右侧悬浮的逐部件显隐 toggle 列表（组视图）。

    每部件一行（部件名 + 勾选态），点击某行切换该层显隐；按 layer_order
    从底到顶排列。默认全部可见，发出的 toggled(part, visible) 由上层负责过滤。

    按钮列表放在滚动区内：部件多 / B 区较矮时滚动而非裁剪或挤压；
    高度上限由 AnimView._reposition_toggles 按宿主可用高度动态设置。
    """

    toggled = Signal(str, bool)   # (part 名, 是否可见)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "PartToggles, PartToggles QFrame, PartToggles QWidget { background: rgba(30,32,35,210); border: none; }"
        )
        self.setAutoFillBackground(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        title = QLabel("显示层")
        title.setStyleSheet("color: #96A1AD; font-size: 11px; letter-spacing: 1px;")
        outer.addWidget(title)

        # 按钮列表滚动区：高度受限时出现滚动条，按钮不再溢出裁剪
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
        )
        enable_hover_scroll(self._scroll)
        list_host = QWidget()
        list_host.setStyleSheet("background: transparent;")
        self._list = QVBoxLayout(list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        self._list.addStretch(1)          # 按钮顶部对齐，余量留白
        self._scroll.setWidget(list_host)
        outer.addWidget(self._scroll, 1)

        self._items: list[tuple[str, QPushButton]] = []
        self._visible: dict[str, bool] = {}

    def set_parts(self, parts: dict[str, str], hidden: set[str]) -> None:
        """parts = {part 名: 中文名}；hidden = 当前应隐藏的 part 集合。

        清旧按钮时先 hide 再延迟销毁：removeWidget 只解除布局管理，控件
        仍原地绘制（deleteLater 要等事件循环），快速切换组时新旧按钮会层叠。
        """
        for _, btn in self._items:
            self._list.removeWidget(btn)
            btn.hide()
            btn.deleteLater()
        self._items.clear()
        self._visible.clear()

        for part, cn in parts.items():
            is_hidden = part in hidden
            btn = QPushButton(f"{'☑' if not is_hidden else '☐'} {cn or part}")
            btn.setCheckable(True)
            btn.setChecked(not is_hidden)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                "QPushButton { background: #2A2E33; border: 1px solid #3A3F46; border-radius: 4px;"
                " color: #96A1AD; padding: 2px 8px; font-size: 12px; text-align: left; }"
                "QPushButton:checked { color: #D4AF37; border-color: #D4AF37; }"
                "QPushButton:hover { border-color: #D4AF37; }"
            )
            btn.clicked.connect(lambda _, p=part, b=btn: self._on_clicked(p, b))
            self._list.addWidget(btn)
            self._items.append((part, btn))
            self._visible[part] = not is_hidden

    def _on_clicked(self, part: str, btn: QPushButton) -> None:
        visible = btn.isChecked()
        self._visible[part] = visible
        btn.setText(f"{'☑' if visible else '☐'} " + btn.text().split(" ", 1)[-1])
        self.toggled.emit(part, visible)


class AnimView(QFrame):
    frame_clicked = Signal(int)  # B 区点击当前帧时发出（与 A 区保持一致）
    direction_overlay_clicked = Signal(str)  # 画布内点击/拖拽某方向热区时发出（等同按钮矩阵的方向点击）

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # ---- 上半：标题行 + 叠加层（canvas 铺底，按钮矩阵浮于其上）----
        self._title = QLabel("B · GIF 动画预览（并集 bbox · 防抖动）")
        self._title.setStyleSheet("color: #96A1AD; font-size: 12px; letter-spacing: 1px;")
        outer.addWidget(self._title)

        # 叠加容器：QStackedLayout(StackAll) 让 canvas 铺满，按钮矩阵浮于其左上
        stack_host = QWidget()
        self._stack_host = stack_host
        stack_host.setStyleSheet("background: transparent;")
        stack = QStackedLayout(stack_host)
        stack.setStackingMode(QStackedLayout.StackAll)

        # 底层：canvas 铺满整个区域
        self._canvas = _AnimCanvas()
        self._canvas.direction_overlay_clicked.connect(self.direction_overlay_clicked)
        stack.addWidget(self._canvas)

        # 上层：按钮矩阵容器（透明底，靠左悬浮）
        self._matrix_container = QFrame()
        self._matrix_container.setAutoFillBackground(False)
        self._matrix_container.setStyleSheet(
            "QFrame, QWidget { background: transparent; border: none; }"
        )
        ml = QVBoxLayout(self._matrix_container)
        ml.setContentsMargins(8, 4, 0, 0)
        ml.setSpacing(0)
        self._matrix_container.setFixedWidth(LEFT_PANEL_WIDTH)
        self._matrix_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)

        self._matrix_scroll = _MatrixScrollArea()
        self._matrix_scroll.setWidgetResizable(True)
        self._matrix_scroll.setFrameShape(QFrame.NoFrame)
        self._matrix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._matrix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        enable_hover_scroll(self._matrix_scroll)
        self._matrix_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
        )
        self._matrix_scroll.viewport().setAutoFillBackground(False)
        ml.addWidget(self._matrix_scroll)
        stack.addWidget(self._matrix_container)
        # 按钮矩阵在上层，透出下层 canvas
        self._matrix_container.raise_()

        # 上层：右侧逐部件显隐 toggle（组视图用）。
        # ⚠ 不能放进 stack：StackAll 模式下每个页都铺满全区，透明容器会整面
        # 拦截鼠标事件（左侧按钮矩阵/画布 hover 全部失效）。改为 stack_host 的
        # 手动定位悬浮子控件——不进布局，只占自身尺寸，其余区域点击不受影响。
        self._toggles = PartToggles(stack_host)
        self._toggles.hide()
        stack_host.installEventFilter(self)

        outer.addWidget(stack_host, 1)

        # ---- 底部整行：控制条 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.setCheckable(True)
        self._play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self._play_btn)

        self._prev_btn = QPushButton("◀ 上一帧")
        self._prev_btn.clicked.connect(self._prev_frame)
        bar.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一帧 ▶")
        self._next_btn.clicked.connect(self._next_frame)
        bar.addWidget(self._next_btn)

        bar.addWidget(QLabel("FPS"))
        self._fps_slider = QSlider(Qt.Horizontal)
        self._fps_slider.setRange(FPS_MIN, FPS_MAX)
        self._fps_slider.setValue(12)
        self._fps_slider.setMaximumWidth(120)
        self._fps_slider.valueChanged.connect(self._on_fps_changed)
        bar.addWidget(self._fps_slider)
        self._fps_label = QLabel("12")
        self._fps_label.setStyleSheet("color: #D4AF37; min-width: 20px;")
        bar.addWidget(self._fps_label)

        self._loop_chk = QCheckBox("循环")
        self._loop_chk.setChecked(True)
        self._loop_chk.setStyleSheet("color: #96A1AD;")
        bar.addWidget(self._loop_chk)

        self._checker_chk = QCheckBox("棋盘格")
        self._checker_chk.setChecked(False)
        self._checker_chk.setStyleSheet("color: #96A1AD;")
        self._checker_chk.toggled.connect(self._canvas.set_checker)
        bar.addWidget(self._checker_chk)

        bar.addStretch(1)
        outer.addLayout(bar)

        # 状态
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self._worker: DecodeWorker | None = None
        self._frames: list[QPixmap] = []
        self._labels: list[str] = []
        self._index = 0
        self._fps = 12
        self._on_fps_changed(12)
        self._pending_frames: list[QPixmap] = []   # 新序列解码中缓存（整体替换用）
        self._pending_labels: list[str] = []

    # ---------------- 公共 API ----------------
    def set_matrix_widget(self, widget) -> None:
        """注入方向/动作按钮矩阵（ButtonMatrix 实例），显示在 B 区左侧 QScrollArea 内。

        不主动 setFixedWidth——交给 QScrollArea 的 widgetResizable=True 自动对齐 viewport 宽度；
        按钮矩阵高度超过 B 区时由垂直滚动条接管，避免按钮被裁切。
        """
        old = self._matrix_scroll.takeWidget()
        if old is not None:
            old.setParent(None)
        self._matrix_scroll.setWidget(widget)

    def set_dir_overlay_enabled(self, enabled: bool) -> None:
        """「显向」toggle：开 = 画布响应 hover/拖拽方向热区；关 = 画布完全透明。"""
        self._canvas.set_dir_overlay_enabled(enabled)

    def set_available_dirs(self, dirs: set[str]) -> None:
        """同步模板 directions：限制 overlay 只响应这些方向。"""
        self._canvas.set_available_dirs(dirs)

    def set_current_dir(self, direction: str | None) -> None:
        """同步当前选中方向（overlay 金色高亮）。"""
        self._canvas.set_current_dir(direction)

    def show_part_toggles(self, parts: dict[str, str], hidden: set[str]) -> None:
        """组视图：显示右侧逐部件显隐 toggle。parts = {part 名: 中文名}。"""
        self._toggles.set_parts(parts, hidden)
        self._toggles.show()
        self._reposition_toggles()

    def hide_part_toggles(self) -> None:
        """单部件视图/无选中：隐藏右侧 toggle 列表。"""
        self._toggles.hide()

    def _reposition_toggles(self) -> None:
        """显示层靠右垂直居中；高度超出宿主时限制并交由内部滚动。"""
        host, tog = self._stack_host, self._toggles
        btns = [b for _, b in tog._items]
        n = len(btns)
        if n:
            # 尺寸手动按内容计算：QScrollArea 的 sizeHint 在按钮刚加入时
            # 不会自动失效（拿到的是过期小尺寸），不能依赖 adjustSize。
            w = max(b.sizeHint().width() for b in btns) + 16 + 10  # 边距 + 滚动条余量
            h = 6 + 18 + 4 + n * 24 + (n - 1) * 4 + 6             # 标题 + 按钮行
            tog.setFixedSize(w, h)
        # 高度上限：超出宿主则收窄到宿主高度，内部滚动接管
        max_h = host.height() - 8
        if n and tog.height() > max_h and max_h > 80:
            tog.setMaximumHeight(max_h)
        x = max(LEFT_PANEL_WIDTH + 8, host.width() - tog.width() - 8)
        y = max(4, (host.height() - tog.height()) // 2)
        tog.move(x, y)
        tog.raise_()

    def eventFilter(self, obj, event) -> bool:
        """stack_host 尺寸变化（splitter 拖动/窗口缩放）时同步 toggle 位置。"""
        if obj is self._stack_host and event.type() == QEvent.Type.Resize:
            self._reposition_toggles()
        return super().eventFilter(obj, event)

    def part_toggles_signal(self):
        """暴露 toggled 信号供上层连接。"""
        return self._toggles.toggled

    def show_sequence(self, layers: list[list[Path]], start_idx: int = 0) -> None:
        """layers[0] 为最底层；多层即同 ID 叠层合成。

        切换体验（M6）：**延迟清空 + 整体替换**——保留旧动画继续播放直到
        新序列全部解码完成，避免切换瞬间画布闪黑。
        """
        self._stop_worker()
        if not layers or all(len(layer) == 0 for layer in layers):
            self._frames = []
            self._labels = []
            self._index = 0
            self._canvas.set_frame(None)
            self._play_btn.setChecked(False)
            self._play_btn.setText("▶ 播放")
            self._timer.stop()
            self._title.setText("B · GIF 动画预览（无帧）")
            return

        self._title.setText("B · GIF 动画预览 · 解码中…")
        self._pending_frames = []
        self._pending_labels = []
        self._worker = DecodeWorker(layers)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def goto_frame(self, idx: int) -> None:
        """跳转到指定帧并暂停，方便对照 A 区细节。"""
        self._pause()
        self._set_index(idx)

    # ---------------- 内部 ----------------
    def _stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        self._worker = None

    def _on_frame(self, idx: int, img, label: str) -> None:
        # sender() 竞态防护：旧 worker 残留的 queued 信号直接忽略
        if self._worker is None or self.sender() is not self._worker:
            return
        self._pending_frames.append(_img_to_pixmap(img))
        self._pending_labels.append(label)

    def _on_done(self, total: int) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        self._frames = self._pending_frames
        self._labels = self._pending_labels
        self._pending_frames = []
        self._pending_labels = []
        self._title.setText(f"B · GIF 动画预览（{total} 帧 · 并集 bbox · 防抖动）")
        if not self._frames:
            return
        self._index = 0
        self._canvas.set_frame(self._frames[0])
        self._play_btn.setChecked(True)
        self._play_btn.setText("⏸ 暂停")
        self._timer.start()

    def _toggle_play(self, checked: bool) -> None:
        if not self._frames:
            self._play_btn.setChecked(False)
            return
        if checked:
            self._timer.start()
            self._play_btn.setText("⏸ 暂停")
        else:
            self._timer.stop()
            self._play_btn.setText("▶ 播放")

    def _pause(self) -> None:
        self._play_btn.setChecked(False)
        self._timer.stop()
        self._play_btn.setText("▶ 播放")

    def _advance(self) -> None:
        if not self._frames:
            return
        next_idx = self._index + 1
        if next_idx >= len(self._frames):
            if self._loop_chk.isChecked():
                next_idx = 0
            else:
                self._pause()
                return
        self._set_index(next_idx)

    def _prev_frame(self) -> None:
        if not self._frames:
            return
        self._pause()
        self._set_index((self._index - 1) % len(self._frames))

    def _next_frame(self) -> None:
        if not self._frames:
            return
        self._pause()
        self._set_index((self._index + 1) % len(self._frames))

    def _set_index(self, idx: int) -> None:
        self._index = max(0, min(idx, len(self._frames) - 1))
        self._canvas.set_frame(self._frames[self._index])

    def _on_fps_changed(self, value: int) -> None:
        self._fps = max(FPS_MIN, min(FPS_MAX, value))
        self._fps_label.setText(str(self._fps))
        self._timer.setInterval(int(1000 / self._fps))
