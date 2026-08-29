"""M26 测试：全局特效库与套装穿戴。

覆盖：
1. scanner：特效无论物理位置都进 fx_library（最外层独立文件夹 / 套装目录内）
2. app：穿戴特效追加为顶层 flat 层，帧序列取自身
3. app：part_keys 与 layers 严格同序（偏移微调定位正确）
4. app：特效已在组内（旧目录结构）时不重复叠加
5. app：偏移按特效名存取 → 跨套装复用
6. app：穿戴选择按套装 key 记忆，切换套装自动恢复
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
    # 目录：特效库在最外层独立文件夹；两个「已迁移」套装 + 一个「旧结构」套装
    fxlib = tmp / "特效库"
    make_fx(fxlib, "50105101", "changrao", 5)      # 游龙 5 帧
    make_fx(fxlib, "50105102", "fengwu", 3)        # 凤舞 3 帧

    outfit_a = tmp / "501521005_女主2_女侠客(双刀)_部件"
    make_part(outfit_a, "501031005_shadow")
    make_part(outfit_a, "501511005_hair")
    make_part(outfit_a, "501521005_body")

    outfit_b = tmp / "501421004_月河郡主(琴)_部件"
    make_part(outfit_b, "501031004_shadow")
    make_part(outfit_b, "501421004_body")

    # 旧结构：特效仍放在套装目录内（M25 自动并入）
    outfit_c = tmp / "501521007_旧结构_部件"
    make_part(outfit_c, "501031007_shadow")
    make_part(outfit_c, "501521007_body")
    make_fx(outfit_c, "50105103", "jianqi", 4)

    # ---- 1) 特效无论位置都进全局库 ----
    r = scan_root(tmp, tpl)
    fx_names = sorted(p.name for p in r.fx_library)
    assert fx_names == ["50105101", "50105102", "50105103"], f"特效库: {fx_names}"
    outfits = [g for g in r.groups if g.is_outfit]
    assert len(outfits) == 3, f"3 个套装组: {[g.key for g in r.groups]}"
    print(f"[1] OK 全局特效库: {fx_names}｜套装 {len(outfits)} 组")

    # ---- GUI ----
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from src.app import MainWindow
    from tests.smoke_gui import _wait_workers

    win = MainWindow()
    win._on_folder_dropped(tmp)
    win._hidden_parts = set()      # 清掉 QSettings 残留隐藏项，保证断言稳定
    ga = next(g for g in win._result.groups if g.key.endswith("双刀)_部件"))
    gb = next(g for g in win._result.groups if g.key.endswith("月河郡主(琴)_部件"))
    gc = next(g for g in win._result.groups if g.key.endswith("旧结构_部件"))

    # ---- 2) 未穿戴 vs 穿戴 ----
    win._on_group_selected(ga.key)
    _wait_workers(win)
    layers, flat_mask, offsets, keys = win._layers_for_current()
    assert len(layers) == 3, f"未穿戴应 3 层: {len(layers)}"
    assert not any(flat_mask), f"未穿戴时无 flat 层: {flat_mask}"

    win._on_fx_dressed("50105101")          # 穿游龙（5 帧）
    _wait_workers(win)
    layers, flat_mask, offsets, keys = win._layers_for_current()
    assert len(layers) == 4, f"穿戴后应 4 层: {len(layers)}"
    assert flat_mask[-1] is True, f"穿戴特效应在顶层（末位）: {flat_mask}"
    assert len(layers[-1]) == 5, f"游龙应 5 帧: {len(layers[-1])}"
    print(f"[2] OK 穿戴游龙: {len(layers)} 层, 特效 {len(layers[-1])} 帧, flat_mask={flat_mask}")

    # ---- 3) part_keys 与 layers 同序 ----
    assert len(keys) == len(layers) == len(flat_mask), "layers/flat_mask/part_keys 长度须一致"
    expect = [win._toggle_key(p, ga) for p in ga.parts]
    assert keys[:3] == expect, f"前 3 层应对齐组内部件: {keys[:3]} != {expect}"
    assert keys[-1] == "50105101", f"末位应为特效名: {keys[-1]}"
    print(f"[3] OK 顺序一致: keys={keys}")

    # ---- 4) 特效已在组内 → 不重复叠加 ----
    win._on_group_selected(gc.key)
    win._on_fx_dressed("50105103")          # 选的特效本就已在组内
    _wait_workers(win)
    l2, m2, _o2, k2 = win._layers_for_current()
    assert sum(1 for x in m2 if x) == 1, f"不应重复叠加: {m2}"
    print(f"[4] OK 不重复叠加: {len(l2)} 层, flat_mask={m2}")

    # ---- 5) 偏移按特效名存取 → 跨套装复用 ----
    win._set_fx_offset("50105101", 10, -5)
    assert win._get_fx_offset("50105101") == (10, -5), "偏移存取"
    win._on_group_selected(gb.key)
    win._on_fx_dressed("50105101")          # 套装 B 穿同一特效
    _wait_workers(win)
    l5, m5, off5, k5 = win._layers_for_current()
    assert off5.get(len(l5) - 1) == (10, -5), f"偏移应跨套装复用: {off5}"
    print(f"[5] OK 偏移跨套装复用: {off5.get(len(l5) - 1)}")

    # ---- 6) 穿戴选择按套装 key 记忆 ----
    assert win._dressed_fx.get(ga.key) == "50105101", "套装 A 应记住游龙"
    assert win._dressed_fx.get(gb.key) == "50105101", "套装 B 应记住游龙"
    win._on_group_selected(ga.key)
    _wait_workers(win)
    l6, m6, _o6, k6 = win._layers_for_current()
    assert len(l6) == 4 and m6[-1], "切回套装 A 应恢复穿戴"
    print(f"[6] OK 按套装记忆: A={win._dressed_fx.get(ga.key)} B={win._dressed_fx.get(gb.key)}")

    print("ALL M26 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
