"""M28 测试：全局翅膀库与穿戴翅膀。

覆盖：
1. scanner：翅膀无论物理位置都进 wing_library（最外层独立文件夹 / 角色目录内）
2. scanner：单一部位的「翅膀库」不当套装合并（各自按 ID 独立成组）
3. app：穿戴翅膀追加为顶层 flat 层，帧按当前 (方向,动作) 取（非固定首序列）
4. app：翅膀不进 _fx_layer_indices → Ctrl+方向键微调不会误调翅膀
5. app：翅膀 + 特效可同穿，特效置顶（末位），且只有特效可微调
6. app：翅膀已在组内时不重复叠加
7. app：穿戴选择按套装 key 记忆，切换组自动恢复
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from src.core.scanner import scan_root
from src.core.template import load_templates

tpl = load_templates()[0]


def make_png(path: Path) -> None:
    img = Image.new("RGBA", (10, 10), (120, 200, 90, 255))
    img.save(path)


def make_frames(d: Path, n: int, start: int = 1) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i in range(start, start + n):
        make_png(d / f"{i:04d}.png")


def make_part(parent: Path, name: str, **acts: int) -> None:
    """make_part(p, "501521005_body", idle=4, run=2) → E/idle 4 帧、E/run 2 帧。"""
    for act, n in (acts or {"idle": 2}).items():
        make_frames(parent / name / "E" / act, n)


def make_fx(parent: Path, rid: str, seq: str, n: int) -> Path:
    d = parent / rid
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        make_png(d / f"{seq}_{i}.png")
    return d


tmp = Path(tempfile.mkdtemp())
try:
    # 翅膀库在最外层独立文件夹（M28 主场景）；一个套装 + 一个自带翅膀的角色组
    winglib = tmp / "翅膀库"
    make_part(winglib, "50151101_wings", idle=3)              # 青翼 仅 idle 3 帧
    make_part(winglib, "50151102_wings", idle=5, run=2)       # 火翼 idle 5 / run 2

    outfit_a = tmp / "501521005_女主2_女侠客(双刀)_部件"
    make_part(outfit_a, "501031005_shadow", idle=4, run=4)
    make_part(outfit_a, "501511005_hair", idle=4, run=4)
    make_part(outfit_a, "501521005_body", idle=4, run=4)

    # 自带翅膀的角色（同 ID 组）：body + wings
    char_b = tmp / "角色"
    make_part(char_b, "501421004_body", idle=2, run=2)
    make_part(char_b, "501421004_wings", idle=2, run=2)

    fxlib = tmp / "特效库"
    make_fx(fxlib, "50105101", "changrao", 5)

    # ---- 1) 翅膀无论位置都进全局库 ----
    r = scan_root(tmp, tpl)
    wing_names = sorted(p.name for p in r.wing_library)
    assert wing_names == ["501421004_wings", "50151101_wings", "50151102_wings"], \
        f"翅膀库: {wing_names}"
    assert sorted(p.name for p in r.fx_library) == ["50105101"], "特效库"
    print(f"[1] OK 全局翅膀库: {wing_names}")

    # ---- 2) 单一部位的「翅膀库」不当套装合并 ----
    lib_groups = [g for g in r.groups if str(g.key).startswith(str(winglib))]
    assert not lib_groups, f"翅膀库不应合成套装: {[g.key for g in lib_groups]}"
    assert {"50151101", "50151102"} <= {g.key for g in r.groups}, \
        f"翅膀应各自按 ID 成组: {[g.key for g in r.groups]}"
    outfits = [g for g in r.groups if g.is_outfit]
    assert len(outfits) == 1 and outfits[0].key == str(outfit_a), \
        f"仍应识别真套装: {[(g.key, g.is_outfit) for g in r.groups]}"
    print("[2] OK 翅膀库不合并为套装，真套装照常识别")

    # ---- GUI ----
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from src.app import MainWindow
    from tests.smoke_gui import _wait_workers

    win = MainWindow()
    win._on_folder_dropped(tmp)
    win._hidden_parts = set()      # 清掉 QSettings 残留隐藏项，保证断言稳定
    ga = next(g for g in win._result.groups if g.key.endswith("双刀)_部件"))
    gchar = next(g for g in win._result.groups if g.key == "501421004")

    # ---- 3) 穿戴翅膀：顶层 flat 层，按 (方向,动作) 取帧 ----
    win._on_group_selected(ga.key)
    _wait_workers(win)
    base, bm, _bo, _bk = win._layers_for_current()
    assert len(base) == 3, f"未穿戴应 3 层: {len(base)}"
    assert not any(bm), f"未穿戴时无 flat 层: {bm}"

    win._on_wing_dressed("50151101_wings")     # 青翼 idle 3 帧
    _wait_workers(win)
    layers, flat_mask, _off, keys = win._layers_for_current()
    assert len(layers) == 4, f"穿戴翅膀后应 4 层: {len(layers)}"
    assert flat_mask[-1] is True, f"穿戴翅膀应在顶层（末位）: {flat_mask}"
    assert len(layers[-1]) == 3, f"青翼 E/idle 应 3 帧: {len(layers[-1])}"
    assert keys[-1] == "50151101_wings", f"末位 key 应为翅膀名: {keys[-1]}"
    print(f"[3] OK 穿戴青翼: {len(layers)} 层, 翅膀 {len(layers[-1])} 帧, flat_mask={flat_mask}")

    # 按 (方向,动作) 取帧：换火翼后切 run → 取该动作的 2 帧，而非固定首序列 5 帧
    win._on_wing_dressed("50151102_wings")
    _wait_workers(win)
    win.matrix.action_selected.emit("run")
    _wait_workers(win)
    l_run, _m, _o, _k = win._layers_for_current()
    assert win.matrix.current()[1] == "run", f"动作应切到 run: {win.matrix.current()}"
    assert len(l_run[-1]) == 2, f"火翼 E/run 应 2 帧（按动作取帧）: {len(l_run[-1])}"
    win.matrix.action_selected.emit("idle")
    _wait_workers(win)
    l_idle, _m, _o, _k = win._layers_for_current()
    assert len(l_idle[-1]) == 5, f"火翼 E/idle 应 5 帧: {len(l_idle[-1])}"
    print(f"[4] OK 按 (方向,动作) 取帧: run={len(l_run[-1])} 帧 / idle={len(l_idle[-1])} 帧")

    # ---- 5) 翅膀不进 _fx_layer_indices；同穿特效时只有特效可微调 ----
    assert win._fx_layer_indices == set(), \
        f"只穿翅膀时不应有可微调特效层: {win._fx_layer_indices}"
    win._on_fx_dressed("50105101")            # 同时穿特效（5 帧）
    _wait_workers(win)
    layers, flat_mask, _off, keys = win._layers_for_current()
    assert len(layers) == 5, f"翅膀+特效应 5 层: {len(layers)}"
    assert keys[-1] == "50105101", f"特效应置顶（末位）: {keys[-1]}"
    assert keys[-2] == "50151102_wings", f"翅膀应在特效之下: {keys[-2]}"
    assert win._fx_layer_indices == {4}, \
        f"仅末位（特效）可微调: {win._fx_layer_indices}"
    print(f"[5] OK 翅膀+特效同穿: keys={keys}, 可微调层={win._fx_layer_indices}")

    # ---- 6) 翅膀已在组内 → 不重复叠加 ----
    win._on_group_selected(gchar.key)
    win._on_wing_dressed("501421004_wings")   # 选的翅膀本就已在组内
    _wait_workers(win)
    l6, m6, _o6, k6 = win._layers_for_current()
    assert sum(1 for x in m6 if x) == 0, f"不应重复叠加: {m6} / {k6}"
    assert len(l6) == 2, f"组内自带翅膀，仍 2 层: {len(l6)} / {k6}"
    print(f"[6] OK 不重复叠加: {len(l6)} 层, flat_mask={m6}, keys={k6}")

    # ---- 7) 穿戴选择按组 key 记忆 ----
    assert win._dressed_wings.get(ga.key) == "50151102_wings", "套装 A 应记住火翼"
    win._on_group_selected(ga.key)
    _wait_workers(win)
    l7, m7, _o7, k7 = win._layers_for_current()
    assert len(l7) == 5 and m7[-1], f"切回套装 A 应恢复翅膀+特效: {len(l7)} / {k7}"
    print(f"[7] OK 按套装记忆: A={win._dressed_wings.get(ga.key)}")

    print("ALL M28 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
