# -*- coding: utf-8 -*-
"""M43：多余动作识别（「多」角标）与安全清理（移入回收站）。

覆盖：
1. part_rule_type：主角/坐骑/翅膀 shadow 继承所属组不误判 ride_idle/ride_run；flat 特效豁免
2. part_extra_actions：A 类规则外（E/N/S attack）+ B 类模板外未知（E/dance）+ flat 豁免
3. action_disk_paths：散帧正/反层级目录 + M40 图集成对 .plist/.png
4. move_to_trash：去重、失败隔离（返回 False / 抛异常 / 假 True）、成功校验（注入临时移走函数）
5. GUI（offscreen MainWindow）：
   - 50121103_hair 的 E/N/S attack 与 E/dance 标为 extra="true"（琥珀角标），缺失的 E/skill 保持 unexpected="true"
   - 50122103_shadow 单部件视图不误标红 missing；坐骑 shadow 无论组视图、单部件视图均不误标 extra
   - apply_blank 保留非空多余动作的琥珀「多」角标，并将全透明多余动作转为红「空」
   - CleanupConfirmDialog 全选/反选过滤与禁用状态
   - _execute_trash_items 解绑 _dir_watcher、停掉 _rescan_timer、保留回收站提示、自动重扫刷新
   - 图集端到端清理与最后一项删除后的视图清空

运行：QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/test_m43_cleanup_extra_actions.py
"""
import os
import plistlib
import shutil
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
from src.core.cleanup import (
    action_disk_paths,
    collect_extra_cleanup_items,
    move_to_trash,
    part_extra_actions,
    part_rule_type,
)
from src.core.scanner import scan_part, scan_root
from src.core.template import Template, load_templates
from src.ui.cleanup_dialog import CleanupConfirmDialog

TPL = next(t for t in load_templates() if t.name == "天命装")


def _mk_frames(folder: Path, n: int = 4, alpha: int = 255) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        Image.new("RGBA", (8, 8), (180, 120, 60, alpha)).save(folder / f"{i:04d}.png")


def _mk_atlas_pair(root: Path, stem: str) -> tuple[Path, Path]:
    plist_p = root / f"{stem}.plist"
    png_p = root / f"{stem}.png"
    with open(plist_p, "wb") as f:
        plistlib.dump(
            {
                "frames": {
                    "0001.png": {
                        "aliases": [],
                        "spriteOffset": "{0,0}",
                        "spriteSize": "{4,4}",
                        "spriteSourceSize": "{8,8}",
                        "textureRect": "{{0,0},{4,4}}",
                        "textureRotated": False,
                    }
                },
                "metadata": {"format": 3},
            },
            f,
        )
    Image.new("RGBA", (8, 8), (200, 80, 80, 255)).save(png_p)
    return plist_p, png_p


