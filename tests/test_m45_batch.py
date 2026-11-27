# -*- coding: utf-8 -*-
"""M45 阶段 C：批量导出（整个角色 / 当前方向全部动作 / 展示合集）+ WebP。

测试数据：套装（4 个部件）× 方向 E / SE × 动作 idle(6 帧) / attack(4 帧)，
SE 方向另有一个全透明的 dead（模拟美术占位图，应被跳过）。

验证：
1. core 部分（不启动界面，且不应带入 PySide6）
   - plan_combos：按模板顺序排列；只取某个方向；单部件；特效不单独算一个组合
   - run_batch 一个动作一个文件：文件名/个数/帧数；空帧跳过并写明原因；进度回调
   - 展示合集：一个文件，帧数 = 各动作之和；尺寸统一
   - WebP：能写出、帧数/尺寸正确，半透明保留
   - 中途取消：已写的文件都是完整的（不留半截文件）
   - 单项失败不拖累后面的
   - planned_paths：导出前检查「已存在」用
2. 界面
   - 导出弹窗：范围选项的启用/灰掉、默认范围、保存目录提示、WebP 选项
   - 左栏右键 → 选中该项并按「整个角色」打开弹窗
   - BatchExportWorker + 主窗口批量流程：真的把文件写出来，报告正确

运行：python tests/test_m45_batch.py
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

from src.core import compose
from src.core.export import ExportOptions
from src.core.export.batch import (item_path, plan_combos, planned_paths, run_batch,
                                   showcase_path)
from src.core.scanner import scan_root
from src.core.template import load_templates

assert not any(m.startswith("PySide6") for m in sys.modules), "批量导出核心不应导入 PySide6"

CANVAS = (160, 160)
NAME = "501521005_女侠客"


def make_part(folder: Path, box, color, step: int = 1) -> None:
    """一个部件：E 和 SE 两个方向；idle 6 帧、attack 4 帧（同方向内帧号连续）；
    SE 另有 dead 1 帧，全透明（占位图）。"""
    for d in ("E", "SE"):
        num = 1
        acts = [("idle", 6), ("attack", 4)] + ([("dead", 1)] if d == "SE" else [])
        for a, n in acts:
            ad = folder / d / a
            ad.mkdir(parents=True)
            for k in range(n):
                img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
                if a != "dead":
                    x0, y0, x1, y1 = box
                    ImageDraw.Draw(img).rectangle([x0 + k * step, y0, x1 + k * step - 1, y1 - 1],
                                                  fill=color)
                img.save(ad / f"{num + k:04d}.png")
            num += n


def settle(app, cond, timeout=15.0):
    """一直处理界面事件，直到 cond() 为真或超时。"""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def main():
    tpl = load_templates()[0]
    tmp = Path(tempfile.mkdtemp(prefix="m45_batch_"))
    out = Path(tempfile.mkdtemp(prefix="m45_batch_out_"))          # 输出放扫描目录外面
    try:
        scan = tmp / "501521005_女侠客_部件"
        make_part(scan / "501521005_shadow", (40, 130, 90, 138), (0, 0, 0, 100))
        make_part(scan / "501521005_body", (55, 40, 85, 129), (200, 80, 60, 255))
        make_part(scan / "501031005_weapon", (30, 70, 55, 80), (220, 200, 90, 255), 2)
        make_part(scan / "501031005_shadow", (25, 130, 55, 136), (0, 0, 0, 90), 2)
        r = scan_root(scan, tpl)
        grp = r.groups[0]
        assert grp.is_outfit
        ctx = compose.ComposeContext(groups=r.groups, tpl=tpl)
        look = compose.LookConfig()

        # ---------------- 1. 导出计划 ----------------
        print("== 1. plan_combos ==")
        all_items = plan_combos(grp, look, ctx)
        assert all_items == [("E", "idle"), ("E", "attack"), ("SE", "idle"),
                             ("SE", "attack"), ("SE", "dead")], f"按模板顺序（先方向后动作）: {all_items}"
        assert plan_combos(grp, look, ctx, "E") == [("E", "idle"), ("E", "attack")]
        weapon = next(p for p in r.parts if p.name == "501031005_weapon")
        assert plan_combos(weapon, look, ctx, "SE") == [("SE", "idle"), ("SE", "attack"), ("SE", "dead")]
        print(f"  ✓ 整个角色 {len(all_items)} 个组合，按模板顺序；可按方向筛选；单部件可用")

        # ---------------- 2. 一个动作一个文件 ----------------
        print("== 2. 逐个导出 + 报告 ==")
        prog = []
        rep = run_batch(grp, all_items, look, ctx, ExportOptions(fmt="gif", fps=12), out, NAME,
                        progress=lambda i, n, label: prog.append((i, n, label)))
        assert (len(rep.ok), len(rep.skipped), len(rep.failed)) == (4, 1, 0), rep.summary()
        assert rep.skipped[0][0] == "SE · dead" and "空帧" in rep.skipped[0][1], rep.skipped
        files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        assert files == [f"{NAME}/E_attack.gif", f"{NAME}/E_idle.gif",
                         f"{NAME}/SE_attack.gif", f"{NAME}/SE_idle.gif"], files
        assert Image.open(item_path(out, NAME, "E", "idle", "gif")).n_frames == 6
        assert Image.open(item_path(out, NAME, "SE", "attack", "gif")).n_frames == 4
        assert prog[0] == (0, 5, "E · idle") and prog[-1][:2] == (5, 5), prog
        assert rep.folder == out / NAME and "跳过 SE · dead" in rep.details()
        print(f"  ✓ {rep.summary()}；空帧已跳过并写明原因；进度 {prog[0]} … {prog[-1]}")

        # ---------------- 3. 展示合集 ----------------
        print("== 3. 展示合集 ==")
        rep2 = run_batch(grp, plan_combos(grp, look, ctx, "E"), look, ctx,
                         ExportOptions(fmt="apng", fps=12), out, NAME, showcase=True)
        assert len(rep2.ok) == 1, rep2.summary()
        path, info = rep2.ok[0][1], rep2.ok[0][2]
        assert path == showcase_path(out, NAME, "E", "png") and path.name == "E_展示.png"
        assert info["frames"] == 10, f"idle 6 + attack 4 = 10 帧: {info}"
        print(f"  ✓ {path.name}：{info['frames']} 帧 {info['size']}")

        # ---------------- 4. WebP ----------------
        print("== 4. WebP ==")
        rep3 = run_batch(grp, [("E", "idle")], look, ctx,
                         ExportOptions(fmt="webp", fps=12, background=None, scale=2), out, "webp测试")
        w = Image.open(rep3.ok[0][1])
        assert w.n_frames == 6 and w.size == (rep3.ok[0][2]["size"]), (w.n_frames, w.size)
        w.seek(0)
        px = w.convert("RGBA").getpixel((0, 0))
        assert px[3] == 0, f"透明背景应保留: {px}"
        print(f"  ✓ WebP {w.n_frames} 帧 {w.size}，透明保留")

        # ---------------- 5. 中途取消不留半截文件 ----------------
        print("== 5. 中途取消 ==")
        want = {"E_idle.gif": 6, "E_attack.gif": 4, "SE_idle.gif": 6, "SE_attack.gif": 4}
        for limit in (3, 9, 15, 22):                       # 在不同位置喊停
            shutil.rmtree(out / "cancel", ignore_errors=True)
            calls = {"n": 0}

            def stop():
                calls["n"] += 1
                return calls["n"] > limit
            rep4 = run_batch(grp, all_items, look, ctx, ExportOptions(fmt="gif"), out / "cancel",
                             NAME, should_stop=stop)
            assert rep4.cancelled
            got = {p.name: Image.open(p).n_frames for p in (out / "cancel").rglob("*.gif")}
            assert all(want[k] == v for k, v in got.items()), f"取消后不应有不完整的文件: {got}"
        print("  ✓ 在 4 个不同位置取消，已写的文件帧数都完整")

        # ---------------- 6. 单项失败不拖累后面 ----------------
        print("== 6. 单项失败 ==")
        import src.core.export.batch as batch_mod
        real = batch_mod.export_frames
        calls = {"n": 0}

        def flaky(frames, path, opts):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("模拟：磁盘写入失败")
            return real(frames, path, opts)
        batch_mod.export_frames = flaky
        try:
            rep5 = run_batch(grp, all_items, look, ctx, ExportOptions(fmt="gif"), out / "flaky", NAME)
        finally:
            batch_mod.export_frames = real
        assert (len(rep5.ok), len(rep5.failed), len(rep5.skipped)) == (3, 1, 1), rep5.summary()
        assert "模拟" in rep5.failed[0][1]
        print(f"  ✓ {rep5.summary()}：第 2 个失败，其余照常导出")

        # ---------------- 7. 路径 ----------------
        assert [p.name for p in planned_paths(all_items[:2], out, NAME, "gif", False)] == \
            ["E_idle.gif", "E_attack.gif"]
        assert [p.name for p in planned_paths(all_items[:2], out, NAME, "gif", True)] == ["E_展示.gif"]
        print("== 7. planned_paths ✓ ==")

        # ---------------- 8. 界面 ----------------
        print("== 8. 界面 ==")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        import src.app as appmod
        from src.ui.export_dialog import ExportDialog
        from src.ui.export_worker import BatchExportWorker

        ini = out / "settings.ini"                                  # 设置隔离（不碰注册表）
        appmod.QSettings = lambda *a, **k: QSettings(str(ini), QSettings.IniFormat)
        settings = QSettings(str(ini), QSettings.IniFormat)
        kw = dict(name="x", subtitle="E · idle · 6 帧", layers="身体", fps=12, default_dir=out,
                  rel_path=f"{NAME}/E_idle", settings=settings, direction="E", action="idle")

        d1 = ExportDialog(None, scopes={"direction": 2, "all": 5}, **kw)
        radios = d1._scope_radios
        assert all(radios[k].isEnabled() for k in ("current", "direction", "all", "showcase"))
        assert d1.scope() == "current", "默认范围：当前动画"
        radios["all"].setChecked(True)
        assert d1.scope() == "all" and str(out / NAME) in d1.path_note.text()
        assert d1.fmt.findData("webp") >= 0
        d1.fmt.setCurrentIndex(d1.fmt.findData("webp"))
        radios["current"].setChecked(True)
        assert d1.output_path().suffix == ".webp"
        d2 = ExportDialog(None, scopes={"direction": 1, "all": 1}, **kw)
        assert d2._scope_radios["current"].isEnabled()
        assert not any(d2._scope_radios[k].isEnabled() for k in ("direction", "all", "showcase")), \
            "只有 1 个动作时，批量范围没有意义，应灰掉"
        d3 = ExportDialog(None, scopes={"direction": 2, "all": 5}, scope="all", **kw)
        assert d3.scope() == "all", "可指定默认范围（左栏右键用）"
        print("  ✓ 弹窗：范围启用/灰掉、默认范围、保存位置提示、WebP 选项")

        win = appmod.MainWindow()
        try:
            win._on_folder_dropped(scan)
            settle(app, lambda: False, 0.5)
            key = "GRP:" + grp.key

            # 左栏右键 → 选中该项 + 按「整个角色」
            asked = []
            win._on_export_requested = lambda scope="current": asked.append(scope)
            win.part_list.export_requested.emit(key)
            assert asked == ["all"], f"左栏右键应按「整个角色」打开: {asked}"
            assert win.part_list.current_key() == key, "右键的那一项应被选中"
            del win._on_export_requested

            # 主窗口批量流程（不弹报告框，直接收集报告）
            settle(app, lambda: win._group is not None, 5)
            job = win._export_job()
            assert job is not None and job["name"] == NAME, job and job["name"]
            ctx2, look2 = win._compose_ctx(), win._look_snapshot(win._group)
            items = plan_combos(job["target"], look2, ctx2)
            assert items == all_items, f"界面和 core 的导出计划应一致: {items}"
            got = {}

            def on_done(rep):
                got["rep"] = rep
                win._close_batch_progress()
            win._on_batch_done = on_done
            dest = out / "ui"
            win._start_batch_export(job, "all", items, [c for c in items if c[0] == "E"],
                                    look2, ctx2, ExportOptions(fmt="gif"), dest, tmp)
            assert settle(app, lambda: "rep" in got, 30), "批量导出应在 30 秒内结束"
            rep6 = got["rep"]
            assert (len(rep6.ok), len(rep6.skipped)) == (4, 1), rep6.summary()
            assert len(list((dest / NAME).glob("*.gif"))) == 4
            print(f"  ✓ 主窗口批量流程：{rep6.summary()}，文件已写出")

            # BatchExportWorker 直接同步跑（取消 → report.cancelled）
            reps = []
            w = BatchExportWorker(grp, items, look2, ctx2, ExportOptions(fmt="gif"),
                                  out / "w", NAME, True, False)
            w.report_ready.connect(reps.append)
            # 注意：Qt 的 requestInterruption() 只对「正在运行的线程」生效。
            # 这里是直接调用 run()（没有 start()，线程并没有真的跑起来），喊停会被 Qt 忽略。
            # 所以改成让「要不要停」永远回答「要」，模拟用户在开始前就点了取消。
            # （真实使用时是在线程运行中点「取消」，requestInterruption() 是生效的。）
            w.isInterruptionRequested = lambda: True
            w.run()
            assert reps and reps[0].cancelled and not reps[0].ok, "开始前就取消 → 不应写任何文件"
            print("  ✓ BatchExportWorker：开始前取消，不写任何文件")
        finally:
            win.close()

        assert True
        print("\nALL M45 BATCH PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
