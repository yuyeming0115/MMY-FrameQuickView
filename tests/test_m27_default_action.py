"""M27 测试：默认播放动作按类型优化（idle / ride_idle）。

背景：原实现取 sorted(可用动作)[0]，主角 E 方向约定动作是
[idle, attack, run, ride_idle, ride_run]，字母序第一个是 attack，
打开就播攻击动画。改为按类型优先：常规类型 idle、坐骑 ride_idle。

覆盖：
1. 主角（protagonist）：E 有 idle/attack/run → 默认 idle（而非 attack）
2. 坐骑（mount）：默认 ride_idle
3. 翅膀（wings）：默认 idle
4. NPC / 伙伴：默认 idle
5. 优先动作缺失 → 回退字母序第一个
6. pick_default_action 纯函数直测
7. 组视图（show_group）与单部件视图（show_part）行为一致
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
from PySide6.QtWidgets import QApplication

from src.core.scanner import scan_root
from src.core.template import load_templates
from src.ui.button_matrix import ButtonMatrix, pick_default_action

tpl = load_templates()[0]
app = QApplication.instance() or QApplication([])


def make_png(path: Path) -> None:
    img = Image.new("RGBA", (10, 10), (120, 200, 90, 255))
    img.save(path)


def make_act(parent: Path, name: str, direction: str, action: str, n: int = 2) -> None:
    d = parent / name / direction / action
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        make_png(d / f"{i:04d}.png")


def default_action_of(part) -> tuple[str | None, str | None]:
    """用 ButtonMatrix 取某部件首次进入时的默认 (方向, 动作)。"""
    m = ButtonMatrix()
    m.set_template(tpl)
    m.show_part(part, None, None)
    return m.current()


tmp = Path(tempfile.mkdtemp())
try:
    # ---- 1) 主角：E 有 idle/attack/run → 默认 idle（不是字母序的 attack）----
    hero = tmp / "hero"
    make_act(hero, "50112151_body", "E", "idle")
    make_act(hero, "50112151_body", "E", "attack")
    make_act(hero, "50112151_body", "E", "run")
    p = scan_root(hero, tpl).parts[0]
    d, a = default_action_of(p)
    assert a == "idle", f"主角默认动作应为 idle，实际 {a}（可用 {sorted(p.available_actions(d))}）"
    print(f"[1] OK 主角: 方向={d} 动作={a}（可用 {sorted(p.available_actions(d))}）")

    # ---- 2) 坐骑：默认 ride_idle ----
    mount = tmp / "mount"
    make_act(mount, "50201101_ride_front", "E", "ride_idle")
    make_act(mount, "50201101_ride_front", "E", "ride_run")
    p2 = scan_root(mount, tpl).parts[0]
    d2, a2 = default_action_of(p2)
    assert a2 == "ride_idle", f"坐骑默认动作应为 ride_idle，实际 {a2}"
    print(f"[2] OK 坐骑: 动作={a2}（类型 {p2.effective_type}）")

    # ---- 3) 翅膀：默认 idle ----
    wing = tmp / "wing"
    make_act(wing, "50106101_wings", "E", "idle")
    make_act(wing, "50106101_wings", "E", "run")
    p3 = scan_root(wing, tpl).parts[0]
    d3, a3 = default_action_of(p3)
    assert a3 == "idle", f"翅膀默认动作应为 idle，实际 {a3}"
    print(f"[3] OK 翅膀: 动作={a3}（类型 {p3.effective_type}）")

    # ---- 4) NPC：默认 idle ----
    npc = tmp / "npc"
    make_act(npc, "50509901_body", "E", "idle")
    make_act(npc, "50509901_body", "E", "run")
    p4 = scan_root(npc, tpl).parts[0]
    d4, a4 = default_action_of(p4)
    assert a4 == "idle", f"NPC 默认动作应为 idle，实际 {a4}"
    print(f"[4] OK NPC: 动作={a4}（类型 {p4.effective_type}）")

    # ---- 5) 伙伴（502）：默认 idle ----
    mate = tmp / "mate"
    make_act(mate, "50201102_body", "E", "idle")
    make_act(mate, "50201102_body", "E", "run")
    p5 = scan_root(mate, tpl).parts[0]
    d5, a5 = default_action_of(p5)
    assert a5 == "idle", f"伙伴默认动作应为 idle，实际 {a5}"
    print(f"[5] OK 伙伴: 动作={a5}（类型 {p5.effective_type}）")

    # ---- 6) 优先动作缺失 → 回退字母序第一个 ----
    noidle = tmp / "noidle"
    make_act(noidle, "50112152_body", "E", "attack")
    make_act(noidle, "50112152_body", "E", "run")
    p6 = scan_root(noidle, tpl).parts[0]
    d6, a6 = default_action_of(p6)
    assert a6 == "attack", f"idle 缺失时应回退字母序第一个 attack，实际 {a6}"
    print(f"[6] OK 回退: 无 idle → {a6}")

    # ---- 7) pick_default_action 纯函数 ----
    assert pick_default_action("protagonist", ["attack", "idle", "run"]) == "idle"
    assert pick_default_action("mount", ["ride_idle", "ride_run"]) == "ride_idle"
    assert pick_default_action("wings", ["idle", "run"]) == "idle"
    assert pick_default_action("npc", ["idle", "run"]) == "idle"
    assert pick_default_action("non_protagonist", ["idle", "run"]) == "idle"
    assert pick_default_action("protagonist", ["run"]) == "run"      # idle 缺失回退
    assert pick_default_action("protagonist", []) is None
    print("[7] OK pick_default_action 纯函数")

    # ---- 8) 组视图与单部件一致 ----
    outfit = tmp / "501521005_女主2_部件"
    make_act(outfit, "501031005_shadow", "E", "idle")
    make_act(outfit, "501031005_shadow", "E", "attack")
    make_act(outfit, "501521005_body", "E", "idle")
    make_act(outfit, "501521005_body", "E", "attack")
    g8 = next(g for g in scan_root(outfit, tpl).groups if g.is_outfit)
    m8 = ButtonMatrix()
    m8.set_template(tpl)
    m8.show_group(g8, None, None)
    d8, a8 = m8.current()
    assert a8 == "idle", f"组视图默认动作应为 idle，实际 {a8}"
    print(f"[8] OK 组视图: 方向={d8} 动作={a8}")

    # ---- 9) 坐骑影子：只有 ride_idle/ride_run，却被当作非坐骑类型查漏
    #        → 约定动作(idle/run/attack…)一个都不中。修复前默认动作= None
    #        （B 区不播放）；修复后应回退到实际动作 ride_idle。 ----
    mount_shadow = tmp / "mount_shadow"
    make_act(mount_shadow, "50302301_shadow", "SE", "ride_idle")
    make_act(mount_shadow, "50302301_shadow", "SE", "ride_run")
    ps = scan_root(mount_shadow, tpl).parts[0]
    d9, a9 = default_action_of(ps)
    assert a9 == "ride_idle", f"坐骑影子默认动作应为 ride_idle，实际 {a9}（修复前为 None）"
    print(f"[9] OK 坐骑影子回退: 方向={d9} 动作={a9}（eff_type={ps.effective_type or '空'}）")

    print("ALL M27 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
