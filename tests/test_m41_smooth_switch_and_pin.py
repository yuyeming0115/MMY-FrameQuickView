# -*- coding: utf-8 -*-
"""M41：切换丝滑化 + 快捷文件夹「📌 常驻」。

验证链路（offscreen）：
A. A 区网格切换不再跳动
   1. 连续切换左栏组 → A 区宽度恒定（原 bug：minimumSizeHint 随格子尺寸浮动，
      横向 splitter 每次切换重排 441↔496）
   2. 每次切换只重建一次格子控件（原 bug：resize 又触发 2 次重建）
   3. 布局签名未变时走「就地换像素」，不销毁控件
B. 快捷文件夹常驻
   4. 设为常驻 → 进 _quick_pins、置顶带 📌、不占最近名额
   5. 再拖 6 个新目录 → 常驻项不被挤掉（原 bug：收藏上限 8 会被淘汰）
   6. 顶栏「📌 常驻」按钮状态随当前目录同步；点按钮可切换
   7. 取消常驻 → 回到最近最前
   8. 旧键 folders/favs 自动迁移为 folders/pins

运行：python tests/test_m41_smooth_switch_and_pin.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import src.app as appmod
from src.app import MainWindow
from src.core.template import load_templates


def make_part(root: Path, name: str, n=4):
    dd = root / name / "E" / "idle"
    dd.mkdir(parents=True)
    for i in range(1, n + 1):
        # 每帧颜色不同，便于肉眼/程序区分内容确实换过
        Image.new("RGBA", (24, 24), (i * 20 % 255, 40, 60, 255)).save(dd / f"{i:04d}.png")


def chips(dz) -> list[str]:
    lay = dz._chips_layout
    return [lay.itemAt(i).widget().text() for i in range(lay.count() - 1)]


def pump(app, ms=400):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        app.processEvents()
        time.sleep(0.004)


def main():
    load_templates()
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp())
    # 必须是模板认识的部位后缀（_body）才会被识别为独立 ID 组
    names = [f"5011215{i}_body" for i in range(1, 9)]
    for n in names:
        make_part(tmp, n)
    a = tmp / names[0]

    # 隔离 QSettings：本机 PySide6 的 QSettings(org, app) 总是落到注册表，
    # 直接测试会读写/清空用户真实配置——这里统一重定向到临时 ini 文件。
    # 注意：ini 必须放在被扫描目录**之外**——否则每次 QSettings 写入都会
    # 触发目录监听 → 自动重扫 → 再写设置，形成重扫风暴（测试假象）。
    ini = str(Path(tempfile.mkdtemp()) / "app.ini")
    S = lambda: QSettings(ini, QSettings.Format.IniFormat)
    appmod.QSettings = lambda *a, **k: QSettings(ini, QSettings.Format.IniFormat)
    S().clear()

    w = MainWindow()
    w.resize(1500, 900)
    w.show()
    app.processEvents()
    w._on_folder_dropped(tmp)
    pump(app, 400)

    gv = w.grid_view
    panes = w._panes
    assert gv.minimumWidth() >= 240, "A 区应有固定最小宽度（防 splitter 重排）"

    # ---- A1/A2：切换时 A 区宽度恒定、每次只重建一次 ----
    builds: list[str] = []
    _build = gv._build_cells
    def build(force=False):
        builds.append("force" if force else "soft")
        _build(force)
    gv._build_cells = build
    inplace: list[int] = []
    _refresh = gv._refresh_cells_in_place
    def refresh():
        inplace.append(1)
        _refresh()
    gv._refresh_cells_in_place = refresh

    widths = []
    groups = [g.key for g in w._result.groups][:5]
    assert len(groups) >= 3, f"应扫出多组: {groups}"
    for k in groups:
        builds.clear()
        w.part_list._select_by_key("GRP:" + k)
        pump(app, 500)
        widths.append(gv.width())
        assert len(gv._cells) > 0, f"{k} 应有帧格"
        assert builds.count("force") == 1, f"{k} 每次切换应只重建一次: {builds}"
        assert "soft" not in builds, f"{k} resize 不应再触发重建: {builds}"
    assert len(set(widths)) == 1, f"A 区宽度不应随切换变化: {widths}（panes={panes.sizes()}）"
    print(f"[A1] OK 切换 {len(groups)} 组，A 区宽度恒定 {widths[0]}px，每次仅 1 次重建")

    # ---- A3：签名不变 → 就地刷新，不销毁控件 ----
    before = [id(c) for c in gv._cells]
    builds.clear()
    inplace.clear()
    gv._build_cells()                    # 非 force，签名未变
    assert inplace, "签名未变应走就地刷新而非重建"
    assert [id(c) for c in gv._cells] == before, "就地刷新不应重建控件"
    print("[A3] OK 布局签名未变 → 就地换像素，控件不重建")

    # ---- B4：设为常驻 ----
    b = tmp / names[1]
    w._on_folder_dropped(b)
    w._on_quick_pin_toggled(b, True)
    assert w._quick_pins == [str(b)] and str(b) not in w._quick_recents
    assert chips(w.drop)[0] == "📌 " + b.name, f"常驻 chip 应置顶带 📌: {chips(w.drop)}"
    assert w.drop._pin_btn.isChecked(), "当前目录常驻时顶栏按钮应为选中态"

    # ---- B5：再拖 6 个新目录，常驻不被挤掉 ----
    for n in names[2:]:
        w._on_folder_dropped(tmp / n)
    assert str(b) in w._quick_pins, f"常驻不应被新历史挤掉: {w._quick_pins}"
    assert len(w._quick_recents) == w.QUICK_RECENT_MAX, "最近仍按上限滚动"
    assert chips(w.drop)[0] == "📌 " + b.name, "常驻 chip 应始终排最前"
    print(f"[B5] OK 连拖 6 个新目录后常驻仍在最前，最近={len(w._quick_recents)} 个")

    # ---- B6：按钮状态随当前目录同步 ----
    w._on_folder_dropped(tmp / names[3])
    assert not w.drop._pin_btn.isChecked(), "非常驻目录按钮应为未选中"
    assert w.drop._pin_btn.isEnabled(), "有当前目录时按钮应可用"
    w._on_folder_dropped(b)
    assert w.drop._pin_btn.isChecked(), "切回常驻目录按钮应恢复选中"
    w.drop._pin_btn.click()                     # 点按钮 → 取消常驻
    pump(app, 50)
    assert str(b) not in w._quick_pins and w._quick_recents[0] == str(b), \
        "点按钮取消常驻后应回到最近最前"
    print("[B6] OK 「📌 常驻」按钮状态随当前目录同步，点击可取消")

    # ---- B7：重启恢复 + 旧键迁移 ----
    w2 = MainWindow()
    assert str(b) in w2._quick_recents and w2._quick_pins == [], "重启应恢复最近/常驻"
    S().setValue("folders/favs", [str(a)])
    S().remove("folders/pins")
    S().sync()
    w3 = MainWindow()
    assert w3._quick_pins == [str(a)], f"旧 folders/favs 应迁移为常驻: {w3._quick_pins}"
    assert str(a) not in w3._quick_recents, "迁移项不应同时留在最近"
    print("[B7] OK 重启恢复 + 旧 folders/favs 自动迁移为常驻")

    print("✅ M41 切换丝滑化 + 常驻文件夹 全部用例通过")
    S().clear()


if __name__ == "__main__":
    main()
