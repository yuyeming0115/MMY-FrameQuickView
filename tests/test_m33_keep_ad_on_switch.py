# -*- coding: utf-8 -*-
"""M33：切换部件/组保持当前方向与动作（直到用户主动改变）。

旧行为：_on_part_selected / _on_group_selected 传 (None, None)，每次切换
部件/角色ID都重置回默认 SE+idle——跨 ID 比对同一动作需反复重选。

新行为：切换时携带 matrix.current() 继续传递；新部件/组缺失该组合时由
show_part/show_group 兜底回退（方向回退第一个可用、动作回退按类型默认）。

附带修正（兜底条件「缺失」→「不可用」）：
- 方向：direction not in avail（原只判 miss_dirs，漏掉特效虚拟方向等非模板方向）
- 动作：action not in present / owned_a_dir（原只判 miss_acts，漏掉 unexpected，
  如把角色的 run 带进坐骑会选中灰显且无资源的动作）

运行：python tests/test_m33_keep_ad_on_switch.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from src.app import MainWindow
from src.core.scanner import scan_root
from src.core.template import load_templates
from src.ui.button_matrix import ButtonMatrix

tpl = load_templates()[0]
app = QApplication.instance() or QApplication([])


def make_png(path: Path) -> None:
    Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(path)


def make_act(root: Path, name: str, direction: str, action: str, n: int = 2) -> None:
    d = root / name / direction / action
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        make_png(d / f"{i:04d}.png")


QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                  str(Path(tempfile.mkdtemp()) / "settings"))
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings("MMY", "FrameQuickView").clear()

tmp = Path(tempfile.mkdtemp())
try:
    # ============ 一、e2e：MainWindow 切换部件 / 组保持 ============
    # 同 ID 三件（body/weapon 同结构 SE 有 idle,attack,skill,run；hair 缺 attack）。
    # 注意：不能跨 ID——同父目录跨 ≥2 ID 会触发 M23 套装合并，改变树结构语义。
    make_act(tmp, "50112151_body", "SE", "idle")
    make_act(tmp, "50112151_body", "SE", "attack")
    make_act(tmp, "50112151_body", "SE", "skill")
    make_act(tmp, "50112151_body", "SE", "run")
    make_act(tmp, "50112151_weapon", "SE", "idle")
    make_act(tmp, "50112151_weapon", "SE", "attack")
    make_act(tmp, "50112151_weapon", "SE", "skill")
    make_act(tmp, "50112151_weapon", "SE", "run")
    make_act(tmp, "50112151_hair", "SE", "idle")
    make_act(tmp, "50112151_hair", "SE", "run")

    w = MainWindow()
    w._on_folder_dropped(tmp)
    assert w._part is not None, "自动选中第一项后 _part 应非空"

    # [1] 选 SE+attack → 切到同结构部件 → 保持 SE+attack
    w._on_action_selected("attack")
    assert w.matrix.current() == ("SE", "attack"), f"前置：应为 (SE, attack)，实际 {w.matrix.current()}"
    tree = w.part_list.tree
    grp0 = tree.topLevelItem(0)
    child_keys = [grp0.child(i).data(0, Qt.UserRole) for i in range(grp0.childCount())]
    target = next(k for k in child_keys if str(k).endswith("50112151_weapon"))
    w.part_list._select_by_key(target)
    assert w.matrix.current() == ("SE", "attack"), \
        f"[1] 切换部件后应保持 (SE, attack)，实际 {w.matrix.current()}"
    assert w._part is not None and w._part.name.endswith("50112151_weapon"), \
        f"[1] 信号应已切换 _part，实际 {w._part and w._part.name}"
    print("[1] OK 切换部件保持 SE+attack")

    # [2] 切到缺 attack 的部件 → 方向 SE 保持，动作回退默认 idle
    hair_key = next(k for k in child_keys if str(k).endswith("50112151_hair"))
    w.part_list._select_by_key(hair_key)
    assert w.matrix.current() == ("SE", "idle"), \
        f"[2] 缺 attack 应回退 (SE, idle)，实际 {w.matrix.current()}"
    print("[2] OK 缺失动作回退默认（方向仍保持）")

    # [3] 部件 SE+attack → 切同 ID 组 → 保持 SE+attack
    w.part_list._select_by_key(next(k for k in child_keys
                                    if str(k).endswith("50112151_body")))
    w._on_action_selected("attack")
    gkey = next(g.key for g in w._result.groups if g.res_id == "50112151")
    w._on_group_selected(gkey)
    assert w._group is not None, "[3] 应进入组视图"
    assert w.matrix.current() == ("SE", "attack"), \
        f"[3] 切换到组应保持 (SE, attack)，实际 {w.matrix.current()}"
    print("[3] OK 部件→组保持 SE+attack")

    # [4] 部件 → 套装组保持（跨 ID 部件父目录）
    outfit = tmp / "501521005_女主2_部件"
    make_act(outfit, "501031005_shadow", "SE", "idle")
    make_act(outfit, "501031005_shadow", "SE", "attack")
    make_act(outfit, "501521005_body", "SE", "idle")
    make_act(outfit, "501521005_body", "SE", "attack")
    w._on_folder_dropped(outfit)
    w._on_action_selected("attack")
    og = next(g for g in w._result.groups if g.is_outfit)
    w._on_group_selected(og.key)
    assert w.matrix.current() == ("SE", "attack"), \
        f"[4] 切换到套装组应保持 (SE, attack)，实际 {w.matrix.current()}"
    print("[4] OK 部件→套装组保持 SE+attack")

    # ============ 二、直测：ButtonMatrix 兜底回退 ============
    def matrix_of_part(folder: Path, direction, action):
        p = scan_root(folder, tpl).parts[0]
        m = ButtonMatrix()
        m.set_template(tpl)
        m.show_part(p, direction, action)
        return m.current()

    # [5] 常规 → 坐骑：run 对坐骑是 unexpected（非缺失），旧逻辑会保持灰显无资源的 run
    mount = tmp / "mount"
    make_act(mount, "50201101_ride_front", "E", "ride_idle")
    make_act(mount, "50201101_ride_front", "E", "ride_run")
    d5, a5 = matrix_of_part(mount, "E", "run")
    assert a5 == "ride_idle", f"[5] 坐骑应回退 ride_idle，实际 {a5}（unexpected 漏洞回归）"
    print(f"[5] OK 常规→坐骑：run 不可用 → 回退 {a5}")

    # [6] 特效虚拟方向 → 常规部件：非模板方向必须回退（旧逻辑 miss_dirs 判不出来）
    normal = tmp / "50112151_body"
    d6, a6 = matrix_of_part(normal, "特效", "idle")
    assert d6 == "SE", f"[6] 虚拟方向应回退 SE，实际 {d6}"
    assert a6 == "idle", f"[6] 动作应保持 idle，实际 {a6}"
    print(f"[6] OK 虚拟方向回退：方向={d6} 动作={a6}")

    # [7] 组视图保持 + 缺失回退
    g_all = tmp / "grp_have"
    make_act(g_all, "50112161_body", "E", "idle")
    make_act(g_all, "50112161_body", "E", "attack")
    make_act(g_all, "50112161_weapon", "E", "idle")
    make_act(g_all, "50112161_weapon", "E", "attack")
    g_no = tmp / "grp_miss"
    make_act(g_no, "50112162_body", "E", "idle")
    make_act(g_no, "50112162_body", "E", "run")
    r1 = scan_root(g_all, tpl)
    r2 = scan_root(g_no, tpl)
    m7 = ButtonMatrix()
    m7.set_template(tpl)
    m7.show_group(r1.groups[0], "E", "attack")
    assert m7.current() == ("E", "attack"), f"[7] 组保持失败，实际 {m7.current()}"
    m7.show_group(r2.groups[0], "E", "attack")
    assert m7.current() == ("E", "idle"), \
        f"[7] 组缺 attack 应回退 idle，实际 {m7.current()}"
    print("[7] OK 组视图：保持 + 缺失回退")

    # [8] 回归：首次进入（None, None）默认行为不变（SE + idle）
    d8, a8 = matrix_of_part(normal, None, None)
    assert (d8, a8) == ("SE", "idle"), f"[8] 默认应为 (SE, idle)，实际 {(d8, a8)}"
    print("[8] OK 首次进入默认 SE+idle 不变")

    print("ALL M33 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
