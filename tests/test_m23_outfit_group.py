"""M23 冒烟：套装组跨 ID 整装预览（检测规则 / 降级 / 显示名 / 层序 / UI key）。"""
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
from src.core.template import load_templates
from src.ui.part_list import PartList

tpl = load_templates()[0]


def make_part(base: Path, name: str, dirs=("E",), acts=("idle",), n=2):
    d = base / name
    for dr in dirs:
        for a in acts:
            (d / dr / a).mkdir(parents=True)
            for i in range(1, n + 1):
                (d / dr / a / f"{i:04d}.png").write_bytes(b"x")
    return d


tmp = Path(tempfile.mkdtemp())
try:
    # ---- 1) 套装文件夹：跨 ID 部件 → 单个套装组 ----
    outfit = tmp / "501521005_女主2_女侠客(双刀)_部件"
    make_part(outfit, "501031005_shadow")
    make_part(outfit, "501031005_weapon")
    make_part(outfit, "501511005_hair")
    make_part(outfit, "501521005_body")
    make_part(outfit, "501521005_shadow")
    r = scan_root(outfit, tpl)
    assert len(r.groups) == 1, f"应合并为 1 个套装组: {[g.key for g in r.groups]}"
    g = r.groups[0]
    assert g.is_outfit and g.key == str(outfit)
    assert g.display_name == "501521005_女主2_女侠客(双刀)", g.display_name
    assert g.res_id == "501521005", "主 ID 应取 body 部件"
    assert g.character_type == "protagonist", g.character_type
    assert [p.part for p in g.parts] == ["shadow", "shadow", "body", "hair", "weapon"], \
        f"层序应按 layer_order（双 shadow 最底）: {[p.part for p in g.parts]}"
    assert len({p.res_id for p in g.parts}) == 3, "跨 3 个 ID"
    print(f"[1] OK 套装合并: {g.display_name} 5件3ID type={g.effective_type}")

    # ---- 2) 无 _部件 后缀的套装文件夹（纯中文名） ----
    outfit2 = tmp / "月河郡主（琴）"
    make_part(outfit2, "501031004_shadow")
    make_part(outfit2, "501031004_weapon")
    make_part(outfit2, "501421004_body")
    r2 = scan_root(outfit2, tpl)
    assert len(r2.groups) == 1 and r2.groups[0].is_outfit
    assert r2.groups[0].display_name == "月河郡主（琴）"
    print("[2] OK 纯中文名套装文件夹")

    # ---- 3) 混类别部件堆：不合并（维持按 ID 分组） ----
    mix = tmp / "mix"
    make_part(mix, "50106101_wings")
    make_part(mix, "50309901_ride_front")
    make_part(mix, "50122101_body")
    r3 = scan_root(mix, tpl)
    assert len(r3.groups) == 3 and not any(g.is_outfit for g in r3.groups), \
        f"混类别不应合并: {[(g.res_id, g.is_outfit) for g in r3.groups]}"
    print("[3] OK 混类别不合并")

    # ---- 4) 超阈值：不合并（安全降级） ----
    big = tmp / "big"
    for i in range(20):
        make_part(big, f"50199{i:02d}_body")
    r4 = scan_root(big, tpl)
    assert len(r4.groups) == 20 and not any(g.is_outfit for g in r4.groups), \
        f"20 件超阈值(16)不应合并: {len(r4.groups)}"
    print("[4] OK 超阈值降级")

    # ---- 5) 同 ID 部件：维持 ID 组（不触发套装） ----
    same = tmp / "same"
    make_part(same, "50104101_shadow")
    make_part(same, "50104101_wings")
    r5 = scan_root(same, tpl)
    assert len(r5.groups) == 1 and not r5.groups[0].is_outfit and r5.groups[0].key == "50104101"
    print("[5] OK 同 ID 维持 ID 组")

    # ---- 6) 嵌套：拖入上级目录，套装子文件夹各自成组 ----
    nest = tmp / "nest_root"
    o1 = nest / "天命女(剑)"
    o2 = nest / "天命女(双刀)"
    make_part(o1, "50112151_body")
    make_part(o1, "50111151_hair")
    make_part(o2, "50152151_body")
    make_part(o2, "50151151_hair")
    r6 = scan_root(nest, tpl)
    outfits = [g for g in r6.groups if g.is_outfit]
    assert len(outfits) == 2, f"应 2 个套装组: {[(g.display_name, g.is_outfit) for g in r6.groups]}"
    assert {g.display_name for g in outfits} == {"天命女(剑)", "天命女(双刀)"}
    print("[6] OK 嵌套多套装")

    # ---- 7) UI：左栏组头显示 + 子项带 ID + 套装组不可改名 ----
    pl = PartList()
    pl.set_template(tpl)
    pl.set_namemap(None)
    pl.load_result(r)   # 用例1的套装结果
    assert pl.tree.topLevelItemCount() == 1
    header = pl.tree.topLevelItem(0).text(0)
    assert "501521005_女主2_女侠客(双刀)" in header and "套装" in header, header
    child0 = pl.tree.topLevelItem(0).child(0)
    assert "501031005" in child0.text(0), f"子项应带所属 ID: {child0.text(0)}"
    from PySide6.QtCore import Qt
    assert not (pl.tree.topLevelItem(0).flags() & Qt.ItemIsEditable), "套装组头不可编辑"
    print(f"[7] OK 左栏显示: {header} / 子项 {child0.text(0)}")

    # ---- 8) 组选择 key 唯一（套装组 key = 路径，ID 组 key = res_id） ----
    keys = [g.key for g in r.groups]
    assert len(keys) == len(set(keys)), "组 key 必须唯一"
    print("[8] OK 组 key 唯一")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL M23 PASS")
