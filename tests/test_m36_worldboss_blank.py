# -*- coding: utf-8 -*-
"""M36：worldboss 变体识别 + 空帧检测提示。

实测背景（角色输出图，2026-09-29）：
- 13 个 `504XXX_worldboss` 文件夹只有 SE×{attack,idle,skill}，旧版名字解析失败、
  结构兜底（需 ≥2 个方向子目录）也不命中 → 整体不可见；
- 504004（黑龙王）E/N/NW/S 全部帧是全透明空图，旧版照常显示「N帧 连续」但画布
  空白，没有任何提示。

验证：
1. 模板：parts 含 worldboss；action_rules.worldboss 仅 SE 有期望动作
2. 扫描：{id}_worldboss → (id, worldboss)，并入同 ID 组；拖入单个 worldboss 文件夹可扫描
3. worldboss 部件：仅 SE 查漏；E/N/NW/S 记 unexpected（不适用），不标红缺失
4. plain+worldboss 同组：无配套异常；组级查漏/主件口径不被 worldboss 掩盖或污染
5. 只有 worldboss 的 ID：组按 worldboss 规则查漏（缺 skill → 红）
6. blank_sequences：全透明序列检出、有内容序列不误报
7. GUI：方向灰显（unexpected）、空帧红「空」角标；主窗口端到端（状态栏 ⚠ 空帧）

运行：python tests/test_m36_worldboss_blank.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from src.core.blankcheck import blank_sequences
from src.core.scanner import scan_root
from src.core.stats import group_combos
from src.core.template import load_templates

TEMPLATES = load_templates()
TPL = next(t for t in TEMPLATES if t.name == "天命装")

# 动作 → 帧号段（与实测约定一致：同方向内跨动作连续编号）
ACTION_RANGES = {
    "idle": (1, 8), "run": (9, 16), "attack": (17, 24), "skill": (25, 32),
    "hurt": (33, 33), "block": (34, 34), "dead": (35, 35),
}
FULL_ACTS = ["idle", "run", "attack", "skill", "hurt", "block", "dead"]


def _mk_seq(base: Path, direction: str, action: str, blank: bool,
            lo: int | None = None, hi: int | None = None):
    """生成一个 (方向/动作) 序列；blank=True 生成全透明占位图。"""
    a, b = ACTION_RANGES[action]
    lo = a if lo is None else lo
    hi = b if hi is None else hi
    for n in range(lo, hi + 1):
        p = base / direction / action / f"{n:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        color = (255, 0, 0, 0) if blank else (255, 0, 0, 255)
        Image.new("RGBA", (4, 4), color).save(p)


def _build_boss_plain(base: Path, blank_dirs: list[str]):
    """plain BOSS：E/N/S = idle+run；NW/SE = 7 动作。blank_dirs 中的方向全空图。"""
    for d in ("E", "N", "S"):
        for act in ("idle", "run"):
            _mk_seq(base, d, act, blank=d in blank_dirs)
    for d in ("NW", "SE"):
        for act in FULL_ACTS:
            _mk_seq(base, d, act, blank=d in blank_dirs)


def _build_worldboss(base: Path, acts: list[str] = ("attack", "idle", "skill"),
                     idle_hi: int | None = None):
    """worldboss 变体：仅 SE。idle_hi 可截短 idle 帧数（区分主件口径用）。"""
    for act in acts:
        _mk_seq(base, "SE", act, blank=False,
                **({"lo": 1, "hi": idle_hi} if act == "idle" and idle_hi else {}))


def build_tree(root: Path) -> None:
    _build_boss_plain(root / "504004", blank_dirs=["E", "N", "NW", "S"])   # 黑龙王：只有 SE 有内容
    _build_worldboss(root / "504004_worldboss", idle_hi=6)                 # 变体 idle 6帧（≠主件8帧）
    _build_boss_plain(root / "504001", blank_dirs=[])                      # 全内容 BOSS
    _build_worldboss(root / "504001_worldboss")
    _build_worldboss(root / "504016_worldboss", acts=("attack", "idle"))   # 仅变体、缺 skill


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m36_"))
    try:
        build_tree(tmp)
        res = scan_root(tmp, TPL)

        print("== 1. 模板 ==")
        assert "worldboss" in TPL.parts, "模板 parts 应含 worldboss"
        assert TPL.expected_actions("worldboss", "SE") == ["idle", "attack", "skill"]
        assert TPL.expected_actions("worldboss", "E") == []
        print("  ✓ worldboss 规则正确")

        print("== 2. worldboss 不再被忽略 ==")
        assert not [n for n in res.ignored if "worldboss" in n], \
            f"worldboss 不应被忽略，实际 ignored={res.ignored}"
        print("  ✓ 13→3 个 worldboss 文件夹全部识别（测试树 3 个）")

        print("== 3. 单拖 worldboss 文件夹 ==")
        single = scan_root(tmp / "504004_worldboss", TPL)
        assert len(single.parts) == 1 and single.parts[0].part == "worldboss"
        assert single.parts[0].res_id == "504004"
        assert len(single.groups) == 1 and single.groups[0].is_variant
        print("  ✓ 解析为 (504004, worldboss)，独立变体组")

        print("== 4. plain 主体组 + worldboss 变体主项 ==")
        g4 = next(g for g in res.groups if g.res_id == "504004" and not g.is_variant)
        assert [(p.name, p.part) for p in g4.parts] == [("504004", None)], f"实际 {g4.parts}"
        assert g4.effective_type == "non_protagonist"
        assert g4.missing_directions == [] and g4.missing_actions == {}
        assert g4.unexpected_directions == []
        assert g4.pairing_issues == []
        print("  ✓ plain 组保持主体口径，变体不再混入")

        gw = next(g for g in res.groups if g.is_variant and g.res_id == "504004")
        assert [(p.name, p.part) for p in gw.parts] == [("504004_worldboss", "worldboss")]
        assert gw.display_name == "504004_世界BOSS"
        assert gw.effective_type == "worldboss"
        assert gw.missing_directions == [] and gw.missing_actions == {}
        assert gw.unexpected_directions == ["E", "N", "NW", "S"]
        assert not gw.has_issues, "worldboss 变体符合自身约定，不应有 issues"
        wb = gw.parts[0]
        print("  ✓ worldboss 独立主项组（504004_世界BOSS），按自身规则查漏")

        print("== 5. 主件口径 / 账目 ==")
        st = group_combos(g4, TPL)
        row = next(r for r in st.rows if (r.direction, r.action) == ("SE", "idle"))
        assert row.count == 8, f"plain 主件口径应 8 帧，实际 {row.count}"
        st_w = group_combos(gw, TPL)
        row_w = next(r for r in st_w.rows if (r.direction, r.action) == ("SE", "idle"))
        assert row_w.count == 6, f"变体组账目应按自身 6 帧，实际 {row_w.count}"
        print("  ✓ plain 8 帧 / 变体 6 帧各自独立成账")

        print("== 6. 全内容 BOSS 不受影响 ==")
        g1 = next(g for g in res.groups if g.res_id == "504001")
        assert g1.missing_directions == [] and g1.missing_actions == {}
        assert g1.pairing_issues == []
        print("  ✓ 504001 显示正常")

        print("== 7. 仅 worldboss 的 ID：按 worldboss 规则查漏 ==")
        g16 = next(g for g in res.groups if g.res_id == "504016")
        assert g16.effective_type == "worldboss"
        assert g16.missing_directions == [], "E/N/NW/S 不适用，不算缺方向"
        assert g16.unexpected_directions == ["E", "N", "NW", "S"]
        assert g16.missing_actions == {"SE": ["skill"]}, f"缺 skill 应标红: {g16.missing_actions}"
        assert g16.has_issues
        print("  ✓ 缺 SE/skill 标红；其余方向灰显")

        print("== 8. 空帧检测 ==")
        plain = g4.parts[0]
        blank = blank_sequences(plain.matrix)
        expect_blank = {(d, a) for d in ("E", "N", "NW", "S") for a in plain.matrix[d]}
        assert blank == expect_blank, f"空序列检出不符: {blank ^ expect_blank}"
        assert not any(d == "SE" for d, _ in blank), "SE 有内容不应误报"
        assert blank_sequences(wb.matrix) == set(), "worldboss 帧有内容"
        print(f"  ✓ 检出 {len(blank)} 个空序列，SE 无误报")

        _gui_tests(g4, gw, wb, plain, blank, g16)
        print("\nALL M36 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _gui_tests(g4, gw, wb, plain, blank, g16):
    print("== 9. GUI：方向灰显 + 空帧「空」角标 ==")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from src.ui.button_matrix import ButtonMatrix

    m = ButtonMatrix()
    m.set_template(TPL)

    # --- worldboss 部件视图：E/N/NW/S 灰显（unexpected 非红缺失）---
    m.show_part(wb, None, None)
    btn_e = m.dir_stack._buttons["E"]
    assert bool(btn_e.property("unexpected")), "worldboss 的 E 应灰显（不适用）"
    assert not btn_e.property("missing"), "不适用方向不算红色缺失"
    assert m.dir_stack._buttons["SE"].isChecked(), "应回退到唯一可用方向 SE"
    btn_run = m.act_stack._buttons["run"]
    assert bool(btn_run.property("unexpected")), "worldboss 的 run 不适用"
    assert not m.act_stack._buttons["idle"].property("missing")
    print("  ✓ worldboss 部件：E/N/NW/S 灰显、SE 动作正常")

    # --- plain 部件视图：空帧角标 ---
    m.set_blank(blank)
    m.show_part(plain, "E", "idle")
    badge_e = m.dir_stack._buttons["E"]._badge
    assert badge_e.text() == "空", f"E 方向角标应为「空」，实际 {badge_e.text()!r}"
    badge_idle = m.act_stack._buttons["idle"]._badge
    assert badge_idle.text() == "空", f"E/idle 角标应为「空」，实际 {badge_idle.text()!r}"
    m.show_part(plain, "SE", "idle")
    assert m.dir_stack._buttons["SE"]._badge.text() == "7"
    assert m.act_stack._buttons["idle"]._badge.text() == "8", "有内容序列保持帧数角标"
    print("  ✓ 空序列红「空」角标；有内容序列帧数角标不变")

    # --- 组视图：整方向全空 → 方向角标「空」 ---
    m.set_blank(blank)
    m.show_group(g4, "E", "idle")
    assert m.dir_stack._buttons["E"]._badge.text() == "空"
    assert m.dir_stack._buttons["SE"]._badge.text() == "7"
    print("  ✓ 组视图方向角标同步「空」")

    # --- 仅 worldboss 组：缺 skill 红、E 灰 ---
    m.set_blank(set())
    m.show_group(g16, None, None)
    assert bool(m.dir_stack._buttons["E"].property("unexpected"))
    assert m.dir_stack._buttons["SE"].isChecked()
    assert m.act_stack._buttons["skill"].property("missing"), "缺 skill 应红显"
    print("  ✓ 仅 worldboss 组查漏显示正确")

    # --- 主窗口端到端：拖入根目录 → 选中 504004 组 → 等空帧线程 → 状态栏 ⚠ 空帧 ---
    print("== 10. 主窗口端到端（offscreen） ==")
    from PySide6.QtCore import QSettings
    from src.app import MainWindow
    # M29 会把 last/folder、last/selection 持久化到 QSettings（跨进程）；
    # 先快照、结束恢复，避免污染真实环境 / 后续 smoke 测试。
    settings = QSettings("MMY", "FrameQuickView")
    saved_qs = {k: settings.value(k) for k in ("last/folder", "last/selection",
                                               "display/layer_panel")}
    settings.setValue("display/layer_panel", True)   # 面板开关测试从确定态开始
    win = MainWindow()
    win.show()
    try:
        win._on_folder_dropped(g4.parts[0].folder.parent)
        # M36.1：左栏应出现「504004_世界BOSS」主项
        found = False
        for i in range(win.part_list.tree.topLevelItemCount()):
            if "504004_世界BOSS" in win.part_list.tree.topLevelItem(i).text(0):
                found = True
                break
        assert found, "左栏应有 504004_世界BOSS 主项"
        win.part_list._select_by_key("GRP:504004")
        app.processEvents()
        win._on_direction_selected("E")            # 切到 E（504004 的 E 全为空图）
        for _ in range(100):                       # 等空帧抽样线程完成
            app.processEvents()
            if not any(w.isRunning() for w in win._blank_workers):
                break
            import time; time.sleep(0.02)
        for _ in range(10):
            app.processEvents()
        assert win._blank_workers == [] or not any(w.isRunning() for w in win._blank_workers)
        cached = win._blank_cache.get(win._blank_key())
        assert cached == blank, f"端到端空帧缓存不符: {cached}"
        status = win.statusBar().currentMessage()
        assert "空帧" in status, f"状态栏应含空帧提示: {status}"
        # 叠层渲染部件：worldboss 不计入（plain 组 1 层）
        assert len(win._render_parts(g4)) == 1

        # M36.1：选中变体主项组 → HUD/状态栏
        win.part_list._select_by_key("GRP:" + gw.key)
        app.processEvents()
        assert win._group is not None and win._group.is_variant
        assert "504004_世界BOSS" in win.statusBar().currentMessage()

        # M36.2：显示层面板折叠开关（底部「☰ 显示层」按钮）
        layer_btn = win.anim_view._layer_btn
        assert win.anim_view._toggles.isVisible(), "组视图下面板应默认可见"
        layer_btn.setChecked(False)
        app.processEvents()
        assert not win.anim_view._toggles.isVisible(), "关闭开关后面板应收起"
        assert settings.value("display/layer_panel", True, type=bool) is False, \
            "开关状态应持久化到 QSettings"
        layer_btn.setChecked(True)
        app.processEvents()
        assert win.anim_view._toggles.isVisible(), "重新打开后面板应恢复"
    finally:
        win.close()
        for k, v in saved_qs.items():
            if v is None:
                settings.remove(k)
            else:
                settings.setValue(k, v)
        settings.sync()
    print("  ✓ 端到端：空帧缓存 + 状态栏 ⚠ 空帧")


if __name__ == "__main__":
    main()
