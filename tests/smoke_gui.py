"""GUI 冒烟测试（offscreen，无显示器）：实例化主窗口并模拟拖入真实目录。

用法: tests\\smoke_gui.py   （需 .venv 中的 PySide6）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, Qt                       # noqa: E402
from PySide6.QtGui import QKeyEvent                         # noqa: E402
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLineEdit,                     # noqa: E402
)

from src.app import MainWindow                              # noqa: E402

REAL_ROOT = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图")


def _wait_workers(win) -> None:
    """等 GridView / AnimView 后台解码线程结束（offscreen 下事件驱动）。

    注意：processEvents 在队列空时立即返回（不消耗墙钟时间），worker 线程
    得不到运行机会；必须先用阻塞 wait() 给线程真实时间，再 processEvents
    投递排队信号（frame_ready/finished），否则会读到「解码中」的中间态。
    """
    for _ in range(20):
        wg = win.grid_view._worker
        wa = win.anim_view._worker
        done = (wg is None or not wg.isRunning()) and (wa is None or not wa.isRunning())
        if done:
            break
        if wg is not None and wg.isRunning():
            wg.wait(500)
        if wa is not None and wa.isRunning():
            wa.wait(500)
    for _ in range(10):
        QApplication.processEvents()


def main() -> int:
    app = QApplication([])
    win = MainWindow()
    win.show()
    print(f"[窗口] 标题={win.windowTitle()} 模板数={len(win._templates)}")

    # 模拟拖入真实父目录（内部会：扫描→发现匹配表→自动登记→列表加载）
    if REAL_ROOT.is_dir():
        win._on_folder_dropped(REAL_ROOT)
        tree = win.part_list.tree
        n_grp = tree.topLevelItemCount()
        first_grp = tree.topLevelItem(0)
        print(f"[列表] ID组数={n_grp} 首组='{first_grp.text(0)}' 子项={first_grp.childCount()}")
        if first_grp.childCount():
            print(f"[列表] 首部件='{first_grp.child(0).text(0)}'")
        print(f"[状态栏] {win.statusBar().currentMessage()}")
        print(f"[匹配表] {win._namemap.path}")
        assert n_grp > 0, "部件列表为空"
        d, a = win.matrix.current()
        print(f"[按钮] 当前 方向={d} 动作={a}")

        # ---- M2：组叠层 + 单部件网格渲染 ----
        grp = win._result.groups[0]
        win._on_group_selected(grp.res_id)
        _wait_workers(win)
        cells_grp = len(win.grid_view._cells)
        print(f"[M2组] {grp.res_id}: grid cells={cells_grp}")
        assert cells_grp > 0, "组模式网格为空"

        part = win._result.parts[0]
        win._on_part_selected(part)
        _wait_workers(win)
        cells_part = len(win.grid_view._cells)
        frames_part = len(win.anim_view._frames)
        print(f"[M2部件] {part.name}: grid cells={cells_part}")
        print(f"[M3动画] {part.name}: anim frames={frames_part}")
        assert cells_part > 0, "部件模式网格为空"
        assert frames_part == cells_part, f"A/B 区帧数不一致: {frames_part} != {cells_part}"

        # ---- M3：A 区点击跳 B 区 ----
        win.grid_view.frame_clicked.emit(0)
        _wait_workers(win)
        print(f"[M3联动] 点击 A 区第 0 帧 → B 区 index={win.anim_view._index}")
        assert win.anim_view._index == 0, "A/B 联动跳转失败"

        # ---- M5：方向切换保持动作 ----
        d0, a0 = win.matrix.current()
        other = [d for d in win._tpl.directions if d != d0]
        if other:
            win._on_direction_selected(other[0])
            _wait_workers(win)
            d1, a1 = win.matrix.current()
            print(f"[M5保持] 方向 {d0}→{other[0]}: 动作 {a0}→{a1}")
            # 仅当 a0 在新方向下存在时断言保持（缺失时由兜底逻辑接管）
            if a0 and a0 in part.available_actions(other[0]):
                assert a1 == a0, f"切换方向后动作未保持: {a1} != {a0}"
            # 复位到 a0（兜底后可能已变，以当前为准）
            _, cur_a = win.matrix.current()
            if cur_a:
                win._on_action_selected(cur_a)

        # ---- M5：键盘导航（左右切方向 / 上下切动作，模板循环） ----
        full_part = next(
            (p for p in win._result.parts
             if set(p.available_directions()) == set(win._tpl.directions)),
            None,
        )
        if full_part is not None:
            win._on_part_selected(full_part)
            _wait_workers(win)
            dirs, acts = win._tpl.directions, win._tpl.actions
            d_before, a_before = win.matrix.current()

            # 模拟用户点击过按钮：焦点落在方向按钮上（offscreen 默认焦点在模板下拉框，
            # 会被输入控件保护逻辑拦截，故显式设焦点）
            btn = win.matrix.dir_stack._buttons.get(d_before)
            if btn is not None:
                btn.setFocus()
            assert not isinstance(win.focusWidget(), (QLineEdit, QComboBox)), \
                "焦点未落在按钮上，键盘导航被拦截"

            # → 右方向键：方向前进一格（模板循环）
            win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Right, Qt.NoModifier))
            _wait_workers(win)
            d_right, a_after = win.matrix.current()
            exp_d = dirs[(dirs.index(d_before) + 1) % len(dirs)]
            print(f"[M5键盘→] 方向 {d_before}→{d_right}（期望 {exp_d}）动作保持 {a_after}")
            assert d_right == exp_d, f"右方向键切换失败: {d_right} != {exp_d}"
            assert a_after == a_before, f"键盘切方向后动作未保持: {a_after} != {a_before}"

            # ↑ 上方向键：动作后退一格（模板循环）
            win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Up, Qt.NoModifier))
            _wait_workers(win)
            _, a_up = win.matrix.current()
            exp_a = acts[(acts.index(a_before) - 1) % len(acts)]
            print(f"[M5键盘↑] 动作 {a_before}→{a_up}（期望 {exp_a}）")
            assert a_up == exp_a, f"上方向键切换失败: {a_up} != {exp_a}"

            # ← 左方向键：方向后退一格
            win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Left, Qt.NoModifier))
            _wait_workers(win)
            d_left, _ = win.matrix.current()
            exp_dl = dirs[(dirs.index(d_right) - 1) % len(dirs)]
            print(f"[M5键盘←] 方向 {d_right}→{d_left}（期望 {exp_dl}）")
            assert d_left == exp_dl, f"左方向键切换失败: {d_left} != {exp_dl}"
        else:
            print("[跳过] 未找到覆盖全部方向的部件，键盘导航断言跳过")

        # ---- M6：切换延迟清空（保留旧画面直到新序列就绪） ----
        cells_before = len(win.grid_view._cells)
        frames_before = len(win.anim_view._frames)
        assert cells_before > 0 and frames_before > 0, "M6 前置：AB 区应有旧内容"
        _, a_now = win.matrix.current()
        # 触发动作切换（新 worker 启动，但不应立即清空旧内容）
        win._on_action_selected(
            win._tpl.actions[(win._tpl.actions.index(a_now) + 1) % len(win._tpl.actions)]
        )
        # 不等待 worker：立即断言旧内容仍在（无闪空/闪黑）
        assert len(win.grid_view._cells) == cells_before, \
            f"M6 切换瞬间网格被提前清空: {len(win.grid_view._cells)} != {cells_before}"
        assert len(win.anim_view._frames) == frames_before, \
            f"M6 切换瞬间动画被提前清空: {len(win.anim_view._frames)} != {frames_before}"
        print(f"[M6延迟] 切换瞬间 A区 cells={len(win.grid_view._cells)} / B区 frames={len(win.anim_view._frames)} 均保留（延迟替换）")
        _wait_workers(win)
        assert len(win.grid_view._cells) > 0 and len(win.anim_view._frames) > 0, "M6 新序列替换失败"
        print(f"[M6完成] 解码完成后整体替换: cells={len(win.grid_view._cells)} frames={len(win.anim_view._frames)}")
    else:
        print(f"[跳过] 真实目录不存在: {REAL_ROOT}")

    win.anim_view._timer.stop()
    print("GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