def _settle(app, win) -> None:
    for _ in range(60):
        app.processEvents()
        wg, wa = win.grid_view._worker, win.anim_view._worker
        wb = [w for w in win._blank_workers if w.isRunning()]
        if (wg is None or not wg.isRunning()) and (wa is None or not wa.isRunning()) and not wb:
            break
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="m43_"))
    trash_dir = tmp / "_trash"
    trash_dir.mkdir(parents=True)
    counter = [0]

    def fake_trash(p: Path) -> bool:
        counter[0] += 1
        dst = trash_dir / f"{counter[0]}_{p.name}"
        shutil.move(str(p), str(dst))
        return True

    try:
        print("== 1. 核心单元测试：part_rule_type / part_extra_actions / action_disk_paths ==")
        res_dir = tmp / "res"
        res_dir.mkdir()
        (res_dir / "匹配表.txt").write_text(
            "50121103\t归元·男「斧」发\n50122103\t归元·男「斧」时装\n50302301\t开明兽「坐骑」\n",
            encoding="utf-8",
        )
        # 50121103_hair：正常 E/idle, E/run, E/ride_idle, SE/attack + 多余 E/N/S attack + 未知 E/dance（透明空帧测试用）
        for d in ("E", "N", "S"):
            _mk_frames(res_dir / "50121103_hair" / d / "idle", 2)
            _mk_frames(res_dir / "50121103_hair" / d / "run", 2)
            _mk_frames(res_dir / "50121103_hair" / d / "ride_idle", 2)
            _mk_frames(res_dir / "50121103_hair" / d / "attack", 3)
        _mk_frames(res_dir / "50121103_hair" / "E" / "dance", 5, alpha=0)
        _mk_frames(res_dir / "50121103_hair" / "SE" / "idle", 2)
        _mk_frames(res_dir / "50121103_hair" / "SE" / "attack", 2)

        # 50122103_body + 50122103_shadow（含正常 E/ride_idle）
        _mk_frames(res_dir / "50122103_body" / "E" / "idle", 2)
        _mk_frames(res_dir / "50122103_body" / "E" / "ride_idle", 2)
        _mk_frames(res_dir / "50122103_shadow" / "E" / "idle", 2)
        _mk_frames(res_dir / "50122103_shadow" / "E" / "ride_idle", 2)

        # 坐骑组 50302301_ride_front + 50302301_shadow（shadow.character_type == non_protagonist，但所属组为 mount）
        _mk_frames(res_dir / "50302301_ride_front" / "E" / "ride_idle", 2)
        _mk_frames(res_dir / "50302301_ride_front" / "E" / "ride_run", 2)
        _mk_frames(res_dir / "50302301_shadow" / "E" / "ride_idle", 2)
        _mk_frames(res_dir / "50302301_shadow" / "E" / "ride_run", 2)

        # 扁平特效 50105101
        fx_dir = res_dir / "50105101"
        fx_dir.mkdir()
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(fx_dir / "glow_1.png")

        scan = scan_root(res_dir, TPL)
        p_hair = next(p for p in scan.parts if p.name == "50121103_hair")
        p_shadow = next(p for p in scan.parts if p.name == "50122103_shadow")
        p_mshadow = next(p for p in scan.parts if p.name == "50302301_shadow")
        p_fx = next(p for p in scan.parts if p.is_flat)
        g_22 = next(g for g in scan.groups if g.res_id == "50122103")
        g_mount = next(g for g in scan.groups if g.res_id == "50302301")

        assert part_rule_type(p_shadow, g_22) == "protagonist", \
            f"shadow 应继承组类型 protagonist: {part_rule_type(p_shadow, g_22)}"
        assert part_rule_type(p_mshadow, g_mount) == "mount", \
            f"坐骑 shadow 应继承组类型 mount: {part_rule_type(p_mshadow, g_mount)}"
        assert part_rule_type(p_fx) == "", "flat 特效应豁免"
        assert part_extra_actions(p_shadow, TPL, g_22) == {}, \
            f"主角 shadow 的 ride_idle 不应误判多余: {part_extra_actions(p_shadow, TPL, g_22)}"
        assert part_extra_actions(p_mshadow, TPL, g_mount) == {}, \
            f"坐骑 shadow 的 ride_idle/ride_run 不应误判多余: {part_extra_actions(p_mshadow, TPL, g_mount)}"
        assert part_extra_actions(p_fx, TPL) == {}, "flat 特效不应产生多余动作"

        extras_hair = part_extra_actions(p_hair, TPL)
        assert extras_hair == {"E": ["attack", "dance"], "N": ["attack"], "S": ["attack"]}, \
            f"50121103_hair 多余动作集合不符: {extras_hair}"
        assert action_disk_paths(p_hair, "E", "attack", TPL) == (res_dir / "50121103_hair" / "E" / "attack",)
        assert action_disk_paths(p_hair, "E", "dance", TPL) == (res_dir / "50121103_hair" / "E" / "dance",)

        # 反层级（action -> direction）路径解析
        rev_tpl = Template.from_dict({**TPL.to_dict(), "hierarchy": ["action", "direction"]})
        rev_dir = tmp / "rev" / "50121103_hair"
        _mk_frames(rev_dir / "attack" / "E", 2)
        _mk_frames(rev_dir / "idle" / "E", 2)
        p_rev = scan_part(rev_dir, rev_tpl)
        assert p_rev is not None
        assert action_disk_paths(p_rev, "E", "attack", rev_tpl) == (rev_dir / "attack" / "E",)

        # 图集路径解析（含 2 个正常动作 + 2 个多余动作，清理多余后仍保持图集目录结构）
        atlas_dir = tmp / "atlas"
        atlas_dir.mkdir()
        _mk_atlas_pair(atlas_dir, "50121103_hair_E_idle")
        _mk_atlas_pair(atlas_dir, "50121103_hair_E_run")
        _mk_atlas_pair(atlas_dir, "50121103_hair_E_attack")
        _mk_atlas_pair(atlas_dir, "50121103_hair_E_dance")
        atlas_scan = scan_root(atlas_dir, TPL)
        p_atl = atlas_scan.parts[0]
        atl_paths = action_disk_paths(p_atl, "E", "attack", TPL)
        assert atl_paths == (atlas_dir / "50121103_hair_E_attack.plist", atlas_dir / "50121103_hair_E_attack.png"), \
            f"图集应成对返回 plist+png: {atl_paths}"
        atl_items = collect_extra_cleanup_items([p_atl], TPL)
        assert [(it.direction, it.action) for it in atl_items] == [("E", "attack"), ("E", "dance")], \
            f"图集多余动作清单: {atl_items}"
        print("  ✓ 单元测试全部通过")

        print("== 2. move_to_trash 去重与失败隔离 ==")
        f_a = tmp / "t1.txt"
        f_b = tmp / "t2.txt"
        f_c = tmp / "t3.txt"
        f_d = tmp / "t4.txt"
        for f in (f_a, f_b, f_c, f_d):
            f.write_text("ok", encoding="utf-8")

        def tricky_trash(path: Path) -> bool:
            if path == f_b:
                return False           # 返回 False
            if path == f_c:
                raise PermissionError("busy")  # 抛异常
            if path == f_d:
                return True            # 假返回 True 但文件仍在磁盘
            return fake_trash(path)

        moved, failed = move_to_trash([f_a, f_a, f_b, f_c, f_d, tmp / "missing.txt"], trash_fn=tricky_trash)
        assert moved == [f_a] and len(failed) == 4 and not f_a.exists() and f_b.exists() and f_d.exists()
        print("  ✓ move_to_trash 去重与各类失败隔离正确")

        print("== 3. GUI（offscreen）：按钮矩阵「多」标、shadow 防误标与清理弹窗 ==")
        ini_path = tmp / "settings.ini"
        orig_qsettings = appmod.QSettings
        appmod.QSettings = lambda *a, **k: QSettings(str(ini_path), QSettings.IniFormat)
        win = None
        try:
            win = appmod.MainWindow()
            win.show()
            win._on_folder_dropped(res_dir)
            app.processEvents()

            # 选中单部件 50121103_hair 并切到 E 方向
            win.part_list._select_by_key(str(res_dir / "50121103_hair"))
            _settle(app, win)
            win.matrix.direction_selected.emit("E")
            _settle(app, win)

            btns = win.matrix.act_stack._buttons
            assert bool(btns["attack"].property("extra")) is True, "E/attack 应标为 extra=true"
            assert "多" in btns["attack"]._badge.text(), f"E/attack 角标应含「多」: {btns['attack']._badge.text()}"
            assert bool(btns["dance"].property("extra")) is True, "未知动作 E/dance 应标为 extra=true"
            assert "多" in btns["dance"]._badge.text(), f"E/dance 角标应含「多」: {btns['dance']._badge.text()}"
            assert bool(btns["skill"].property("unexpected")) is True, "缺失的 E/skill 仍应保持 unexpected=true"
            assert not btns["idle"].property("extra"), "正常 E/idle 不应标 extra"

            # apply_blank：非空多余动作保留琥珀「多」，全透明规则外多余动作变为红「空」，恢复后回到「多」
            win.matrix.apply_blank({("E", "idle")})
            assert "多" in btns["attack"]._badge.text(), "apply_blank 不应洗掉 E/attack 的「多」角标"
            assert btns["dance"]._badge.text() == "多", "未知动作 E/dance 应保持「多」角标"
            win.matrix.apply_blank({("E", "attack")})
            assert btns["attack"]._badge.text() == "空", f"全透明多余动作应显示红「空」: {btns['attack']._badge.text()}"
            win.matrix.apply_blank(set())
            assert "多" in btns["attack"]._badge.text(), "清空空帧标记后 E/attack 应恢复「多」角标"

            # 50122103_shadow 单部件视图：E/ride_idle 不标 extra，且缺失的 run/ride_run 不误标红 missing
            win.part_list._select_by_key(str(res_dir / "50122103_shadow"))
            _settle(app, win)
            win.matrix.direction_selected.emit("E")
            _settle(app, win)
            s_btns = win.matrix.act_stack._buttons
            assert not s_btns["ride_idle"].property("extra"), "主角 shadow 的 ride_idle 不应标为 extra"
            assert not s_btns["run"].property("missing"), "shadow 不应单独标红 missing"
            assert not s_btns["ride_run"].property("missing"), "shadow 不应单独标红 missing"

            # 50302301_shadow（坐骑影子）：组视图与单部件视图均不误标 ride_idle/ride_run 为 extra
            win.part_list._select_by_key("GRP:50302301")
            _settle(app, win)
            assert not win.matrix.act_stack._buttons["ride_idle"].property("extra")
            win.part_list._select_by_key(str(res_dir / "50302301_shadow"))
            _settle(app, win)
            assert not win.matrix.act_stack._buttons["ride_idle"].property("extra"), \
                "坐骑 shadow 单部件视图不应误标 ride_idle 为 extra"
            assert not win.matrix.act_stack._buttons["ride_run"].property("extra"), \
                "坐骑 shadow 单部件视图不应误标 ride_run 为 extra"

            # CleanupConfirmDialog 默认全选 + 取消勾选过滤
            win.part_list._select_by_key(str(res_dir / "50121103_hair"))
            _settle(app, win)
            items = collect_extra_cleanup_items([win._part], win._tpl)
            assert len(items) == 4, f"50121103_hair 应有 4 项多余动作: {items}"
            dlg = CleanupConfirmDialog(items)
            assert len(dlg.selected_items()) == 4 and dlg._ok_btn.isEnabled()
            dlg._list.item(0).setCheckState(dlg._list.item(0).checkState().Unchecked)
            assert len(dlg.selected_items()) == 3
            dlg._set_all(False)
            assert len(dlg.selected_items()) == 0 and not dlg._ok_btn.isEnabled()
            dlg._set_all(True)

            print("== 4. _execute_trash_items 执行清理 + 防二次重扫 + 正常动作保留 ==")
            ok_cnt, fail_cnt = win._execute_trash_items(dlg.selected_items(), trash_fn=fake_trash)
            assert not win._rescan_timer.isActive(), "清理后不应残留活跃的 1s 防抖重扫定时器"
            assert "移入回收站" in win.statusBar().currentMessage(), \
                f"状态栏应保留回收站提示: {win.statusBar().currentMessage()}"
            _settle(app, win)
            assert ok_cnt == 4 and fail_cnt == 0, f"清理统计异常: {(ok_cnt, fail_cnt)}"
            for d in ("E", "N", "S"):
                assert not (res_dir / "50121103_hair" / d / "attack").exists()
            assert not (res_dir / "50121103_hair" / "E" / "dance").exists()
            assert (res_dir / "50121103_hair" / "E" / "idle").is_dir(), "正常动作必须保留"
            assert (res_dir / "50121103_hair" / "SE" / "attack").is_dir(), "SE/attack 必须保留"
            assert not win.matrix.act_stack._buttons["attack"].property("extra"), \
                "清理并重扫后 E/attack 不再标 extra"
            assert "dance" not in win.matrix.act_stack._buttons, \
                "清理并重扫后未知动作按钮应移除"

            print("== 5. 图集端到端清理 + 最后一项删空视图清空 ==")
            win._on_folder_dropped(atlas_dir)
            _settle(app, win)
            win.part_list._select_by_key(str(atlas_dir / "50121103_hair"))
            _settle(app, win)
            atl_cleanup = collect_extra_cleanup_items([win._part], win._tpl)
            ok2, fail2 = win._execute_trash_items(atl_cleanup, trash_fn=fake_trash)
            _settle(app, win)
            assert ok2 == 2 and fail2 == 0
            assert (atlas_dir / "50121103_hair_E_idle.plist").is_file()
            assert (atlas_dir / "50121103_hair_E_idle.png").is_file()
            assert not (atlas_dir / "50121103_hair_E_attack.plist").exists()
            assert not (atlas_dir / "50121103_hair_E_attack.png").exists()
            assert not (atlas_dir / "50121103_hair_E_dance.plist").exists()

            # 删除独立散帧目录中仅剩的 E/idle 后，A/B 区与 HUD 应清空不残留
            solo_dir = tmp / "solo"
            _mk_frames(solo_dir / "50121103_hair" / "E" / "idle", 2)
            win._on_folder_dropped(solo_dir)
            _settle(app, win)
            win.part_list._select_by_key(str(solo_dir / "50121103_hair"))
            _settle(app, win)
            from src.core.cleanup import collect_action_cleanup_items
            last_items = collect_action_cleanup_items([win._part], "E", "idle", win._tpl)
            ok3, fail3 = win._execute_trash_items(last_items, trash_fn=fake_trash)
            _settle(app, win)
            assert ok3 == 1 and fail3 == 0
            assert win._result.parts == [] and len(win.grid_view._cells) == 0
            print("  ✓ 散帧、图集、防二次重扫及删空清空全部验证通过")
        finally:
            if win is not None:
                win.close()
            appmod.QSettings = orig_qsettings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nALL M43 PASS")


if __name__ == "__main__":
    main()
