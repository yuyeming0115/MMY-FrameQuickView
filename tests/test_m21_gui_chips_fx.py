"""M21 GUI 冒烟：分类 chips 过滤 + 特效扁平预览（offscreen）。"""
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
from src.ui.button_matrix import ButtonMatrix

tpl = load_templates()[0]

tmp = Path(tempfile.mkdtemp())
try:
    # 造一个混合目录：主角 2 组 + 伙伴 1 组 + 特效 1 组（断帧）
    for rid in ("50122101", "50132101"):
        d = tmp / f"{rid}_body"
        (d / "E" / "idle").mkdir(parents=True)
        (d / "E" / "idle" / "0001.png").write_bytes(b"x")
    d = tmp / "502001"
    (d / "E" / "idle").mkdir(parents=True)
    (d / "E" / "idle" / "0001.png").write_bytes(b"x")
    d = tmp / "50105101"
    d.mkdir()
    for i in [1, 2, 4]:   # 缺 3
        (d / f"changrao_{i}.png").write_bytes(b"x")

    r = scan_root(tmp, tpl)

    # ---- PartList chips ----
    pl = PartList()
    pl.set_template(tpl)
    pl.set_namemap(None)
    pl.load_result(r)
    chips = list(pl._chips.keys())
    assert "" in chips and "主角" in chips and "伙伴" in chips and "特效" in chips, chips
    assert pl._chips_host.isVisibleTo(pl), "分类 ≥2 时 chips 行应显示"
    assert pl._active_category == ""
    total_groups = pl.tree.topLevelItemCount()
    assert total_groups == 4, total_groups

    pl._on_chip_clicked("主角")
    visible = [pl.tree.topLevelItem(i).text(0)
               for i in range(total_groups)
               if not pl.tree.topLevelItem(i).isHidden()]
    assert len(visible) == 2 and all("5013" in v or "5012" in v for v in visible), visible
    assert "主角 · 2/4 组" in pl.count_label.text(), pl.count_label.text()
    print(f"[1] OK chips 过滤: {visible}")

    # 与搜索叠加（AND）
    pl.filter_edit.setText("5013")
    visible = [pl.tree.topLevelItem(i).text(0)
               for i in range(total_groups)
               if not pl.tree.topLevelItem(i).isHidden()]
    assert len(visible) == 1 and "5013" in visible[0], visible
    print(f"[2] OK chips+搜索叠加: {visible}")

    # 再点当前 chip 取消
    pl.filter_edit.clear()
    pl._on_chip_clicked("主角")
    visible = sum(1 for i in range(total_groups)
                  if not pl.tree.topLevelItem(i).isHidden())
    assert visible == 4, visible
    print("[3] OK 再点取消过滤")

    # 分类不足 2 个 → chips 行隐藏
    r2 = scan_root(tmp / "502001", tpl)
    pl.load_result(r2)
    assert not pl._chips_host.isVisibleTo(pl), "单分类应隐藏 chips 行"
    print("[4] OK 单分类隐藏 chips")

    # ---- ButtonMatrix 特效虚拟方向 ----
    mx = ButtonMatrix()
    mx.set_template(tpl)
    fx_part = next(p for p in r.parts if p.is_flat)
    mx.show_part(fx_part, None, None)
    d, a = mx.current()
    assert d == "特效" and a == "changrao", (d, a)
    fx_group = next(g for g in r.groups if g.is_flat)
    mx.show_group(fx_group, None, None)
    d, a = mx.current()
    assert d == "特效" and a == "changrao", (d, a)
    print(f"[5] OK 特效按钮: 方向={d} 动作={a}")

    # 断帧红点
    assert fx_group.has_issues, "缺帧 3 应标红"
    print("[6] OK 特效断帧红点")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS")
