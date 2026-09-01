# -*- coding: utf-8 -*-
"""M29：重启恢复选中后，方向/动作按钮应立即可用（无需重新点选左栏）。

Bug（2026-09-01）：_on_folder_dropped 在 load_result / _select_by_key 之后才
清空 _part/_group，把恢复选中时刚建立的状态抹掉了——界面照常显示 idle，
但点 attack/skill 无反应（_update_matrix 里 _part/_group 均为 None 直接跳过），
必须再点一次左栏子项才恢复。修复：清空挪到 load_result 之前。

附带 Qt 坑：setCurrentItem 对「已选中项」不触发 itemSelectionChanged，
故恢复目标与 load_result 自动选中的第一项相同时不会二次触发信号——
依赖 load_result 自动选中时设置的 _part，顺序正确即天然成立。

运行：python tests/test_m29_restore_selection.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from src.app import MainWindow
from src.core.template import load_templates


def make_part(root: Path, name: str, dirs=("E", "SE"), acts=("idle", "attack", "skill"), n=2):
    for d in dirs:
        for a in acts:
            dd = root / name / d / a
            dd.mkdir(parents=True)
            for i in range(1, n + 1):
                Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(dd / f"{i:04d}.png")


def click_action(w, act):
    w._on_action_selected(act)
    return w.matrix.current()


def main():
    load_templates()
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp())
    make_part(tmp, "50112151_body")
    make_part(tmp, "50112151_weapon")

    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / "settings"))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    S = lambda: QSettings("MMY", "FrameQuickView")  # 与 MainWindow 同一存储

    # 1) 首次启动（无记忆）：load_result 自动选中第一项 → 按钮直接可用
    S().clear()
    w1 = MainWindow()
    w1._on_folder_dropped(tmp)
    assert w1._part is not None, "首次启动自动选中后 _part 应非空"
    assert click_action(w1, "attack") == ("SE", "attack"), "首次启动点 attack 应生效"
    print("[1] OK 首次启动：自动选中第一项，按钮直接可用")

    tree = w1.part_list.tree
    grp = tree.topLevelItem(0)
    child0, child1 = grp.child(0), grp.child(1)

    # 2) 重启恢复第一项（目标 == 自动选中项，setCurrentItem 不触发信号的坑）
    S().setValue("last/selection", child0.data(0, Qt.UserRole))
    S().setValue("last/folder", str(tmp))
    S().sync()
    w2 = MainWindow()
    w2._on_folder_dropped(tmp)
    assert w2._part is not None, "恢复第一项后 _part 应非空"
    assert click_action(w2, "attack") == ("SE", "attack"), "恢复第一项后点 attack 应生效"
    print("[2] OK 重启恢复第一项：按钮直接可用")

    # 3) 重启恢复第二项（真正切换选中，信号路径）
    S().setValue("last/selection", child1.data(0, Qt.UserRole))
    S().sync()
    w3 = MainWindow()
    w3._on_folder_dropped(tmp)
    assert w3._part is not None and w3._part.name.endswith("_weapon"), "应恢复到 weapon"
    assert click_action(w3, "skill") == ("SE", "skill"), "恢复第二项后点 skill 应生效"
    print("[3] OK 重启恢复第二项：按钮直接可用")

    # 4) 重启恢复组头（GRP: key → 组视图）
    S().setValue("last/selection", grp.data(0, Qt.UserRole))
    S().sync()
    w4 = MainWindow()
    w4._on_folder_dropped(tmp)
    assert w4._group is not None, "恢复组头后 _group 应非空"
    assert click_action(w4, "attack")[1] == "attack", "恢复组头后点 attack 应生效"
    print("[4] OK 重启恢复组头：按钮直接可用")

    print("ALL M29 PASS")


if __name__ == "__main__":
    main()
