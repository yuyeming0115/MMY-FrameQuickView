# -*- coding: utf-8 -*-
"""M44.1：阶段 1 小修回归测试（不改布局的体验修复）。

验证：
1. B 区 FPS 滑杆有最小宽度（不再被挤成一个点）
2. A 区「原图 | 自适应」两段式按钮：互斥、文字固定、高亮 = 当前模式
3. A 区自适应模式固定用内容裁剪：即使画布对齐开着，缩略图也按内容并集 bbox，
   切回原图后恢复画布尺寸
4. B 区快捷键公共方法：播放/暂停、上一帧/下一帧、首帧/尾帧
5. 「A 区模式」重启恢复：注册表里存成字符串 "false" 时不再被误读为 True
6. 主窗口注册了 5 个快捷键，空格快捷键能暂停播放
7. 状态栏：单部件资源显示「单部件」，不再显示「1 层叠合 · shadow 最底」；
   多部件且有影子时仍显示「N 层叠合 · shadow 最底」
8. 显示层面板：5 个穿戴下拉全部显示时，最后一个部件按钮（如「☑ 整体」）完整不被截

运行：python tests/test_m44_quick_fixes.py
（GUI 测试，建议加 QT_QPA_PLATFORM=offscreen；本脚本已默认设置）

⚠ QSettings 隔离（见 .workbuddy-ai/memory/MEMORY.md）：
本机 QSettings 总是落注册表，直接读写会改掉你的真实配置。
这里把 app 模块里的 QSettings 换成一个临时 ini 文件，
而且 ini 放在「被扫描目录之外」，避免写设置时触发文件监听 → 重扫。
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

CANVAS = (200, 200)            # 模拟「原始渲染画布」尺寸
BOX = (80, 60, 120, 140)       # 角色内容在画布里的位置（40×80）
N_FRAMES = 8


def _mk_seq(folder: Path, n: int = N_FRAMES) -> list[Path]:
    """生成 n 帧：整张画布 200×200，中间一个 40×80 的矩形，每帧向右挪 1px（模拟动画）。

    所以全序列内容并集 = 宽 40+(n-1) = 47、高 80。
    """
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = BOX
        d.rectangle([x0 + i, y0, x1 + i - 1, y1 - 1], fill=(200, 80, 60, 255))
        p = folder / f"{i + 1:04d}.png"
        img.save(p)
        paths.append(p)
    return paths


def _wait_view(app, view) -> None:
    """等某个视图（GridView / AnimView）的后台解码线程跑完，并把排队的信号处理掉。"""
    for _ in range(200):
        app.processEvents()
        w = view._worker
        if w is None or not w.isRunning():
            break
        time.sleep(0.01)
    for _ in range(5):
        app.processEvents()


def _settle(app, win) -> None:
    """主窗口版：等 A/B 区解码线程 + 空帧抽样线程都结束（同 test_m37）。"""
    for _ in range(100):
        app.processEvents()
        wg, wa = win.grid_view._worker, win.anim_view._worker
        wb = [w for w in win._blank_workers if w.isRunning()]
        if (wg is None or not wg.isRunning()) and (wa is None or not wa.isRunning()) and not wb:
            break
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m44_scan_"))      # 被扫描的资源目录
    ini_dir = Path(tempfile.mkdtemp(prefix="m44_ini_"))   # 设置文件放在扫描目录之外
    ini = ini_dir / "settings.ini"
    try:
        app = QApplication.instance() or QApplication([])

        # 测试素材：一个单部件资源 + 一个「身体+影子」双部件资源
        frames = _mk_seq(tmp / "503122" / "SE" / "idle")
        _mk_seq(tmp / "50103101_body" / "SE" / "idle")
        _mk_seq(tmp / "50103101_shadow" / "SE" / "idle")
        (tmp / "匹配表.txt").write_text("503122\t宝箱怪\n50103101\t测试角色\n", encoding="utf-8")

        # ---------------- 1. FPS 滑杆最小宽度 ----------------
        print("== 1. FPS 滑杆最小宽度 ==")
        from src.ui.anim_view import AnimView
        av = AnimView()
        assert av._fps_slider.minimumWidth() >= 100, \
            f"FPS 滑杆应有 ≥100px 最小宽度，实际 {av._fps_slider.minimumWidth()}"
        # 逐帧按钮缩成图标后宽度固定，给滑杆和右侧按钮腾地方
        assert av._prev_btn.maximumWidth() <= 44 and av._next_btn.maximumWidth() <= 44, "逐帧按钮应为窄图标按钮"
        assert "," in av._prev_btn.toolTip() and "." in av._next_btn.toolTip(), "悬停提示应写明快捷键"
        print(f"  ✓ 最小宽度 {av._fps_slider.minimumWidth()}px")

        # ---------------- 2. 两段式按钮 ----------------
        print("== 2. A 区「原图 | 自适应」两段式 ==")
        from src.ui.grid_view import GridView
        gv = GridView()
        gv.resize(600, 500)
        gv.show()
        assert gv._orig_btn.isChecked() and not gv._mode_btn.isChecked(), "默认应为原图"
        gv._mode_btn.setChecked(True)
        assert not gv._orig_btn.isChecked(), "选中自适应后原图应自动弹起（互斥）"
        assert (gv._orig_btn.text(), gv._mode_btn.text()) == ("原图", "自适应"), \
            "按钮文字应固定不变（高亮表示当前模式）"
        gv._orig_btn.click()
        assert gv._orig_btn.isChecked() and not gv._mode_btn.isChecked(), "点原图应切回原图"
        print("  ✓ 互斥、文字固定")

        # ---------------- 3. 自适应 = 内容裁剪 ----------------
        print("== 3. 自适应模式固定内容裁剪（画布对齐开着也一样）==")
        gv.show_sequence([frames], canvas_align=True)       # 原图 + 画布对齐
        _wait_view(app, gv)
        assert (gv._max_w, gv._max_h) == CANVAS, \
            f"原图 + 画布对齐应保持整张画布 {CANVAS}，实际 {(gv._max_w, gv._max_h)}"
        gv._mode_btn.click()                                # 切到自适应 → 应重新解码
        _wait_view(app, gv)
        expect = (BOX[2] - BOX[0] + N_FRAMES - 1, BOX[3] - BOX[1])   # (47, 80)
        assert (gv._max_w, gv._max_h) == expect, \
            f"自适应应按内容并集裁剪 {expect}，实际 {(gv._max_w, gv._max_h)}"
        assert "内容裁剪" in gv._title.text(), f"标题应标注内容裁剪: {gv._title.text()}"
        gv._orig_btn.click()                                # 切回原图 → 恢复画布尺寸
        _wait_view(app, gv)
        assert (gv._max_w, gv._max_h) == CANVAS, "切回原图应恢复画布对齐尺寸"
        print(f"  ✓ 原图 {CANVAS} ↔ 自适应 {expect}")

        # ---------------- 4. B 区快捷键公共方法 ----------------
        print("== 4. B 区播放控制公共方法 ==")
        av.show_sequence([frames])
        _wait_view(app, av)
        assert av.is_playing(), "解码完成后应自动播放（原有行为）"
        av.toggle_play()
        assert not av.is_playing(), "toggle_play 应暂停"
        av.goto_edge(last=True)
        assert av.current_index() == N_FRAMES - 1, "End 应跳到最后一帧"
        av.step_frame(+1)
        assert av.current_index() == 0, "最后一帧再下一帧应回绕到第一帧"
        av.step_frame(-1)
        assert av.current_index() == N_FRAMES - 1, "第一帧再上一帧应回绕到最后一帧"
        av.goto_edge(last=False)
        assert av.current_index() == 0 and not av.is_playing(), "Home 应跳到第一帧并保持暂停"
        av.toggle_play()
        assert av.is_playing(), "再次 toggle_play 应恢复播放"
        print("  ✓ 播放/暂停、逐帧、首尾帧")

        # ---------------- 主窗口：隔离 QSettings ----------------
        import src.app as appmod
        appmod.QSettings = lambda *a, **k: QSettings(str(ini), QSettings.IniFormat)

        # ---------------- 5. A 区模式重启恢复 ----------------
        print("== 5. A 区模式重启恢复（字符串布尔值）==")
        s = QSettings(str(ini), QSettings.IniFormat)
        s.setValue("grid/fit_mode", "false")      # 模拟注册表里存的字符串
        s.sync()
        win = appmod.MainWindow()
        assert not win.grid_view._mode_btn.isChecked(), \
            '存的是 "false" 时应恢复为原图（旧版 bool("false") 误判为 True）'
        win.close()
        s.setValue("grid/fit_mode", "true")
        s.sync()
        win = appmod.MainWindow()
        assert win.grid_view._mode_btn.isChecked(), '存的是 "true" 时应恢复为自适应'
        print("  ✓ false/true 都能正确恢复")

        # ---------------- 6. 快捷键注册 ----------------
        print("== 6. 主窗口快捷键 ==")
        keys = sorted(sc.key().toString() for sc in win._shortcuts)
        assert keys == sorted([",", ".", "Ctrl+E", "End", "Home", "Space"]), f"快捷键清单不符: {keys}"  # M45 加了 Ctrl+E 导出
        print(f"  ✓ 已注册 {keys}")

        # ---------------- 7. 状态栏文案 ----------------
        print("== 7. 状态栏文案 ==")
        win.show()
        win._tpl.outfit_merge_max = 2      # 小型测试树：禁用 M23 套装合并（同 test_m37）
        win._on_folder_dropped(tmp)
        _settle(app, win)

        win.part_list._select_by_key("GRP:503122")
        _settle(app, win)
        msg = win.statusBar().currentMessage()
        assert "单部件" in msg and "层叠合" not in msg, f"单部件资源状态栏文案不符: {msg}"
        print(f"  ✓ 单部件：{msg[:40]}…")

        # 空格快捷键：此时 B 区在自动播放，触发一次应暂停
        assert win.anim_view.is_playing(), "选中资源后 B 区应在播放"
        space = next(sc for sc in win._shortcuts if sc.key().toString() == "Space")
        space.activated.emit()
        assert not win.anim_view.is_playing(), "空格快捷键应暂停播放"
        print("  ✓ 空格暂停")

        win.part_list._select_by_key("GRP:50103101")
        _settle(app, win)
        msg = win.statusBar().currentMessage()
        assert "2 层叠合 · shadow 最底" in msg, f"多部件 + 影子应提示层序: {msg}"
        print(f"  ✓ 多部件：{msg[:40]}…")

        win.close()

        # ---------------- 8. 显示层面板高度 ----------------
        print("== 8. 显示层面板高度（5 个穿戴项 + 部件按钮）==")
        from PySide6.QtCore import QPoint
        from src.ui.anim_view import PartToggles
        tog = PartToggles()
        # 模拟鳄鱼截图的情况：特效/翅膀/时装/头发/武器 5 个下拉全显示
        for lbl, combo in ((tog._fx_label, tog._fx_combo), (tog._wing_label, tog._wing_combo),
                           (tog._body_label, tog._body_combo), (tog._hair_label, tog._hair_combo),
                           (tog._weapon_label, tog._weapon_combo)):
            lbl.show()
            combo.show()
        tog.set_parts({"whole": "整体"}, set())
        h = tog.content_height()
        tog.setFixedSize(240, h)            # 和 AnimView._reposition_toggles 的用法一致
        tog.show()
        for _ in range(5):
            app.processEvents()
        btn = tog._items[-1][1]
        bottom = btn.mapTo(tog, QPoint(0, btn.height())).y()     # 按钮下边缘在面板里的位置
        assert bottom <= h, f"最后一个部件按钮应完整显示：按钮下边缘 {bottom} > 面板高 {h}"
        assert tog._scroll.verticalScrollBar().maximum() == 0, "内容应一屏放下，不需要滚动"
        print(f"  ✓ 面板高 {h}px，按钮下边缘 {bottom}px")
        tog.close()

        print("\nALL M44.1 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(ini_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
