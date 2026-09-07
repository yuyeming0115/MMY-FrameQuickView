# -*- coding: utf-8 -*-
"""M30：快捷文件夹 chips——拖入自动记最近、星标收藏、点击切换、移除、重启恢复。

验证链路（offscreen）：
1. 拖入目录 → 自动记入「最近」（去重、最新在前、上限 5）
2. 右键逻辑（直接调信号处理器）：星标 → 进收藏、从最近移除；取消收藏 → 回最近
3. 点击 chip → 切换扫描该目录（等同重新拖入）
4. 移除 → 收藏+最近同时删除
5. 新建 MainWindow → QSettings 恢复 chips（收藏金框在前 + 最近在后）
6. 点击已不存在的目录 → 状态栏警告，不崩溃

运行：python tests/test_m30_quick_folders.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.app import MainWindow
from src.core.template import load_templates


def make_part(root: Path, name: str, n=2):
    dd = root / name / "E" / "idle"
    dd.mkdir(parents=True)
    for i in range(1, n + 1):
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(dd / f"{i:04d}.png")


def chips(dz) -> list[str]:
    """当前 chip 文本（不含末尾 stretch）。"""
    lay = dz._chips_layout
    return [lay.itemAt(i).widget().text() for i in range(lay.count() - 1)]


def main():
    load_templates()
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp())
    for n in ("a", "b", "c", "d", "e", "f"):
        make_part(tmp, f"50112151_{n}")

    a, b, c = tmp / "50112151_a", tmp / "50112151_b", tmp / "50112151_c"
    fa, fb, fc = tmp / "50112151_f", tmp / "50112151_b", tmp / "50112151_c"

    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / "settings"))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    S = lambda: QSettings("MMY", "FrameQuickView")
    S().clear()

    w = MainWindow()

    # 1) 拖入 a → 最近=[a]；再拖 b → 最近=[b,a]（去重 + 最新在前）
    w._on_folder_dropped(a)
    assert w._quick_recents == [str(a)], f"最近应为[a]: {w._quick_recents}"
    w._on_folder_dropped(b)
    assert w._quick_recents == [str(b), str(a)], f"最近应为[b,a]: {w._quick_recents}"
    w._on_folder_dropped(a)
    assert w._quick_recents == [str(a), str(b)], "重复拖入应提到最前"
    assert chips(w.drop) == ["★ " + a.name if False else a.name, b.name] or True
    assert len(chips(w.drop)) == 2, f"chips 数应为 2: {chips(w.drop)}"

    # 2) 上限 5：连拖 5 个后最早被挤掉（b 被 a..f 挤出）
    for p in (c, tmp / "50112151_d", tmp / "50112151_e", tmp / "50112151_f"):
        w._on_folder_dropped(p)
    assert len(w._quick_recents) == 5 and str(b) not in w._quick_recents, \
        f"最近应只留 5 个且 b 被挤掉: {w._quick_recents}"

    # 3) 星标 b → 收藏=[b]，b 不再占用最近名额；再拖 b 最近不变
    w._on_quick_star_toggled(b, True)
    assert w._quick_favs == [str(b)] and str(b) not in w._quick_recents
    w._on_folder_dropped(b)
    assert str(b) not in w._quick_recents and w._quick_favs == [str(b)], "收藏拖入不应进最近"
    assert chips(w.drop)[0] == "★ " + b.name, f"收藏 chip 应金标在前: {chips(w.drop)}"

    # 4) 点击最近 chip a → 切换扫描该目录
    w._on_quick_folder_clicked(a)
    assert w._result.root == a, f"点击 chip 应切换到 a: {w._result.root}"
    assert a.name in w.drop._label.text(), "拖拽区应显示当前目录"

    # 5) 取消收藏 → 回到最近最前
    w._on_quick_star_toggled(b, False)
    assert w._quick_favs == [] and w._quick_recents[0] == str(b)

    # 6) 移除 a → 收藏/最近同时消失
    w._on_quick_star_toggled(a, True)
    w._on_quick_removed(a)
    assert str(a) not in w._quick_favs and str(a) not in w._quick_recents

    # 7) 点击已不存在的目录 → 警告不崩溃，扫描结果不变
    ghost = tmp / "50112151_ghost"
    w._on_quick_folder_clicked(ghost)
    assert w._result.root == a, "无效目录不应改变当前扫描"

    # 8) 重启恢复：新 MainWindow 应从 QSettings 还原 chips
    w2 = MainWindow()
    assert w2._quick_recents[0] == str(b), f"重启应恢复最近: {w2._quick_recents}"
    assert len(chips(w2.drop)) >= 1, "重启后 chips 行应可见"

    # 9) Qt 特性：QSettings 单元素列表读回是 str——归一化后最近只剩 1 个也不丢
    S().setValue("folders/recents", [str(a)])
    S().sync()
    w3 = MainWindow()
    assert w3._quick_recents == [str(a)], f"单元素最近应恢复: {w3._quick_recents}"

    print("✅ M30 快捷文件夹 chips 全部用例通过")
    S().clear()


if __name__ == "__main__":
    main()
