# -*- coding: utf-8 -*-
"""M45 阶段 A 第一步：逐帧画面「标准答案」录制 / 比对脚本。

为什么要有它
------------
M45 要把「叠哪些层」（app._layers_for_current）和「怎么渲染一串帧」（DecodeWorker.run）
从界面代码里搬到 core/compose.py、core/render.py。搬代码最怕「看起来没改，其实画面变了」：
层的顺序、穿戴武器的对齐方式、特效居中，任何一点不同都会让预览变样。

所以先在**改代码之前**跑一遍 record，把真实资源每一帧画面的「指纹」（哈希值）存下来；
改完之后跑 check，指纹必须 100% 一致，才算没改坏。

它会做什么 / 不会做什么
------------------------
- ✅ 只读：扫描资源目录、合成每一帧、算指纹、写一个 JSON 文件
- ✅ 设置隔离：用临时 ini 代替注册表里的真实设置，不会改动你的 QSettings
- ✅ 匹配表只读：关掉「新 ID 自动登记」，不会往 *匹配表*.txt 里写东西
- ❌ 不修改、不移动、不删除任何资源文件

用法（在项目根目录，Git Bash）
------------------------------
    # 改代码前：录制标准答案（默认用 E:\\XYJProject\\美术资源\\动画序列帧\\角色输出图）
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/golden_frames.py record

    # 改代码后：比对（全部一致 → exit=0；有差异 → 列出差异，exit=1）
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/golden_frames.py check

    # 可选参数
    #   --root 目录        要录制的资源目录，可写多次（比如再加一个 TexturePacker 图集目录）
    #   --per-cat 2        每种资源类型抽几个（越多越慢）
    #   --max-combos 10    每个资源最多录几个「方向×动作」组合
    #   --out 路径         标准答案文件位置（默认 tests/golden/m45_frames.json）

⚠ 改代码时请保留下面两个「接口适配」函数能用：
    current_recipe(win)  —— 现在调用 win._layers_for_current()
    render(...)          —— 现在调用 DecodeWorker(...).run()
如果重构后这两个入口改了名字/参数，只需要改这两个函数，其它部分不用动。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")       # 不弹窗口
ROOT_DIR = Path(__file__).resolve().parent.parent           # 项目根目录
sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtCore import QSettings                        # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

DEFAULT_ROOT = r"E:\XYJProject\美术资源\动画序列帧\角色输出图"
DEFAULT_OUT = ROOT_DIR / "tests" / "golden" / "m45_frames.json"


# =====================================================================
# 接口适配：重构后如有改名，只改这两个函数
# =====================================================================
def current_recipe(win):
    """当前选中项 + 当前方向/动作 → (layers, flat_mask, fx_offsets, part_keys)。"""
    return win._layers_for_current()


def render(layers, flat_mask, fx_offsets, canvas_align: bool) -> list:
    """把叠层配方渲染成一串帧，返回 [(PIL.Image, 帧号标签), ...]。

    直接在当前线程调用 DecodeWorker.run()（不起后台线程），
    它每合成一帧就发一次 frame_ready 信号，这里把结果收集起来。
    """
    from src.ui.worker import DecodeWorker
    out: list = []
    w = DecodeWorker(layers, flat_mask=flat_mask, fx_offsets=fx_offsets,
                     canvas_align=canvas_align)
    w.frame_ready.connect(lambda _i, img, label: out.append((img, label)))
    w.run()
    return out


# =====================================================================
# 小工具
# =====================================================================
def img_hash(img) -> str:
    """一帧画面的「指纹」：尺寸 + 颜色模式 + 全部像素，任何一个像素不同指纹就不同。"""
    h = hashlib.sha1()
    h.update(f"{img.mode}|{img.size[0]}x{img.size[1]}|".encode())
    h.update(img.tobytes())
    return h.hexdigest()[:16]      # 取前 16 位就够区分了，JSON 小一点


def recipe_hash(layers, flat_mask, fx_offsets, part_keys) -> str:
    """叠层配方的指纹：每层用了哪些帧文件、哪些是特效层、偏移多少、层名顺序。

    画面指纹不一致时，先看配方指纹——配方不同说明是「叠哪些层」搬错了，
    配方相同画面不同说明是「渲染对齐」搬错了，能快速缩小排查范围。
    """
    data = {
        "layers": [[str(p) for p in layer] for layer in layers],
        "flat": list(flat_mask),
        "off": {str(k): list(v) for k, v in sorted((fx_offsets or {}).items())},
        "keys": list(part_keys),
    }
    return hashlib.sha1(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def settle(app, win) -> None:
    """等后台线程（A/B 区解码）跑完，避免和本脚本的同步渲染抢资源。"""
    for _ in range(300):
        app.processEvents()
        busy = [w for w in (win.grid_view._worker, win.anim_view._worker)
                if w is not None and w.isRunning()]
        if not busy:
            break
        time.sleep(0.02)
    app.processEvents()


# =====================================================================
# 抽样：从扫描结果里按「资源类型」各挑几个，覆盖所有叠层分支
# =====================================================================
def pick_targets(win, per_cat: int) -> list[dict]:
    """返回要录制的对象清单。每项是一个可以存进 JSON 的描述（重跑 check 时按它找回对象）。"""
    groups = sorted(win._result.groups, key=lambda g: g.key)

    def render_n(g):
        return len(win._render_parts(g))

    cats: dict[str, list] = {
        # 纯 ID 单部件（怪物/NPC/伙伴，如宝箱怪）
        "single": [g for g in groups if not (g.is_outfit or g.is_variant or g.is_flat)
                   and render_n(g) == 1 and g.parts[0].part is None],
        # 同 ID 多部件叠层（如 body + shadow）
        "id_multi": [g for g in groups if not (g.is_outfit or g.is_variant or g.is_flat)
                     and render_n(g) >= 2],
        # 跨 ID 形象组合（M37：时装 + 发 + 武器靠中文名前缀配对）
        "look_combo": [g for g in groups if not g.is_flat
                       and len(win._display_parts(g)) > render_n(g)],
        # 套装组（M23：同父文件夹跨 ID 部件，含组内特效 M25）
        "outfit": [g for g in groups if g.is_outfit],
        # 世界 BOSS 变体（M36.1）
        "variant": [g for g in groups if g.is_variant],
        # 扁平特效（M21）
        "flat_fx": [g for g in groups if g.is_flat],
        # 翅膀 / 坐骑
        "wings": [g for g in groups if any(p.part == "wings" for p in g.parts)],
        "ride": [g for g in groups if any((p.part or "").startswith("ride") for p in g.parts)],
        # 自动推断穿戴武器（M42：时装名里有「剑」「斧」→ 自动配 0 阶武器）
        "auto_weapon": [g for g in groups if win._auto_weapon_key(g)],
    }
    targets: list[dict] = []
    for cat, lst in cats.items():
        for g in lst[:per_cat]:
            targets.append({"cat": cat, "kind": "group", "key": g.key})

    # 单部件视图（左栏点开组、选中其中一个部件）
    multi = cats["id_multi"] or cats["outfit"]
    if multi:
        p = multi[0].parts[0]
        targets.append({"cat": "part_view", "kind": "part", "key": str(p.folder)})

    # 手动穿戴翅膀 + 特效（M26/M28）：只改内存里的选择，不写设置
    modular = [g for g in groups if not (g.is_outfit or g.is_variant or g.is_flat)
               and win._is_modular_look(win._display_parts(g))]
    if modular and (win._wing_library or win._fx_library):
        targets.append({
            "cat": "dress_wing_fx", "kind": "group", "key": modular[0].key,
            "wing": win._wing_library[0].name if win._wing_library else "",
            "fx": win._fx_library[0].name if win._fx_library else "",
        })
    return targets


def combos_for(win, target) -> list[tuple[str, str]]:
    """这个对象拥有的全部「方向×动作」组合（多部件取并集），按字母序固定顺序。"""
    if target["kind"] == "part":
        parts = [target["_obj"]]
    else:
        parts = win._display_parts(target["_obj"])
    out = set()
    for p in parts:
        for d, col in p.matrix.items():
            for a in col:
                out.add((d, a))
    return sorted(out)


def spread(items: list, n: int) -> list:
    """从列表里均匀挑 n 个（固定挑法，record 和 check 挑到的一样）。"""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# =====================================================================
# 录制 / 比对核心
# =====================================================================
def apply_target(win, target) -> bool:
    """把窗口状态切到这个对象（不触发界面解码，只改内部状态）。找不到对象返回 False。"""
    if target["kind"] == "group":
        g = next((x for x in win._result.groups if x.key == target["key"]), None)
        if g is None:
            return False
        win._group, win._part = g, None
        target["_obj"] = g
        # 手动穿戴：只放进内存字典，录完再还原
        if target.get("wing"):
            win._dressed_wings[g.key] = target["wing"]
        if target.get("fx"):
            win._dressed_fx[g.key] = target["fx"]
    else:
        p = next((x for x in win._result.parts if str(x.folder) == target["key"]), None)
        if p is None:
            return False
        win._group, win._part = None, p
        target["_obj"] = p
    return True


def undo_target(win, target) -> None:
    """还原 apply_target 里临时加的手动穿戴，不影响后面的对象。"""
    if target["kind"] != "group":
        return
    if target.get("wing"):
        win._dressed_wings.pop(target["key"], None)
    if target.get("fx"):
        win._dressed_fx.pop(target["key"], None)


def run_cases(app, win, targets: list[dict], max_combos: int, fixed_combos: dict | None = None) -> dict:
    """逐个对象、逐个组合合成画面并记录指纹。

    fixed_combos：check 模式下传入 —— 直接用标准答案里录过的组合，保证两次比的是同一批。
    """
    cases: dict = {}
    t0 = time.time()
    for n, target in enumerate(targets, 1):
        if not apply_target(win, target):
            print(f"  [{n}/{len(targets)}] ⚠ 找不到 {target['cat']} {target['key']}，跳过")
            continue
        tid = f"{target['cat']}|{target['key']}"
        if fixed_combos is not None:
            combos = [tuple(c) for c in fixed_combos.get(tid, [])]
        else:
            combos = spread(combos_for(win, target), max_combos)
        done = 0
        for d, a in combos:
            win._update_matrix(d, a)                  # 让按钮矩阵选中这个方向/动作
            if win.matrix.current() != (d, a):         # 这个组合被兜底改成了别的 → 不录
                continue
            layers, fm, off, keys = current_recipe(win)
            if not layers:
                continue
            case = {
                "keys": list(keys), "flat": list(fm),
                "recipe": recipe_hash(layers, fm, off, keys),
                "frames": {},
            }
            # 两种对齐模式都录：画布对齐（M34，默认）和并集 bbox
            for mode, align in (("canvas", True), ("bbox", False)):
                frames = render(layers, fm, off, align)
                case["frames"][mode] = [img_hash(img) + ":" + label for img, label in frames]
            cases[f"{tid}|{d}|{a}"] = case
            done += 1
        undo_target(win, target)
        print(f"  [{n}/{len(targets)}] {target['cat']:<13} {Path(target['key']).name:<28} "
              f"{done} 个组合 · 累计 {time.time() - t0:.0f}s")
    return cases


def open_window(app, ini: Path):
    """开一个隔离的主窗口并加载资源目录。"""
    import src.app as appmod
    from src.core.namemap import NameMap
    # 设置隔离：所有 QSettings 读写都落到临时 ini，不碰注册表里的真实配置
    appmod.QSettings = lambda *a, **k: QSettings(str(ini), QSettings.IniFormat)
    # 匹配表只读：关掉「新 ID 自动登记」（否则会往 *匹配表*.txt 末尾追加内容）
    NameMap.register_missing = lambda self, *a, **k: []
    win = appmod.MainWindow()
    win._sync_blank_check = lambda: None      # 不需要空帧抽样（后台线程），省时间
    return win


def main() -> int:
    ap = argparse.ArgumentParser(description="M45 逐帧画面标准答案：record / check")
    ap.add_argument("mode", choices=["record", "check"])
    ap.add_argument("--root", action="append", help="资源目录（可写多次）")
    ap.add_argument("--per-cat", type=int, default=2)
    ap.add_argument("--max-combos", type=int, default=10)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_path = Path(args.out)
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="m45_golden_"))      # 临时设置文件放这里（不在资源目录里）
    try:
        golden = None
        if args.mode == "check":
            if not out_path.exists():
                print(f"❌ 找不到标准答案 {out_path}，请先在改代码前跑 record")
                return 2
            golden = json.loads(out_path.read_text(encoding="utf-8"))
            roots = [Path(r) for r in golden["meta"]["roots"]]
        else:
            roots = [Path(r) for r in (args.root or [DEFAULT_ROOT])]

        all_cases: dict = {}
        all_targets: dict = {}
        for i, root in enumerate(roots):
            if not root.is_dir():
                print(f"❌ 目录不存在：{root}")
                return 2
            print(f"== 扫描 {root}")
            # 每个目录用一份全新的设置文件：否则上一个目录记下的「最近目录」
            # 会让新窗口启动时自动加载上一个目录
            win = open_window(app, tmp / f"settings_{i}.ini")
            win._on_folder_dropped(root)
            settle(app, win)
            print(f"   共 {len(win._result.groups)} 组资源")

            if golden is None:
                targets = pick_targets(win, args.per_cat)
                fixed = None
            else:
                targets = [dict(t) for t in golden["meta"]["targets"][str(root)]]
                fixed = golden["meta"]["combos"]
            all_targets[str(root)] = [{k: v for k, v in t.items() if not k.startswith("_")}
                                      for t in targets]
            all_cases.update(run_cases(app, win, targets, args.max_combos, fixed))
            win.close()

        if golden is None:
            # ---- record：写标准答案 ----
            combos: dict = {}
            for cid in all_cases:
                tid, d, a = cid.rsplit("|", 2)
                combos.setdefault(tid, []).append([d, a])
            data = {
                "meta": {
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "roots": [str(r) for r in roots],
                    "targets": all_targets,
                    "combos": combos,
                },
                "cases": all_cases,
            }
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            n_frames = sum(len(v) for c in all_cases.values() for v in c["frames"].values())
            print(f"\n✅ 已录制 {len(all_cases)} 个组合、{n_frames} 帧 → {out_path}")
            return 0

        # ---- check：逐项比对 ----
        want = golden["cases"]
        diffs: list[str] = []
        for cid, exp in want.items():
            got = all_cases.get(cid)
            if got is None:
                diffs.append(f"缺少  {cid}（这次没渲染出来）")
                continue
            if got["recipe"] != exp["recipe"]:
                diffs.append(f"配方不同 {cid}\n      旧层序 {exp['keys']}\n      新层序 {got['keys']}"
                             f"\n      旧 flat {exp['flat']}  新 flat {got['flat']}")
                continue
            for mode in ("canvas", "bbox"):
                if got["frames"][mode] != exp["frames"][mode]:
                    bad = [i for i, (x, y) in enumerate(zip(exp["frames"][mode], got["frames"][mode]))
                           if x != y]
                    diffs.append(f"画面不同 {cid} [{mode}] 帧数 {len(exp['frames'][mode])}→"
                                 f"{len(got['frames'][mode])}，不同的帧：{bad[:8]}")
        extra = [c for c in all_cases if c not in want]
        print(f"\n比对 {len(want)} 个组合：{len(want) - len(diffs)} 个一致，{len(diffs)} 个不同")
        for d in diffs[:30]:
            print("  ✗", d)
        if extra:
            print(f"  （另有 {len(extra)} 个组合是新出现的，不计入比对）")
        if diffs:
            return 1
        print("✅ 全部一致：重构没有改变任何画面")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
