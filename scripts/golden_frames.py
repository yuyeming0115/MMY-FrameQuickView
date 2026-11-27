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

    # 自检：不碰真实资源和正式标准答案，自己生成一份小资源，验证本脚本的判定逻辑（约 20 秒）
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/golden_frames.py selftest

比对结果分三种
--------------
- 一致：画面和标准答案逐像素相同
- ⚠ 资源已变（不算失败）：标准答案当时用到的帧文件，现在不存在了或内容变了
  （美术改名/重导/删除资源）。这种差异不是代码造成的，资源稳定后重新 record 即可。
- ✗ 不同（算失败）：当时用到的帧文件都还在、都没变，画面却不一样了 → 是代码改坏了
  （「配方不同」= 叠哪些层变了；「画面不同」= 渲染/对齐变了）

注意：匹配表、规则模板变了也可能让画面变，但没法精确归到某个组合，所以只在结果里提示一句，
判定时不排除。另外，资源目录里「新增」的文件（旧答案没用到）不会被当成资源变化。

    # 可选参数
    #   --root 目录        要录制的资源目录，可写多次（比如再加一个 TexturePacker 图集目录）
    #   --per-cat 2        每种资源类型抽几个（越多越慢）
    #   --max-combos 10    每个资源最多录几个「方向×动作」组合
    #   --out 路径         标准答案文件位置（默认 tests/golden/m45_frames.json）
    #   --no-fixture       不加自动生成的套装目录（默认会加，用来覆盖套装合并 M23 / 套装内特效 M25）

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
# 自动生成的「套装」小目录：真实目录里主角套装都已拆成单独文件夹，不会触发
# 套装合并（M23）和套装内特效（M25），但这两段代码重构时也要搬，所以用它兜底。
# 每次运行都重新生成，像素完全一样；已加入 .gitignore，不用提交。
FIXTURE_DIR = ROOT_DIR / "tests" / "golden" / "fixture_outfit"


def build_fixture(root: Path) -> None:
    """生成固定内容的套装目录（record 和 check 前都会调用，保证两次一模一样）。

    结构（和 AGENTS.md 里的套装示例一致）：
        fixture_outfit/
        ├── 501521005_女侠客_部件/          ← 套装 1：5 个跨 ID 部件 + 1 个特效
        │   ├── 501031005_shadow  501031005_weapon  501511005_hair
        │   ├── 501521005_body    501521005_shadow
        │   └── 50105101/changrao_1.png …  ← 扁平特效（M25：并入套装、置顶）
        └── 月河郡主（琴）/                  ← 套装 2：纯中文名、无特效
            └── 501031004_shadow  501031004_weapon  501421004_body
    每个部件：E / SE 两个方向 × idle(8 帧) / attack(6 帧)，画布 200×200。
    各部件位置、大小、半透明程度都不同，并且逐帧移动，这样对齐算法哪里错了都能看出来。
    """
    from PIL import Image, ImageDraw
    if root.exists():
        shutil.rmtree(root)
    W = H = 200

    def draw_frames(folder: Path, names: list[str], box_of, color) -> None:
        """在 folder 里按 names 逐帧画一个矩形；box_of(第几帧) 返回矩形位置。"""
        folder.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(names):
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            x0, y0, x1, y1 = box_of(i)
            ImageDraw.Draw(img).rectangle([x0, y0, x1 - 1, y1 - 1], fill=color)
            img.save(folder / name)

    # 各部件在画布上的基础位置 (x0, y0, x1, y1) 和颜色（第 4 个数是透明度）
    looks = {
        "shadow_body":   ((70, 150, 130, 162), (0, 0, 0, 110)),       # 半透明影子
        "shadow_weapon": ((30, 150, 70, 158),  (0, 0, 0, 90)),
        "body":          ((80, 60, 120, 150),  (200, 80, 60, 255)),
        "hair":          ((82, 45, 118, 70),   (60, 40, 30, 255)),
        "weapon":        ((35, 90, 80, 110),   (220, 200, 90, 230)),
    }
    actions = [("idle", 8), ("attack", 6)]

    def make_part(folder: Path, look: str, step: int) -> None:
        (bx0, by0, bx1, by1), color = looks[look]
        for di, direction in enumerate(("E", "SE")):
            num = 1                        # 同方向内跨动作连续编号（idle 0001-0008，attack 0009-0014）
            for ai, (action, n) in enumerate(actions):
                names = [f"{num + k:04d}.png" for k in range(n)]
                num += n
                dx0 = di * 12 + ai * 5     # 不同方向/动作整体错开一点
                draw_frames(folder / direction / action, names,
                            lambda k: (bx0 + dx0 + k * step, by0, bx1 + dx0 + k * step, by1), color)

    o1 = root / "501521005_女侠客_部件"
    make_part(o1 / "501031005_shadow", "shadow_weapon", 2)
    make_part(o1 / "501031005_weapon", "weapon", 3)
    make_part(o1 / "501511005_hair", "hair", 1)
    make_part(o1 / "501521005_body", "body", 1)
    make_part(o1 / "501521005_shadow", "shadow_body", 1)
    # 扁平特效：5 帧，每帧大小不同（检验「按整条序列并集居中、不抖动」）
    draw_frames(o1 / "50105101", [f"changrao_{k + 1}.png" for k in range(5)],
                lambda k: (60 - k * 6, 40 - k * 4, 100 + k * 10, 90 + k * 8), (90, 140, 240, 160))

    o2 = root / "月河郡主（琴）"
    make_part(o2 / "501031004_shadow", "shadow_weapon", 2)
    make_part(o2 / "501031004_weapon", "weapon", 3)
    make_part(o2 / "501421004_body", "body", 1)


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
# 资源指纹：判断「旧答案用到的帧文件」现在还在不在、变没变
# =====================================================================
def source_files(layers) -> set[str]:
    """一个叠层配方用到的所有磁盘文件。

    散帧：每帧就是一个 PNG 文件；
    图集帧（M40 TexturePacker）：一帧来自一张大图，记它的 PNG 和 PLIST 两个文件。
    """
    out: set[str] = set()
    for layer in layers:
        for p in layer:
            if p is None:
                continue
            if hasattr(p, "png_path") and hasattr(p, "plist_path"):
                out.add(str(p.png_path))
                out.add(str(p.plist_path))
            else:
                out.add(str(p))
    return out


