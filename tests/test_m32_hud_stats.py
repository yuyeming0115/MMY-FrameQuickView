"""M32 冒烟：HUD 信息统计聚合（单部件账目 / 套装主件口径 / 不一致标记 / 特效行 / 总帧数）。"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication([])

from src.core.scanner import scan_root
from src.core.stats import build_hud_stats, group_combos, part_total
from src.core.template import load_templates

tpl = load_templates()[0]


def make_part(base: Path, name: str, dirs=("E",), acts=("idle",), n=2, gap=False):
    d = base / name
    for dr in dirs:
        for a in acts:
            (d / dr / a).mkdir(parents=True)
            if gap:
                # 0001 与 0003 → 断档 [2]
                for i in (1, 3):
                    (d / dr / a / f"{i:04d}.png").write_bytes(b"x")
            else:
                for i in range(1, n + 1):
                    (d / dr / a / f"{i:04d}.png").write_bytes(b"x")
    return d


tmp = Path(tempfile.mkdtemp())
try:
    # ---- 1) 单部件账目：总数 / 范围 / 排序 / 断档 ----
    p1 = make_part(tmp, "501421004_body", dirs=("E", "N"), acts=("idle", "run"), n=4)
    r1 = scan_root(p1, tpl)
    part = r1.parts[0]
    st = build_hud_stats(part, tpl)
    assert part_total(part) == 16, f"总帧数应 16: {part_total(part)}"
    assert st.grand == 16 and st.rows and len(st.rows) == 4
    assert st.rows[0].direction == "E" and st.rows[0].action == "idle"
    assert (st.rows[0].first, st.rows[0].last) == (1, 4)
    row_gap = next(r for r in st.rows if r.action == "run")
    # 排序按模板 actions 顺序（idle < run），N 方向在后
    assert st.rows[1].action == "run" and st.rows[2].direction == "N"
    print("[1] OK 单部件账目: 16帧 4行 模板序")

    # ---- 2) 断档行 has_issues ----
    p2 = make_part(tmp, "501421005_body", dirs=("E",), acts=("idle",), gap=True)
    r2 = scan_root(p2, tpl)
    st2 = build_hud_stats(r2.parts[0], tpl)
    assert st2.rows[0].gaps == [2] and st2.rows[0].has_issues
    assert st2.rows[0].count == 2
    print("[2] OK 断档行: gaps=[2] count=2")

    # ---- 3) 套装：主件口径 + 不一致标记 ----
    outfit = tmp / "501521005_女侠客_部件"
    make_part(outfit, "501031005_shadow", n=8)                       # shadow 与主件一致
    make_part(outfit, "501031005_weapon", n=7)                       # weapon 7 帧 → 不一致
    make_part(outfit, "501521005_body", n=8)                         # 主件 8 帧
    r3 = scan_root(outfit, tpl)
    g3 = r3.groups[0]
    assert g3.is_outfit
    st3 = group_combos(g3, tpl)
    assert len(st3.rows) == 1, f"并集应 1 行: {st3.rows}"
    row = st3.rows[0]
    assert row.count == 8 and row.source == "", f"主件 body 8帧: {row.count}/{row.source}"
    assert row.mismatch == ["weapon 7帧"], f"不一致标记: {row.mismatch}"
    assert row.has_issues
    assert st3.grand == 8, f"合计=主件口径 8: {st3.grand}"
    assert st3.totals == {"shadow": 8, "weapon": 7, "body": 8}, st3.totals
    print("[3] OK 套装主件口径: 8帧 mismatch=[weapon 7帧] totals 3项")

    # ---- 4) 主件缺失组合 → 来源部件兜底 ----
    outfit2 = tmp / "501522006_剑客_部件"
    make_part(outfit2, "501032006_weapon", dirs=("E",), acts=("idle", "run"), n=6)
    make_part(outfit2, "501522006_body", dirs=("E",), acts=("idle",), n=8)
    r4 = scan_root(outfit2, tpl)
    st4 = group_combos(r4.groups[0], tpl)
    run_row = next(r for r in st4.rows if r.action == "run")
    assert run_row.count == 6 and run_row.source == "weapon", \
        f"run 行应取 weapon 6帧: {run_row.count}/{run_row.source}"
    idle_row = next(r for r in st4.rows if r.action == "idle")
    assert idle_row.count == 8 and idle_row.source == ""
    print("[4] OK 主件缺组合: run←weapon 6帧, idle←body 8帧")

    # ---- 5) 特效行：扁平部件成行 + 不参与 mismatch ----
    # M25：特效文件夹需与套装部件同层（放在套装目录内）才并入套装组
    outfit3 = tmp / "501523007_琴师_部件"
    make_part(outfit3, "501523007_body", dirs=("E",), acts=("idle",), n=8)
    make_part(outfit3, "501032007_weapon", dirs=("E",), acts=("idle",), n=8)
    fx = outfit3 / "50105101"
    fx.mkdir()
    for i in range(1, 21):
        (fx / f"changrao_{i}.png").write_bytes(b"x")
    r5 = scan_root(outfit3, tpl)
    g5 = next(g for g in r5.groups if g.is_outfit and "501523007" in g.key)
    st5 = group_combos(g5, tpl)
    fx_rows = [r for r in st5.rows if r.is_flat]
    assert fx_rows and fx_rows[0].action == "changrao" and fx_rows[0].count == 20, \
        f"特效行: {[(r.direction, r.action, r.count) for r in fx_rows]}"
    assert fx_rows[0].mismatch == [], "特效不参与 mismatch"
    body_row = next(r for r in st5.rows if not r.is_flat)
    assert body_row.count == 8 and body_row.mismatch == []
    assert st5.grand == 8 + 20, f"合计=主体行+特效行: {st5.grand}"
    print(f"[5] OK 特效行: {fx_rows[0].direction}·changrao 20帧 置入并集")

    # ---- 6) 扁平组（纯特效）账目 ----
    r6 = scan_root(fx, tpl)
    st6 = build_hud_stats(r6.groups[0], tpl)
    assert len(st6.rows) == 1 and st6.rows[0].is_flat and st6.grand == 20
    print("[6] OK 纯特效组账目: 20帧")

    print("\n全部通过 ✔")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
