# -*- coding: utf-8 -*-
"""M38：穿戴时装/头发/武器（跨 ID 形象组下拉，按组记忆）。

背景：前缀匹配是 1 组对 1 组，但一把 0 阶武器逻辑上属于同武器类型的**所有**
时装形象（昆仑剑侠职业装、天命·男、伏羲·男…）——改名无法表达 1:N。
方案：显示层新增「穿戴时装/头发/武器」下拉（当前形象缺哪类才显示哪类），
选择按当前组 key 记忆（QSettings），复用 M26/M28 穿戴机制。

验证：
1. 时装组：形象组合带出头发后，仍可从「穿戴武器」下拉选任意武器组（含影子）
2. 穿戴后 A/B 区层数 = 显示部件 + 武器组部件；QSettings 持久化
3. 切到武器组：可反向穿戴时装/头发；武器槽位隐藏（本组已有）
4. 切回时装组：穿戴记忆恢复；脱下（选「无」）层数还原
5. 下拉选项：所有拥有该部位的常规组（排除套装/变体/特效）

运行：python tests/test_m38_wear_look.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.core.template import load_templates

TPL = next(t for t in load_templates() if t.name == "天命装")

MAP_TEXT = "\n".join([
    "501112101\t天命·男〔剑〕时装",
    "501111101\t天命·男〔剑〕发",
    "501031001\t昆仑剑侠〔剑〕武器0阶",
    "501031003\t逍遥仙〔扇〕武器0阶",
]) + "\n"


def _mk_seq(base: Path, n: int = 8):
    for i in range(1, n + 1):
        p = base / f"{i:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def build_tree(root: Path):
    for folder in ("501112101_body", "501112101_shadow", "501111101_hair",
                   "501031001_weapon", "501031001_shadow", "501031003_weapon"):
        _mk_seq(root / folder / "SE" / "idle")
    (root / "匹配表.txt").write_text(MAP_TEXT, encoding="utf-8")


def _settle(app, win):
    import time
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
    tmp = Path(tempfile.mkdtemp(prefix="m38_"))
    try:
        build_tree(tmp)
        app = QApplication.instance() or QApplication([])
        settings = QSettings("MMY", "FrameQuickView")
        snap_keys = ["last/folder", "last/selection", "checks/fills",
                     "display/layer_panel", "layering/hidden_parts",
                     "layering/dressed_body", "layering/dressed_hair",
                     "layering/dressed_weapon"]
        saved_qs = {}
        for k in snap_keys:
            saved_qs[k] = settings.value(k)
            settings.remove(k)
        settings.sync()
        from src.app import MainWindow
        win = MainWindow()
        win.show()
        win._tpl.outfit_merge_max = 2        # 小型树禁用套装合并（app 用自己的模板实例）
        try:
            win._on_folder_dropped(tmp)
            app.processEvents()

            print("== 1. 时装组：武器下拉可见、含全部武器组 ==")
            win.part_list._select_by_key("GRP:501112101")
            _settle(app, win)
            tog = win.anim_view._toggles
            assert len(win._display_parts(win._group)) == 3, "形象组合应已带出头发"
            assert tog._weapon_combo.isVisible(), "缺武器 → 穿戴武器下拉应显示"
            assert not tog._body_combo.isVisible() and not tog._hair_combo.isVisible(), \
                "已有时装/头发 → 对应下拉应隐藏"
            opts = [tog._weapon_combo.itemText(i) for i in range(tog._weapon_combo.count())]
            assert opts == ["无", "昆仑剑侠〔剑〕武器0阶", "逍遥仙〔扇〕武器0阶"], f"武器选项: {opts}"
            print(f"  ✓ 穿戴武器下拉 2 项: {opts[1:]}")

            print("== 2. 穿戴武器 → 叠层 3+2，QSettings 记忆 ==")
            win._on_look_dressed("weapon", "501031001")
            _settle(app, win)
            layers, fm, _off, keys = win._layers_for_current()
            assert len(layers) == 5, f"穿戴后应 5 层（3 显示 + 武器+影子）: {len(layers)}"
            assert "501031001_weapon" in keys and "501031001_shadow" in keys, f"part_keys: {keys}"
            # 穿戴层必须是普通层语义（flat=False）：部件与主体同画布渲染，按原始
            # 坐标叠加才与手上位置对齐；特效层语义（居中）会把武器挪离手（实测 bug）
            assert fm[-2:] == [False, False], f"穿戴层应 flat=False: {fm}"
            assert settings.value("layering/dressed_weapon/501112101", "", type=str) == "501031001"
            print(f"  ✓ 5 层 {keys}（flat=False 原始画布对齐）")

            print("== 3. 武器组：可反向穿戴时装，武器槽位隐藏 ==")
            win.part_list._select_by_key("GRP:501031001")
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 2, f"武器组未穿戴时应 2 层: {len(layers)}"
            assert tog._body_combo.isVisible() and tog._hair_combo.isVisible()
            assert not tog._weapon_combo.isVisible(), "本组已有武器 → 槽位隐藏"
            win._on_look_dressed("body", "501112101")
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 4, f"穿戴时装后应 4 层（武器+影子+身体+影子）: {len(layers)}"
            print("  ✓ 反向穿戴时装生效")

            print("== 4. 切回时装组：穿戴记忆恢复；脱下还原 ==")
            win.part_list._select_by_key("GRP:501112101")
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 5, f"切回应恢复武器穿戴: {len(layers)}"
            assert tog._weapon_combo.currentText() == "昆仑剑侠〔剑〕武器0阶", \
                f"下拉应回显已穿戴项: {tog._weapon_combo.currentText()}"
            win._on_look_dressed("weapon", "")
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 3, f"脱下武器应回到 3 层: {len(layers)}"
            assert settings.value("layering/dressed_weapon/501112101", "", type=str) == ""
            print("  ✓ 记忆恢复 + 脱下还原")

            print("== 5. 空帧抽样含穿戴层（不崩溃、正常收敛） ==")
            win._on_look_dressed("weapon", "501031001")
            _settle(app, win)
            assert win._blank_target_matrix(), "抽样目标不应为空"
            print("  ✓ OK")
        finally:
            win.close()
            for k, v in saved_qs.items():
                if v is None:
                    settings.remove(k)
                else:
                    settings.setValue(k, v)
            settings.sync()
        print("\nALL M38 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
