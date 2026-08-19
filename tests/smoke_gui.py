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

from PySide6.QtWidgets import QApplication            # noqa: E402

from src.app import MainWindow                        # noqa: E402

REAL_ROOT = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图")


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
        # 按钮矩阵状态
        d, a = win.matrix.current()
        print(f"[按钮] 当前 方向={d} 动作={a}")
    else:
        print(f"[跳过] 真实目录不存在: {REAL_ROOT}")

    print("GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