def _in_fixture(path: str) -> bool:
    """这个文件是不是在自动生成的套装目录（fixture_outfit）里。"""
    base = os.path.normcase(str(FIXTURE_DIR)) + os.sep
    return os.path.normcase(path).startswith(base)


def file_stamp(path: str) -> list | None:
    """文件的「指纹」：[大小, 修改时间或内容哈希]；文件不存在返回 None。

    普通资源用「大小 + 修改时间」判断变没变：快，不用读文件内容。
    自动生成的套装目录每次运行都会重新生成，修改时间每次都不一样，
    所以改用「内容哈希」：内容一样就算没变。
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    if _in_fixture(path):
        with open(path, "rb") as f:
            return [st.st_size, "sha:" + hashlib.sha1(f.read()).hexdigest()[:16]]
    return [st.st_size, f"mt:{st.st_mtime_ns}"]


def changed_inputs(exp: dict, files: list, cache: dict) -> list[tuple[str, str]]:
    """标准答案里这个组合当时用到的文件，现在「不存在了」或「内容变了」的有哪些。

    exp["inputs"] 是文件编号列表，files 是录制时的文件总表 [[路径, 大小, 时间戳], ...]。
    cache：同一次比对里很多组合共用同一批文件，查过的直接复用，不重复读磁盘。
    """
    out: list[tuple[str, str]] = []
    for idx in exp.get("inputs", []):
        path, size, stamp = files[idx]
        if path not in cache:
            cache[path] = file_stamp(path)
        now = cache[path]
        if now is None:
            out.append((path, "不存在了"))
        elif now != [size, stamp]:
            out.append((path, "内容变了"))
    return out


def env_stamps(win) -> dict:
    """录制时的「环境文件」指纹：匹配表、规则模板。

    它们变了也会让画面变（比如改了中文名，形象组合 M37 就会变），但没法精确归到某个组合，
    所以比对时只提示一句，不改变判定。
    """
    out: dict = {}
    nm = getattr(getattr(win, "_namemap", None), "path", None)
    if nm:
        out[str(nm)] = file_stamp(str(nm))
    from src.core.template import templates_dir
    for fp in sorted(templates_dir().glob("*.json")):
        out[str(fp)] = file_stamp(str(fp))
    return out


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


def run_cases(app, win, targets: list[dict], max_combos: int, fixed_combos: dict | None = None,
              tag: str = "") -> dict:
    """逐个对象、逐个组合合成画面并记录指纹。

    fixed_combos：check 模式下传入 —— 直接用标准答案里录过的组合，保证两次比的是同一批。
    """
    cases: dict = {}
    t0 = time.time()
    for n, target in enumerate(targets, 1):
        if not apply_target(win, target):
            print(f"  [{n}/{len(targets)}] ⚠ 找不到 {target['cat']} {target['key']}，跳过")
            continue
        # tag = 资源目录名：local-role 是由角色输出图打包的，两边 ID 完全相同，
        # 不带目录名的话后录的会覆盖先录的（首次补录时 148 个组合被覆盖成 36 个）
        tid = f"{tag}|{target['cat']}|{target['key']}"
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
                # 这个组合用到了哪些磁盘文件（录制时换成文件编号存进总表，见 do_record）
                "_inputs": sorted(source_files(layers)),
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


def collect(app, roots: list[Path], tmp: Path, per_cat: int, max_combos: int,
            golden: dict | None = None):
    """依次扫描每个资源目录，渲染并记录组合。

    golden 为 None：录制模式，自己挑要录哪些对象；
    golden 不为 None：比对模式，严格按标准答案里记下的对象和组合重渲染。
    返回 (全部组合, 每个目录挑了哪些对象, 环境文件指纹)；目录不存在等错误返回 None。
    """
    all_cases: dict = {}
    all_targets: dict = {}
    env: dict = {}
    used_tags: set[str] = set()
    for i, root in enumerate(roots):
        # 每个目录一个短标签（目录名），重名时加序号
        tag = root.name if root.name not in used_tags else f"{root.name}#{i}"
        used_tags.add(tag)
        if root == FIXTURE_DIR:
            build_fixture(root)            # 每次重新生成，像素和上次完全一样
        if not root.is_dir():
            print(f"❌ 目录不存在：{root}")
            return None
        print(f"== 扫描 {root}")
        # 每个目录用一份全新的设置文件：否则上一个目录记下的「最近目录」
        # 会让新窗口启动时自动加载上一个目录
        win = open_window(app, tmp / f"settings_{i}.ini")
        win._on_folder_dropped(root)
        settle(app, win)
        print(f"   共 {len(win._result.groups)} 组资源")

        if golden is None:
            targets = pick_targets(win, per_cat)
            fixed = None
        else:
            targets = [dict(t) for t in golden["meta"]["targets"].get(str(root), [])]
            fixed = golden["meta"]["combos"]
        all_targets[str(root)] = [{k: v for k, v in t.items() if not k.startswith("_")}
                                  for t in targets]
        got = run_cases(app, win, targets, max_combos, fixed, tag=tag)
        overlap = set(got) & set(all_cases)
        if overlap:                          # 保险：理论上不会再发生
            print(f"❌ 组合编号重复 {len(overlap)} 个，例如 {next(iter(overlap))}")
            return None
        all_cases.update(got)
        env.update(env_stamps(win))
        win.close()
    return all_cases, all_targets, env


def do_record(app, roots: list[Path], out_path: Path, per_cat: int, max_combos: int) -> int:
    """录制标准答案：渲染 → 记画面指纹 + 每个组合用到的文件指纹 → 写 JSON。"""
    tmp = Path(tempfile.mkdtemp(prefix="m45_golden_"))      # 临时设置文件放这里（不在资源目录里）
    try:
        got = collect(app, roots, tmp, per_cat, max_combos)
        if got is None:
            return 2
        all_cases, all_targets, env = got

        # 文件总表：同一个帧文件会被很多组合用到（两种对齐模式、穿戴组合…），
        # 总表里只记一次，组合里只存编号，JSON 小很多
        files: list = []
        index: dict[str, int] = {}
        for case in all_cases.values():
            ids = []
            for path in case.pop("_inputs", []):
                if path not in index:
                    index[path] = len(files)
                    files.append([path] + (file_stamp(path) or [0, "missing"]))
                ids.append(index[path])
            case["inputs"] = ids

        combos: dict = {}
        for cid in all_cases:
            tid, d, a = cid.rsplit("|", 2)
            combos.setdefault(tid, []).append([d, a])
        data = {
            "meta": {
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "format": 2,                     # 2 = 带资源指纹（能区分「资源已变」）
                "roots": [str(r) for r in roots],
                "targets": all_targets,
                "combos": combos,
                "files": files,
                "env": env,
            },
            "cases": all_cases,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        n_frames = sum(len(v) for c in all_cases.values() for v in c["frames"].values())
        per_root: dict = {}
        for cid in all_cases:
            per_root[cid.split("|", 1)[0]] = per_root.get(cid.split("|", 1)[0], 0) + 1
        print(f"\n✅ 已录制 {len(all_cases)} 个组合、{n_frames} 帧、{len(files)} 个资源文件指纹 → {out_path}")
        print("   各目录组合数：" + "，".join(f"{k} {v}" for k, v in per_root.items()))

        # 附带检查（只提示，不算失败）：图集目录是由散帧目录打包的，同一个资源
        # 在两边合成出的画面理论上应该一模一样（M40 宣称「像素级 0 误差」还原）
        body: dict = {}
        for cid, c in all_cases.items():
            tag_, rest = cid.split("|", 1)
            body.setdefault(rest, {})[tag_] = c["frames"]
        pairs_ = [v for v in body.values() if len(v) >= 2]
        if pairs_:
            def pixels_only(frames: dict) -> str:
                # 只比像素指纹（冒号前），不比帧号标签：图集帧和散帧文件的名字写法可能不同
                return json.dumps({m: [s.split(":", 1)[0] for s in v] for m, v in frames.items()},
                                  sort_keys=True)
            same = sum(1 for v in pairs_ if len({pixels_only(x) for x in v.values()}) == 1)
            print(f"   附带：{len(pairs_)} 个组合在两个目录都录到，其中 {same} 个画面完全相同"
                  + ("（图集还原与散帧一致 ✓）" if same == len(pairs_) else "（不同的可能是图集还原或打包差异，可留意）"))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _compare(exp: dict, got: dict | None, cid: str) -> str | None:
    """比较一个组合：一致返回 None，不一致返回给人看的差异说明。"""
    if got is None:
        return f"缺少  {cid}（这次没渲染出来）"
    if got["recipe"] != exp["recipe"]:
        return (f"配方不同 {cid}\n      旧层序 {exp['keys']}\n      新层序 {got['keys']}"
                f"\n      旧 flat {exp['flat']}  新 flat {got['flat']}")
    for mode in ("canvas", "bbox"):
        if got["frames"][mode] != exp["frames"][mode]:
            bad = [i for i, (x, y) in enumerate(zip(exp["frames"][mode], got["frames"][mode])) if x != y]
            return (f"画面不同 {cid} [{mode}] 帧数 {len(exp['frames'][mode])}→"
                    f"{len(got['frames'][mode])}，不同的帧：{bad[:8]}")
    return None


def do_check(app, out_path: Path) -> dict:
    """按标准答案重渲染并逐项比对。

    返回 {"code": 退出码, "same": 一致数, "changed": 资源已变数, "diffs": 不同数}
    退出码：0 = 没有「不同」（资源已变不算失败）；1 = 有「不同」；2 = 没法比（文件/目录不存在）
    """
    if not out_path.exists():
        print(f"❌ 找不到标准答案 {out_path}，请先在改代码前跑 record")
        return {"code": 2, "same": 0, "changed": 0, "diffs": 0}
    golden = json.loads(out_path.read_text(encoding="utf-8"))
    roots = [Path(r) for r in golden["meta"]["roots"]]
    tmp = Path(tempfile.mkdtemp(prefix="m45_golden_"))
    try:
        got = collect(app, roots, tmp, 0, 0, golden)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if got is None:
        return {"code": 2, "same": 0, "changed": 0, "diffs": 0}
    all_cases, _targets, _env = got

    want = golden["cases"]
    files = golden["meta"].get("files")          # 旧格式（format 1）没有这一项
    cache: dict = {}
    same = 0
    changed: dict[str, dict] = {}                # 对象 → {"n": 几个组合, "eg": (例子路径, 原因)}
    diffs: list[str] = []
    for cid, exp in want.items():
        problem = _compare(exp, all_cases.get(cid), cid)
        if problem is None:
            same += 1
            continue
        if files is not None:
            bad = changed_inputs(exp, files, cache)
            if bad:                              # 当时用到的文件没了/变了 → 资源变化，不算失败
                tid = cid.rsplit("|", 2)[0]
                entry = changed.setdefault(tid, {"n": 0, "eg": bad[0]})
                entry["n"] += 1
                continue
        diffs.append(problem)                    # 文件都没变，画面却变了 → 代码造成的

    n_changed = sum(v["n"] for v in changed.values())
    print(f"\n比对 {len(want)} 个组合：{same} 个一致，{n_changed} 个资源已变（不算失败），{len(diffs)} 个不同")
    for tid, v in changed.items():
        path, why = v["eg"]
        print(f"  ⚠ 资源已变 {tid}：{v['n']} 个组合（例：{path} {why}）")
    for d in diffs[:30]:
        print("  ✗", d)
    if files is None:
        print("  ℹ 这份标准答案是旧格式（录制时没记资源指纹），资源变化也会被算成「不同」；"
              "重新 record 后就能区分")
    for path, st in golden["meta"].get("env", {}).items():
        if file_stamp(path) != st:
            print(f"  ℹ {Path(path).name} 在录制后被改过（匹配表/模板变化也可能让画面变，判定时没有排除，请留意）")
    extra = [c for c in all_cases if c not in want]
    if extra:
        print(f"  （另有 {len(extra)} 个组合是新出现的，不计入比对）")

    result = {"same": same, "changed": n_changed, "diffs": len(diffs)}
    if diffs:
        return {"code": 1, **result}
    if n_changed:
        print(f"✅ 代码没有改变画面；有 {n_changed} 个组合的资源在录制后变了，资源稳定后建议重新 record")
    else:
        print("✅ 全部一致：重构没有改变任何画面")
    return {"code": 0, **result}


def selftest(app) -> int:
    """自检：验证本脚本的三种判定都对。不碰真实资源、不碰正式标准答案。

    做法：在临时目录生成一份小资源（和 fixture_outfit 内容一样），录一份临时标准答案，然后
      1. 什么都不改 → 应全部一致
      2. 模拟「代码改坏了」（每帧左上角改一个像素）→ 应判定为「不同」（失败）
      3. 模拟「美术删了一帧」→ 用到这一帧的组合应判定为「资源已变」（不算失败）
    """
    global render                                # 第 2 步要临时换掉渲染函数
    tmp = Path(tempfile.mkdtemp(prefix="m45_golden_selftest_"))
    res = tmp / "res"                            # 放在临时目录里：判断变化用修改时间（同真实资源）
    out = tmp / "golden.json"
    ok = True

    def expect(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + msg)
        ok = ok and cond

    try:
        build_fixture(res)
        print("== 自检 0：录制临时标准答案")
        expect(do_record(app, [res], out, per_cat=2, max_combos=10) == 0, "录制成功")

        print("\n== 自检 1：什么都不改 → 应全部一致")
        r = do_check(app, out)
        expect(r["code"] == 0 and r["changed"] == 0 and r["diffs"] == 0 and r["same"] > 0,
               f"一致 {r['same']}、资源已变 {r['changed']}、不同 {r['diffs']}")

        print("\n== 自检 2：模拟代码改坏（每帧左上角改一个像素）→ 应判定为「不同」")
        real = render

        def broken(*a, **k):
            frames = []
            for img, label in real(*a, **k):
                img = img.copy()
                img.putpixel((0, 0), (255, 0, 255, 255))
                frames.append((img, label))
            return frames
        render = broken
        try:
            r = do_check(app, out)
        finally:
            render = real
        expect(r["code"] == 1 and r["diffs"] > 0 and r["changed"] == 0,
               f"退出码 {r['code']}、不同 {r['diffs']}、资源已变 {r['changed']}（资源没动，不该算资源已变）")

        print("\n== 自检 3：模拟美术删掉一帧（头发 E/idle 第 1 帧）→ 应判定为「资源已变」，不算失败")
        victim = sorted((res / "501521005_女侠客_部件" / "501511005_hair" / "E" / "idle").glob("*.png"))[0]
        victim.unlink()
        r = do_check(app, out)
        expect(r["code"] == 0 and r["changed"] > 0 and r["diffs"] == 0,
               f"退出码 {r['code']}、资源已变 {r['changed']}、不同 {r['diffs']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n✅ 自检通过" if ok else "\n❌ 自检失败")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="M45 逐帧画面标准答案：record / check / selftest")
    ap.add_argument("mode", choices=["record", "check", "selftest"])
    ap.add_argument("--root", action="append", help="资源目录（可写多次）")
    ap.add_argument("--per-cat", type=int, default=2)
    ap.add_argument("--max-combos", type=int, default=10)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-fixture", action="store_true",
                    help="record 时不加自动生成的套装目录（默认会加）")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    if args.mode == "selftest":
        return selftest(app)
    out_path = Path(args.out)
    if args.mode == "check":
        return do_check(app, out_path)["code"]
    roots = [Path(r) for r in (args.root or [DEFAULT_ROOT])]
    if not args.no_fixture:
        roots.append(FIXTURE_DIR)
    return do_record(app, roots, out_path, args.per_cat, args.max_combos)


if __name__ == "__main__":
    sys.exit(main())
