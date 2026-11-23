# -*- coding: utf-8 -*-
"""M42：类型词容错（单字「装」不截胡「时装」）+ 时装按武器标签自动推断穿戴武器。

覆盖：
1. Template.look_split：含「装」与「时装」时优先匹配长词「时装」，同时兼容单字「装」尾缀
2. extract_weapon_tag：提取「斧」/〔剑〕标签及空括号/无括号边界
3. 端到端（offscreen MainWindow + 临时 QSettings ini 隔离）：
   - 职业装 `501221002 拓跋部「斧」装`（单字「装」容错）+ `501211002 拓跋部「斧」发` + `501031002 拓跋部「斧」武器`
     → M37 自动 5 层组合，武器下拉隐藏
   - 时装 `50122101 天命·男「斧」时装` + `50121101 天命·男「斧」发`
     → 未手动配置时自动推断穿戴 `501031002 拓跋部「斧」武器`，画布 5 层且下拉回显，不写 QSettings
   - 用户显式脱下武器（`_on_look_dressed("weapon", "")`）→ 降回 3 层，切组切回保持脱下
   - 用户手动切换其他武器（如换剑）→ 优先使用手动记忆
4. 真实目录验证（若 `E:\\XYJProject\\美术资源\\动画序列帧\\角色输出图` 存在）：
   - 验证 `501221002` 与 `50122101` 均自动配出头发与斧头武器

运行：QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/test_m42_auto_weapon_and_look_split.py
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
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import src.app as appmod
from src.core.template import Template, extract_weapon_tag, load_templates

TPL = next(t for t in load_templates() if t.name == "天命装")

MAP_TEXT = "\n".join([
    "501221002\t拓跋部「斧」装",
    "501211002\t拓跋部「斧」发",
    "501031002\t拓跋部「斧」武器",
    "50122101\t天命·男「斧」时装",
    "50121101\t天命·男「斧」发",
    "501031001\t昆仑剑侠「剑」武器",
    "503099\t狂斧妖「斧」",
]) + "\n"


def _mk_seq(base: Path, n: int = 8):
    for i in range(1, n + 1):
        p = base / f"{i:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def build_tree(root: Path):
    for folder in (
        "501221002_body", "501221002_shadow",
        "501211002_hair",
        "501031002_weapon", "501031002_shadow",
        "50122101_body", "50122101_shadow",
        "50121101_hair",
        "501031001_weapon", "501031001_shadow",
        "503099",
    ):
        _mk_seq(root / folder / "SE" / "idle")
    (root / "匹配表.txt").write_text(MAP_TEXT, encoding="utf-8")


def _settle(app, win):
    for _ in range(60):
        app.processEvents()
        wg, wa = win.grid_view._worker, win.anim_view._worker
        wb = [w for w in win._blank_workers if w.isRunning()]
        if (wg is None or not wg.isRunning()) and (wa is None or not wa.isRunning()) and not wb:
            break
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


def main():
    print("== 1. look_split 长词优先 + 单字「装」与「职业装」容错 ==")
    assert TPL.look_split("天命·男「剑」时装") == ("天命·男「剑」", "时装"), \
        f"不应被「装」截胡: {TPL.look_split('天命·男「剑」时装')}"
    assert TPL.look_split("拓跋部「斧」职业装") == ("拓跋部「斧」", "职业装"), \
        f"不应被「装」截断职业装: {TPL.look_split('拓跋部「斧」职业装')}"
    assert TPL.look_split("拓跋部「斧」装") == ("拓跋部「斧」", "装")
    assert TPL.look_split("昆仑剑侠〔剑〕武器0阶") == ("昆仑剑侠〔剑〕", "武器")
    assert TPL.look_split("装") is None
    assert TPL.look_split("时装") is None, "词在开头不应被「装」截胡"
    assert TPL.look_split("时装0阶") is None, "带尾缀词在开头不应被「装」截胡"
    assert TPL.look_split(" 时装") is None, "前导空白导致前缀为空应返回 None"
    assert TPL.look_split("") is None
    assert TPL.look_split("冰火双翼") is None
    print("  ✓ look_split 切分与边界全部正确")

    print("== 2. extract_weapon_tag 标签提取 ==")
    assert extract_weapon_tag("天命·男「斧」时装") == "斧"
    assert extract_weapon_tag("昆仑剑侠〔剑〕武器0阶") == "剑"
    assert extract_weapon_tag("黑龙「旧」〔双刀〕武器") == "双刀"
    assert extract_weapon_tag("天命「斧」〔  〕") == "斧"
    assert extract_weapon_tag("「斧「剑」") == "剑"
    assert extract_weapon_tag("无括号名称") is None
    assert extract_weapon_tag("空标签「」") is None
    assert extract_weapon_tag("空格标签〔  〕") is None
    assert extract_weapon_tag("") is None
    print("  ✓ extract_weapon_tag 边界全部正确")

    tmp = Path(tempfile.mkdtemp(prefix="m42_"))
    res_dir = tmp / "res"
    res_dir.mkdir()
    ini_path = tmp / "settings.ini"
    orig_qsettings = appmod.QSettings
    appmod.QSettings = lambda *a, **k: QSettings(str(ini_path), QSettings.IniFormat)
    try:
        build_tree(res_dir)
        app = QApplication.instance() or QApplication([])
        win = appmod.MainWindow()
        win.show()
        win._tpl.outfit_merge_max = 2  # 小型树禁用 M23 套装合并
        try:
            win._on_folder_dropped(res_dir)
            app.processEvents()

            print("== 3. 职业装单字「装」容错：自动 5 层组合 ==")
            win.part_list._select_by_key("GRP:501221002")
            _settle(app, win)
            tog = win.anim_view._toggles
            display = win._display_parts(win._group)
            assert [p.part for p in display] == ["shadow", "shadow", "body", "hair", "weapon"], \
                f"职业装应 M37 自动 5 层组合: {[p.part for p in display]}"
            assert not tog._weapon_combo.isVisible(), "已有武器 → 穿戴武器下拉应隐藏"
            print("  ✓ 501221002 自动 5 层组合")

            print("== 4. 时装自动推断穿戴同标签 0 阶武器 ==")
            win.part_list._select_by_key("GRP:50122101")
            _settle(app, win)
            layers, fm, _off, keys = win._layers_for_current()
            assert len(layers) == 5, f"天命·男「斧」时装应自动配斧 5 层: {len(layers)} / {keys}"
            assert "501031002_weapon" in keys and "501031002_shadow" in keys, f"应配 501031002 斧头: {keys}"
            assert keys.index("501031002_shadow") < keys.index("body") < keys.index("hair") < keys.index("501031002_weapon"), \
                f"自动穿戴斧头影子应在最底、斧头在身体/头发之上: {keys}"
            assert fm == [False] * 5, f"自动穿戴层应全部 flat=False: {fm}"
            assert tog._weapon_combo.isVisible(), "穿戴武器下拉应保持可见以便切换"
            assert tog._weapon_combo.currentData() == "501031002", \
                f"下拉应回显自动推断武器: {tog._weapon_combo.currentData()}"
            s_check = QSettings(str(ini_path), QSettings.IniFormat)
            assert not s_check.contains("layering/dressed_weapon/50122101"), \
                "自动推断不应提前污染 QSettings"
            print("  ✓ 50122101 自动推断穿戴 501031002 且未写 QSettings")

            print("== 5. 用户显式脱下（UI下拉选「无」）跨重启持久化记忆，不再自动重装 ==")
            tog._weapon_combo.setCurrentIndex(0)
            _settle(app, win)
            layers, _fm, _off, keys = win._layers_for_current()
            assert len(layers) == 3, f"显式脱下后应回 3 层: {len(layers)} / {keys}"
            win.close()

            win_reopen = appmod.MainWindow()
            win_reopen.show()
            win_reopen._tpl.outfit_merge_max = 2
            try:
                win_reopen._on_folder_dropped(res_dir)
                app.processEvents()
                win_reopen.part_list._select_by_key("GRP:50122101")
                _settle(app, win_reopen)
                layers, _fm, _off, keys = win_reopen._layers_for_current()
                assert len(layers) == 3, f"重启后应保持脱下 3 层: {len(layers)} / {keys}"
                assert win_reopen.anim_view._toggles._weapon_combo.currentData() == ""
                print("  ✓ UI 下拉脱下 + 重启持久化记忆均严格生效")

                print("== 6. 纯 ID 整包资源(part=None)不误穿主角武器 & 手动记忆优先生效 ==")
                win_reopen.part_list._select_by_key("GRP:503099")
                _settle(app, win_reopen)
                layers_npc, _fm_n, _o_n, keys_npc = win_reopen._layers_for_current()
                assert len(layers_npc) == 1, f"纯 ID 怪物带「斧」不应误挂主角武器: {keys_npc}"

                win_reopen.part_list._select_by_key("GRP:50122101")
                _settle(app, win_reopen)
                win_reopen._on_look_dressed("weapon", "501031001")
                _settle(app, win_reopen)
                layers, _fm, _off, keys = win_reopen._layers_for_current()
                assert len(layers) == 5 and "501031001_weapon" in keys, f"手动改剑应生效: {keys}"

                # 失效记忆回退：QSettings 记住了一个当前目录不存在的旧武器 key → 回退到自动推断斧头
                win_reopen._dressed_weapon["50122101"] = "501039999"
                assert win_reopen._effective_dressed_look("weapon", win_reopen._group) == "501031002"
                print("  ✓ 纯 ID 排除、手动优先、失效 key 回退均生效")
            finally:
                win_reopen.close()

            real_dir = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图")
            if real_dir.is_dir():
                print("== 7. 真实目录验证（角色输出图，全新 QSettings 且禁写匹配表） ==")
                from src.core.namemap import NameMap
                orig_reg = NameMap.register_missing
                NameMap.register_missing = lambda self, *a, **k: []
                real_ini = tmp / "real_settings.ini"
                appmod.QSettings = lambda *a, **k: QSettings(str(real_ini), QSettings.IniFormat)
                win_real = appmod.MainWindow()
                win_real.show()
                try:
                    win_real._on_folder_dropped(real_dir)
                    app.processEvents()

                    win_real.part_list._select_by_key("GRP:501221002")
                    _settle(app, win_real)
                    disp1_names = {p.name for p in win_real._display_parts(win_real._group)}
                    _l, _f, _o, rkeys1 = win_real._layers_for_current()
                    assert {"501211002_hair", "501031002_weapon"} <= disp1_names, \
                        f"真实目录 501221002 应组合出头发+武器: {disp1_names}"
                    assert len(rkeys1) == 5 and "hair" in rkeys1 and "weapon" in rkeys1, \
                        f"真实目录 501221002 应自动配齐 5 层: {rkeys1}"

                    for rid in ("50122101", "50122102", "50122103", "50122104"):
                        win_real.part_list._select_by_key(f"GRP:{rid}")
                        _settle(app, win_real)
                        disp_parts = {p.part for p in win_real._display_parts(win_real._group)}
                        _l, _f, _o, rkeys = win_real._layers_for_current()
                        assert "hair" in disp_parts, \
                            f"真实目录 {rid} 应带出头发: {disp_parts}"
                        assert len(rkeys) == 5 and "hair" in rkeys and "501031002_weapon" in rkeys, \
                            f"真实目录 {rid} 应自动配齐发+斧: {rkeys}"
                    print("  ✓ 真实目录 501221002 及 50122101~50122104 均自动配出头发与斧头武器")
                finally:
                    NameMap.register_missing = orig_reg
                    win_real.close()
        finally:
            win.close()
    finally:
        appmod.QSettings = orig_qsettings
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nALL M42 PASS")


if __name__ == "__main__":
    main()
