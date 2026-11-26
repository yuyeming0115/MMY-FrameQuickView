# -*- coding: utf-8 -*-
"""M45 阶段 B：动画导出 GIF / APNG。

验证：
1. 帧时长：GIF 按 10 毫秒取整（12fps → 80ms，实际 12.5fps），不低于 20ms；APNG 精确到毫秒
2. 完整流水线（不启动界面）：套装 → compose 叠层 → render 渲染 → export 写文件
   - GIF 深色背景：帧数、每帧时长、无限循环、尺寸 = 内容并集 + 留白、背景色正确
   - GIF 透明：透明格编号、背景透明、角色不透明、帧与帧之间没有残影
   - APNG 透明 + 2× 放大：半透明影子的透明度原样保留
   - 「与预览一致」：不裁剪，尺寸 = 原始画布
3. 文件名清洗、默认导出目录（扫描目录旁边）、是否在扫描目录里面
4. 界面（offscreen，设置隔离到临时 ini）：
   - B 区标题行有「⤓ 导出」按钮，Ctrl+E 已注册
   - _export_job 收集到的叠层和预览一致；资源名
   - 导出弹窗：选项能记住，下次打开恢复
   - ExportWorker 同步跑一遍，写出的 GIF 能正常读回

运行：python tests/test_m45_export.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from src.core import compose
from src.core.export import (ExportOptions, actual_fps, default_output_dir, export_frames,
                             frame_ms, is_inside, safe_name)
from src.core.render import SequenceRender
from src.core.scanner import scan_root
from src.core.template import load_templates

CANVAS = (160, 160)
N = 8


def make_part(folder: Path, box, color, step: int = 1) -> None:
    """一个部件：E/idle 共 8 帧，整张 160×160 画布，每帧右移 step 像素（帧帧不同）。"""
    d = folder / "E" / "idle"
    d.mkdir(parents=True)
    for i in range(N):
        img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        x0, y0, x1, y1 = box
        ImageDraw.Draw(img).rectangle([x0 + i * step, y0, x1 + i * step - 1, y1 - 1], fill=color)
        img.save(d / f"{i + 1:04d}.png")


def frames_of(stack, canvas_align=True):
    job = SequenceRender(stack.layers, flat_mask=stack.flat_mask,
                         fx_offsets=stack.fx_offsets, canvas_align=canvas_align)
    return [img for _i, img, _label in job.frames()]


def main():
    # ---------------- 1. 帧时长 ----------------
    print("== 1. 帧时长 ==")
    assert frame_ms("gif", 12) == 80 and actual_fps("gif", 12) == 12.5
    assert frame_ms("gif", 60) == 20, "GIF 每帧最少 20ms（浏览器会把更短的当 100ms 播）"
    assert frame_ms("apng", 12) == 83
    print("  ✓ GIF 12fps→80ms（实际 12.5fps），60fps→20ms；APNG 12fps→83ms")

    tpl = load_templates()[0]
    tmp = Path(tempfile.mkdtemp(prefix="m45_export_"))
    out = Path(tempfile.mkdtemp(prefix="m45_export_out_"))     # 输出放扫描目录外面
    try:
        # ---------------- 2. 完整流水线 ----------------
        print("== 2. 套装 → compose → render → export ==")
        outfit = tmp / "501521005_女侠客_部件"
        make_part(outfit / "501521005_shadow", (40, 130, 90, 138), (0, 0, 0, 100))     # 半透明影子
        make_part(outfit / "501521005_body", (55, 40, 85, 129), (200, 80, 60, 255))
        make_part(outfit / "501031005_weapon", (30, 70, 55, 80), (220, 200, 90, 255), step=2)
        make_part(outfit / "501031005_shadow", (25, 130, 55, 136), (0, 0, 0, 90), step=2)
        r = scan_root(outfit, tpl)
        grp = r.groups[0]
        assert grp.is_outfit, "测试目录应合并成套装组"
        ctx = compose.ComposeContext(groups=r.groups, tpl=tpl)
        stack = compose.build_stack(grp, "E", "idle", compose.LookConfig(), ctx)
        frames = frames_of(stack)
        assert len(frames) == N and frames[0].size == CANVAS

        # 内容并集（8 帧合起来有像素的范围）：
        #   最左 = 武器影子起点 25；最右 = 身体影子 90 + 7 帧×1px = 97
        #   最上 = 身体顶 40；最下 = 身体影子底 138
        x0, y0, x1, y1 = 25, 40, 97, 138
        pad = 8
        want = (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad)

        # GIF 深色背景
        info = export_frames(frames, out / "bg.gif", ExportOptions(fmt="gif", fps=12))
        g = Image.open(out / "bg.gif")
        assert info["size"] == want and g.size == want, f"尺寸应为内容并集+留白 {want}: {g.size}"
        assert g.n_frames == N, f"应有 {N} 帧: {g.n_frames}"
        assert g.info.get("duration") == 80 and g.info.get("loop") == 0
        assert g.convert("RGB").getpixel((0, 0)) == (0x1E, 0x20, 0x23), "背景应为深色 #1E2023"
        print(f"  ✓ GIF 深色背景 {g.size}，{g.n_frames} 帧，80ms，无限循环")

        # GIF 透明
        export_frames(frames, out / "tr.gif", ExportOptions(fmt="gif", fps=12, background=None))
        g = Image.open(out / "tr.gif")
        assert g.info.get("transparency") == 255
        f0 = g.convert("RGBA")
        assert f0.getpixel((0, 0))[3] == 0, "透明 GIF 背景应全透明"
        body_px = (55 - x0 + pad + 3, 60 - y0 + pad)                 # 第 1 帧身体里的一点
        assert f0.getpixel(body_px) == (200, 80, 60, 255), f"身体颜色应保持: {f0.getpixel(body_px)}"
        g.seek(N - 1)
        last = g.convert("RGBA")
        ghost = (55 - x0 + pad, 60 - y0 + pad)                       # 第 1 帧身体最左边，第 8 帧时已移走
        assert last.getpixel(ghost)[3] == 0, "第 8 帧不应残留第 1 帧的画面"
        print("  ✓ GIF 透明：背景透明、颜色正确、无残影")

        # APNG 透明 + 2×
        info = export_frames(frames, out / "tr.png",
                             ExportOptions(fmt="apng", fps=12, background=None, scale=2))
        a = Image.open(out / "tr.png")
        assert a.n_frames == N and a.size == (want[0] * 2, want[1] * 2)
        shadow_px = ((60 - x0 + pad) * 2, (134 - y0 + pad) * 2)     # 身体影子里的一点
        alpha = a.convert("RGBA").getpixel(shadow_px)[3]
        assert alpha == 100, f"APNG 应原样保留影子的半透明（100）: {alpha}"
        print(f"  ✓ APNG 透明 2× {a.size}，影子透明度 {alpha} 原样保留")

        # 与预览一致：不裁剪
        info = export_frames(frames, out / "same.gif", ExportOptions(crop_padding=None))
        assert info["size"] == CANVAS
        print("  ✓ 与预览一致：不裁剪，尺寸 = 原始画布")

        # ---------------- 3. 文件名 / 目录 ----------------
        print("== 3. 文件名与目录 ==")
        assert safe_name('女侠客(双刀)?:*<>|') == "女侠客(双刀)______"
        assert default_output_dir(Path("E:/美术/角色输出图")) == Path("E:/美术/角色输出图_导出")
        assert is_inside(tmp / "a" / "b", tmp) and not is_inside(out, tmp)
        print("  ✓ 文件名清洗、默认目录在扫描目录旁边、是否在扫描目录里面")

        # ---------------- 4. 界面 ----------------
        print("== 4. 界面入口（offscreen）==")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        import src.app as appmod
        ini = out / "settings.ini"                                   # 设置隔离（不碰注册表）
        appmod.QSettings = lambda *a, **k: QSettings(str(ini), QSettings.IniFormat)
        win = appmod.MainWindow()
        try:
            assert win.anim_view._export_btn.text() == "⤓ 导出"
            assert "Ctrl+E" in [sc.key().toString() for sc in win._shortcuts]
            win._on_folder_dropped(outfit)
            for _ in range(50):
                app.processEvents()
            win.part_list._select_by_key("GRP:" + grp.key)
            for _ in range(50):
                app.processEvents()
            job = win._export_job()
            assert job is not None, "选中套装后应能导出"
            assert job["stack"].part_keys == win._layers_for_current()[3], "导出的叠层应和预览一致"
            assert job["name"] == "501521005_女侠客", job["name"]
            print(f"  ✓ 按钮、Ctrl+E；叠层 {job['stack'].part_keys}；资源名 {job['name']}")

            # 弹窗：改选项 → 确定 → 新弹窗恢复
            from src.ui.export_dialog import ExportDialog
            kw = dict(name="x", subtitle="E · idle · 8 帧", layers="身体", fps=15,
                      default_dir=out, rel_path="501521005_女侠客/E_idle", settings=win._settings)
            dlg = ExportDialog(win, **kw)
            dlg.fmt.setCurrentIndex(dlg.fmt.findData("apng"))
            dlg.bg.setCurrentIndex(dlg.bg.findData(""))
            dlg.scale.setCurrentIndex(dlg.scale.findData(2))
            dlg.accept()
            dlg2 = ExportDialog(win, **kw)
            o = dlg2.options()
            assert (o.fmt, o.background, o.scale, o.fps) == ("apng", None, 2, 15), o
            assert dlg2.output_path() == out / "501521005_女侠客" / "E_idle.png"
            print("  ✓ 弹窗选项记住并恢复；输出路径", dlg2.output_path().name)

            # 后台线程：直接同步跑 run()，收集结果信号
            from src.ui.export_worker import ExportWorker
            got = {}
            path = out / "worker" / "E_idle.gif"
            w = ExportWorker(job["stack"], ExportOptions(fmt="gif", fps=12), path, True)
            w.done.connect(lambda p, info: got.update(path=p, info=info))
            w.failed.connect(lambda reason: got.update(error=reason))
            w.run()
            assert "error" not in got, got.get("error")
            assert Path(got["path"]).exists() and Image.open(got["path"]).n_frames == N
            print(f"  ✓ ExportWorker 导出 {got['info']}")
        finally:
            win.close()

        print("\nALL M45 EXPORT PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
