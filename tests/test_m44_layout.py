# -*- coding: utf-8 -*-
"""M44 阶段 2a：右栏检查器（方向/动作按钮 + 显示层/穿戴 从 B 区搬到最右一列）。

验证：
1. 中间区域变成 A | B | 右栏 三段，第三段是检查器
2. 按钮矩阵、显示层面板都在右栏里；B 区左边的按钮列、底栏「☰ 显示层」按钮已隐藏
3. 右栏里方向列和动作列左右并排、顶部对齐
4. 选中组：显示层面板可见、占位提示隐藏、面板高度 = 内容高度（最下面的按钮不被截）
5. 选中单个部件：显示层面板隐藏、占位提示出现
6. 点右栏「显示层 / 穿戴」标题折叠 ↔ 原「显示层」开关同步，设置照旧记在 display/layer_panel
7. 「方向 × 动作」区块折叠状态记在 inspector/matrix_open，下次打开恢复
8. 旧版本只存了 A/B 两段宽度（layout/panes）→ 新版本补成三段，不报错
9. 在右栏按方向键：右栏不处理（交给主窗口切方向/动作），不会去滚动右栏

运行：python tests/test_m44_layout.py
（设置隔离到临时 ini，不碰注册表里的真实配置）
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication


def mk_seq(folder: Path, n: int = 8) -> None:
    """生成 n 帧 4×4 的红色小图（只要能扫描、能叠层即可）。"""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(folder / f"{i:04d}.png")


def settle(app, win) -> None:
    """处理事件，并等 A/B 区后台解码线程结束（同其他 GUI 测试）。"""
    for _ in range(100):
        app.processEvents()
        busy = [w for w in (win.grid_view._worker, win.anim_view._worker)
                if w is not None and w.isRunning()]
        if not busy:
            break
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


def new_window(appmod, app):
    win = appmod.MainWindow()
    win.resize(1400, 860)
    win.show()
    app.processEvents()
    return win


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m44_layout_"))       # 资源目录
    ini_dir = Path(tempfile.mkdtemp(prefix="m44_layout_ini_"))  # 设置文件放资源目录外面
    ini = ini_dir / "settings.ini"
    try:
        for folder in ("50103101_body", "50103101_shadow", "503122"):
            mk_seq(tmp / folder / "SE" / "idle")
        app = QApplication.instance() or QApplication([])
        import src.app as appmod
        appmod.QSettings = lambda *a, **k: QSettings(str(ini), QSettings.IniFormat)
        from src.ui.inspector import Inspector

        win = new_window(appmod, app)
        try:
            av = win.anim_view

            # ---------------- 1/2. 三段 + 搬家 ----------------
            print("== 1/2. 三段布局，按钮和显示层在右栏 ==")
            assert win._panes.count() == 3, f"中间应为 A | B | 右栏 三段: {win._panes.count()}"
            assert isinstance(win._panes.widget(2), Inspector) and win._panes.widget(2) is win.inspector
            assert win.inspector.isAncestorOf(win.matrix), "按钮矩阵应在右栏里"
            assert win.inspector.isAncestorOf(av._toggles), "显示层面板应在右栏里"
            assert not av.isAncestorOf(av._toggles), "显示层面板不应再浮在 B 区画布上"
            assert av._matrix_container.isHidden(), "B 区左边的按钮列应隐藏"
            assert av._layer_btn.isHidden(), "底栏「☰ 显示层」按钮应隐藏（右栏区块标题代替）"
            assert av._hud.parent() is av._stack_host, "HUD 本阶段仍留在 B 区画布右下角"
            print(f"  ✓ 三段宽度 {win._panes.sizes()}")
            # B 区变窄后：底栏每个控件都要放得下自己的文字（不被截断）；棋盘格、HUD 已挪到标题行。
            # 播放/上一帧/下一帧是固定 44px 的图标按钮（M44.1 起），不能用 sizeHint 比：
            # sizeHint 包含全局样式里的左右内边距（各 12px），比文字本身宽得多。改为直接量文字宽度。
            for btn in (av._play_btn, av._prev_btn, av._next_btn):
                need = btn.fontMetrics().horizontalAdvance(btn.text()) + 8    # 文字宽 + 边框/少量留白
                assert btn.width() >= need, \
                    f"图标按钮放不下文字: {btn.text()!r} 宽 {btn.width()} < 需要 {need}"
            chk = av._loop_chk                                              # 「循环」不是固定宽度，按 sizeHint 比
            assert chk.width() >= chk.sizeHint().width(), \
                f"「循环」被挤窄: 宽 {chk.width()} < 需要 {chk.sizeHint().width()}"
            assert av.isAncestorOf(av._checker_chk) and av.isAncestorOf(av._hud_btn)
            assert av._checker_chk.y() < av._canvas.mapTo(av, QPoint(0, 0)).y(), "棋盘格应在标题行（画布上方）"
            print("  ✓ 底栏控件不被截断；棋盘格、HUD 在标题行")

            # ---------------- 3. 方向/动作并排 ----------------
            print("== 3. 方向列和动作列左右并排 ==")
            d, a = win.matrix.dir_stack, win.matrix.act_stack
            assert a.x() > d.x() + d.width() // 2, f"动作列应在方向列右边: dir x={d.x()} act x={a.x()}"
            assert abs(a.y() - d.y()) <= 2, f"两列应顶部对齐: dir y={d.y()} act y={a.y()}"
            print(f"  ✓ 方向列 x={d.x()} 宽 {d.width()}，动作列 x={a.x()} 宽 {a.width()}")

            # ---------------- 4. 选中组 ----------------
            win._tpl.outfit_merge_max = 2          # 小型测试树：不合并成套装（同 test_m37）
            win._on_folder_dropped(tmp)
            settle(app, win)
            win.part_list._select_by_key("GRP:50103101")
            settle(app, win)
            print("== 4. 选中组：显示层可见 ==")
            tog, ph = av._toggles, av._dock_placeholder
            assert tog.isVisible() and not ph.isVisible(), "组视图：显示层可见、占位提示隐藏"
            assert len(tog._items) == 2, f"body + shadow 两个开关: {len(tog._items)}"
            assert tog.height() == tog.content_height(), \
                f"面板高度应等于内容高度（不截掉按钮）: {tog.height()} != {tog.content_height()}"
            last = tog._items[-1][1]
            bottom = last.mapTo(tog, QPoint(0, last.height())).y()
            assert bottom <= tog.height(), "最后一个开关按钮应完整显示"
            print(f"  ✓ {len(tog._items)} 个开关，面板高 {tog.height()}px")

            # ---------------- 5. 选中单个部件 ----------------
            print("== 5. 选中单个部件：占位提示 ==")
            part = next(p for p in win._result.parts if p.name == "50103101_body")
            win._on_part_selected(part)
            settle(app, win)
            assert not tog.isVisible() and ph.isVisible(), "单部件：显示层隐藏、占位提示出现"
            print(f"  ✓ 占位提示：{ph.text().splitlines()[0]}")

            # ---------------- 6. 区块折叠 ↔ 显示层开关 ----------------
            print("== 6. 「显示层 / 穿戴」折叠 ↔ 显示层开关 ==")
            # 注意：第 5 步是直接调用 _on_part_selected 切到单部件的，左栏的选中项其实没变
            #（还是这个组）。这时再用 _select_by_key 选同一个组，左栏「选中项没变」不会发出信号，
            # 组视图不会被重新打开。所以这里直接调用 _on_group_selected 回到组视图。
            win._on_group_selected("50103101")
            settle(app, win)
            assert tog.isVisible(), "回到组视图后显示层应可见"
            sec = win._layer_section
            assert sec.is_expanded() and av._layer_btn.isChecked()
            sec.header.click()                                   # 用户点标题折叠
            app.processEvents()
            assert not av._layer_btn.isChecked(), "折叠区块应关掉显示层开关"
            assert not tog.isVisible() and not sec.body.isVisible()
            assert win._settings.value("display/layer_panel", True, type=bool) is False
            av._layer_btn.setChecked(True)                       # 反过来：开关打开 → 区块展开
            app.processEvents()
            assert sec.is_expanded() and tog.isVisible(), "打开开关应展开区块"
            print("  ✓ 双向同步，设置记在 display/layer_panel")

            # ---------------- 7. 矩阵区块折叠记忆 ----------------
            print("== 7. 「方向 × 动作」折叠记忆 ==")
            win._matrix_section.header.click()
            app.processEvents()
            assert not win._matrix_section.body.isVisible()
            assert win._settings.value("inspector/matrix_open", True, type=bool) is False

            # ---------------- 9. 方向键不被右栏吃掉 ----------------
            print("== 9. 右栏不处理方向键 ==")
            for key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.NoModifier)
                win.inspector.keyPressEvent(ev)
                assert not ev.isAccepted(), f"右栏应放行方向键 {key}"
            print("  ✓ ←→↑↓ 都交给主窗口")
        finally:
            win.close()

        # 重开：折叠状态恢复
        win2 = new_window(appmod, app)
        try:
            assert not win2._matrix_section.is_expanded(), "「方向 × 动作」折叠状态应恢复"
            print("  ✓ 重开后「方向 × 动作」仍是折叠的")
        finally:
            win2.close()

        # ---------------- 8. 旧版本两段宽度 ----------------
        print("== 8. 旧版本 layout/panes 只有两段 ==")
        s = QSettings(str(ini), QSettings.IniFormat)
        s.setValue("layout/panes", [500, 420])
        s.setValue("inspector/matrix_open", True)
        s.sync()
        win3 = new_window(appmod, app)
        try:
            sizes = win3._panes.sizes()
            assert len(sizes) == 3 and all(x > 0 for x in sizes), f"应补成三段: {sizes}"
            assert sizes[2] >= 240, f"右栏不应窄于最小宽度: {sizes}"
            print(f"  ✓ {sizes}")
        finally:
            win3.close()

        print("\nALL M44 LAYOUT (2a) PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(ini_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
