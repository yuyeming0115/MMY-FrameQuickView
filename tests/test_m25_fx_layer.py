"""M25 测试：特效层并入套装组置顶。

覆盖：
1. scanner：套装 + 特效扁平文件夹 → 单套装组，特效成员置顶（parts 末位）
2. scanner：配套校验不误报 / fills 警告不含虚拟方向「特效」
3. scanner：非套装文件夹（单 ID）+ 特效 → 特效维持独立散件组
4. GUI：_layers_for_current 特效层取自身序列（叠在任何方向/动作上）
5. GUI：左栏套装子项显示特效中文名（无匹配表时兜底「特效」）
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


def make_part(parent: Path, name: str, n: int = 2) -> None:
    act = parent / name / "E" / "idle"
    act.mkdir(parents=True)
    for i in range(1, n + 1):
        make_png(act / f"{i:04d}.png")


def make_fx(parent: Path, rid: str, seq: str, n: int) -> Path:
    d = parent / rid
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        make_png(d / f"{seq}_{i}.png")
    return d


tmp = Path(tempfile.mkdtemp())
try:
    # ---- 1) 套装 + 特效 → 单套装组，特效置顶 ----
    outfit = tmp / "501521005_女主2_女侠客(双刀)_部件"
    make_part(outfit, "501031005_shadow")
    make_part(outfit, "501031005_weapon")
    make_part(outfit, "501511005_hair")
    make_part(outfit, "501521005_body")
    make_part(outfit, "501521005_shadow")
    make_fx(outfit, "50105101", "changrao", 3)

    r = scan_root(outfit, tpl)
    assert len(r.groups) == 1, f"应合并为 1 个套装组: {[g.key for g in r.groups]}"
    g = r.groups[0]
    assert g.is_outfit
    fxs = [p for p in g.parts if p.is_flat]
    assert len(fxs) == 1 and fxs[0].res_id == "50105101", g.parts
    assert g.parts[-1].is_flat, f"特效应排 parts 末位（置顶渲染）: {[p.name for p in g.parts]}"
    assert [p.name for p in g.parts][:2] == ["501031005_shadow", "501521005_body"] or \
        g.parts[0].part == "shadow", "shadow 应排首位（最底层）"
    print(f"[1] OK 套装+特效: {[p.name for p in g.parts]}")

    # ---- 2) 配套不误报 / fills 警告不含特效方向 ----
    assert g.pairing_issues == [], f"特效不应触发配套误报: {g.pairing_issues}"
    assert "特效" not in g.missing_fills, f"虚拟方向不应进 fills 缺失: {g.missing_fills}"
    assert "特效" not in g.missing_directions
    print(f"[2] OK 豁免: pairing={g.pairing_issues} fills_warn={g.missing_fills}")

    # ---- 3) 非套装（单 ID）+ 特效 → 特效独立 ----
    reg = tmp / "reg"
    make_part(reg, "501421004_body")
    make_part(reg, "501421004_shadow")
    make_fx(reg, "50105201", "liuxing", 2)
    r2 = scan_root(reg, tpl)
    kinds = sorted(f"{g.res_id}:{g.is_flat}" for g in r2.groups)
    assert len(r2.groups) == 2, kinds
    assert any(g.is_flat and g.res_id == "50105201" for g in r2.groups), kinds
    print(f"[3] OK 非套装场景: {kinds}")

    # ---- 4) GUI：特效层取自身序列 + 帧数 = max ----
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from src.app import MainWindow
    win = MainWindow()
    win._on_folder_dropped(outfit)
    win._on_group_selected(g.key)
    from tests.smoke_gui import _wait_workers
    _wait_workers(win)

    d, a = win.matrix.current()
    assert (d, a) == ("E", "idle"), (d, a)
    layers = win._layers_for_current()
    assert len(layers) == 6, f"6 层（5 部件 + 1 特效）: {len(layers)}"
    assert layers[-1] and len(layers[-1]) == 3, "特效层应为自身 3 帧"
    body_idx = [i for i, p in enumerate(win._group.parts) if p.part == "body"][0]
    assert len(layers[body_idx]) == 2, "身体层 2 帧"
    cells = len(win.grid_view._cells)
    assert cells == 3, f"合成帧数 = max(2,3) = 3: {cells}"
    frames = len(win.anim_view._frames)
    assert frames == 3, f"B 区帧数: {frames}"
    print(f"[4] OK 渲染: {len(layers)} 层, A区={cells} 帧, B区={frames} 帧, 特效层={len(layers[-1])} 帧")

    # ---- 5) 左栏套装子项特效中文名（匹配表有则显示中文名，无则兜底「特效」） ----
    item = win.part_list.tree.topLevelItem(0)
    texts = [item.child(i).text(0).replace("  🔴", "").replace("  🟠", "")
             for i in range(item.childCount())]
    fx_labels = [t for t in texts if t.startswith("50105101 ")]
    assert len(fx_labels) == 1 and "整体" not in fx_labels[0], texts
    print(f"[5] OK 左栏子项: {texts}")

    print("ALL M25 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
