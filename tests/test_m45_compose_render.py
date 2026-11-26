# -*- coding: utf-8 -*-
"""M45 阶段 A：core/compose.py + core/render.py 脱离界面独立使用。

为什么要这个测试
----------------
M45 的目标是「导出 GIF / 命令行批量导出」时不用打开界面也能拿到和预览一模一样的画面。
这要求「叠哪些层」（compose）和「怎么渲染」（render）都不依赖 Qt。
golden_frames.py 证明了搬代码后画面没变；这个测试证明它们真的能**单独**用。

验证：
1. 只导入 core 模块，不会把 PySide6 带进来
2. 套装组（M23 + M25 组内特效）：层序 = 影子 → 身体 → 头发 → 武器 → 特效；只有特效是特效层、可微调
3. 显示层关掉某个部件（hidden_parts）→ 这一层不叠
4. 跨 ID 穿戴武器（M38）：武器 + 武器影子叠上来，影子在最底，且是普通层（flat=False，不脱手）
5. 非模块化角色（M44.2）：纯 ID 怪物即使「记住」了穿戴武器也不叠
6. 单部件视图：只有 1 层
7. 渲染：帧数、画布尺寸（画布对齐 = 原始渲染画布）

运行：python tests/test_m45_compose_render.py
（不需要 QT_QPA_PLATFORM，本测试根本不启动界面）
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------- 1. 只导入 core，不应带入 Qt ----------------
from src.core import compose                         # noqa: E402
from src.core.render import SequenceRender           # noqa: E402
from src.core.scanner import scan_root               # noqa: E402
from src.core.template import load_templates         # noqa: E402

assert not any(m.startswith("PySide6") for m in sys.modules), \
    "core/compose、core/render、scanner、template 都不应导入 PySide6"
print("[1] OK 只导入 core 模块，没有带入 PySide6")

from PIL import Image, ImageDraw                     # noqa: E402

CANVAS = (120, 120)


def make_part(folder: Path, box, color, n: int = 8) -> None:
    """一个部件：E/idle 共 n 帧，整张 120×120 画布，box 位置画一个矩形，每帧右移 1px。"""
    d = folder / "E" / "idle"
    d.mkdir(parents=True)
    for i in range(n):
        img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        x0, y0, x1, y1 = box
        ImageDraw.Draw(img).rectangle([x0 + i, y0, x1 + i - 1, y1 - 1], fill=color)
        img.save(d / f"{i + 1:04d}.png")


def make_fx(folder: Path, n: int = 5) -> None:
    """扁平特效：直接放 前缀_序号.png（M21 结构），每帧大小不同。"""
    folder.mkdir(parents=True)
    for i in range(n):
        img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([30 - i * 3, 30 - i * 3, 60 + i * 3, 60 + i * 3],
                                    fill=(90, 140, 240, 160))
        img.save(folder / f"changrao_{i + 1}.png")


tpl = load_templates()[0]
tmp = Path(tempfile.mkdtemp(prefix="m45_compose_"))
try:
    # ---------------- 2/3. 套装组 + 组内特效 ----------------
    outfit = tmp / "501521005_女侠客_部件"
    make_part(outfit / "501031005_shadow", (20, 100, 50, 106), (0, 0, 0, 100))
    make_part(outfit / "501031005_weapon", (20, 60, 50, 70), (220, 200, 90, 255))
    make_part(outfit / "501511005_hair", (50, 20, 70, 32), (60, 40, 30, 255))
    make_part(outfit / "501521005_body", (45, 30, 75, 100), (200, 80, 60, 255))
    make_part(outfit / "501521005_shadow", (40, 100, 80, 108), (0, 0, 0, 110))
    make_fx(outfit / "50105101")

    r = scan_root(outfit, tpl)
    assert len(r.groups) == 1 and r.groups[0].is_outfit, "应合并为 1 个套装组"
    grp = r.groups[0]
    ctx = compose.ComposeContext(groups=r.groups, tpl=tpl,
                                 fx_by_key={p.name: p for p in r.fx_library},
                                 wing_by_key={p.name: p for p in r.wing_library})

    stack = compose.build_stack(grp, "E", "idle", compose.LookConfig(), ctx)
    parts = [k.split("·")[0] for k in stack.part_keys]
    assert parts == ["shadow", "shadow", "body", "hair", "weapon", "50105101"], \
        f"层序应为 影子×2 → 身体 → 头发 → 武器 → 特效: {stack.part_keys}"
    assert stack.flat_mask == [False] * 5 + [True], f"只有最后一层是特效层: {stack.flat_mask}"
    assert stack.fx_layer_indices == {5}, f"只有特效层可微调: {stack.fx_layer_indices}"
    assert all(len(layer) == 8 for layer in stack.layers[:5]) and len(stack.layers[5]) == 5
    print(f"[2] OK 套装层序 {stack.part_keys}")

    # 特效偏移：fx_offset_of 返回什么，配方里就是什么
    look = compose.LookConfig(fx_offset_of=lambda key: (7, -3) if key == "50105101" else (0, 0))
    assert compose.build_stack(grp, "E", "idle", look, ctx).fx_offsets == {5: (7, -3)}

    look = compose.LookConfig(hidden_parts={"hair"})
    hidden = compose.build_stack(grp, "E", "idle", look, ctx)
    assert "hair" not in hidden.part_keys and len(hidden.layers) == 5, \
        f"关掉头发后应剩 5 层: {hidden.part_keys}"
    print("[3] OK 特效偏移传递、关掉头发后不叠")

    # ---------------- 4/5. 跨 ID 穿戴武器、非模块化角色 ----------------
    loose = tmp / "loose"
    make_part(loose / "50122101_body", (45, 30, 75, 100), (200, 80, 60, 255))
    make_part(loose / "50122101_shadow", (40, 100, 80, 108), (0, 0, 0, 110))
    make_part(loose / "501031001_weapon", (20, 60, 50, 70), (220, 200, 90, 255))
    make_part(loose / "501031001_shadow", (20, 100, 50, 106), (0, 0, 0, 100))
    make_part(loose / "503099", (30, 30, 90, 100), (90, 160, 60, 255))     # 纯 ID 怪物
    saved = tpl.outfit_merge_max
    tpl.outfit_merge_max = 2            # 同一文件夹多个 ID：不合并成套装，按 ID 分组（同 test_m37）
    try:
        r2 = scan_root(loose, tpl)
    finally:
        tpl.outfit_merge_max = saved
    ctx2 = compose.ComposeContext(groups=r2.groups, tpl=tpl)
    body_grp = next(g for g in r2.groups if g.key == "50122101")
    monster = next(g for g in r2.groups if g.key == "503099")

    look = compose.LookConfig(dressed_look={"weapon": "501031001"})
    worn = compose.build_stack(body_grp, "E", "idle", look, ctx2)
    keys = worn.part_keys
    assert len(keys) == 4 and "501031001_weapon" in keys and "501031001_shadow" in keys, keys
    assert keys.index("501031001_shadow") < keys.index("body") < keys.index("501031001_weapon"), \
        f"穿戴武器：影子最底、武器在身体之上: {keys}"
    assert worn.flat_mask == [False] * 4, "穿戴层必须是普通层（flat=False），否则武器会脱手"
    # 明确脱下（""）→ 不叠
    off = compose.build_stack(body_grp, "E", "idle",
                              compose.LookConfig(dressed_look={"weapon": ""}), ctx2)
    assert len(off.layers) == 2, f"脱下武器后应剩 身体+影子 2 层: {off.part_keys}"
    print(f"[4] OK 穿戴武器 {keys}；脱下后 2 层")

    m = compose.build_stack(monster, "E", "idle", look, ctx2)
    assert len(m.layers) == 1, f"纯 ID 怪物不应叠穿戴: {m.part_keys}"
    print("[5] OK 纯 ID 怪物不叠记住的穿戴")

    # ---------------- 6. 单部件视图 ----------------
    weapon_part = next(p for p in r2.parts if p.name == "501031001_weapon")
    single = compose.build_stack(weapon_part, "E", "idle", compose.LookConfig(), ctx2)
    assert single.part_keys == ["501031001_weapon"] and single.fx_layer_indices == set()
    assert compose.build_stack(weapon_part, "SE", "idle", compose.LookConfig(), ctx2).layers == [], \
        "没有的方向 → 空配方"
    print("[6] OK 单部件视图 1 层；没有的方向返回空")

    # ---------------- 7. 渲染 ----------------
    job = SequenceRender(stack.layers, flat_mask=stack.flat_mask,
                         fx_offsets=stack.fx_offsets, canvas_align=True)
    frames = list(job.frames())
    assert job.total == 8 and len(frames) == 8, f"应渲染 8 帧（最长层），实际 {job.total}"
    assert frames[0][1].size == CANVAS, f"画布对齐：输出 = 原始画布 {CANVAS}，实际 {frames[0][1].size}"
    assert not job.all_blank
    assert [label for _i, _img, label in frames][:2] == ["0001", "0002"], "帧号标签取文件名"
    # 中途停止：should_stop 返回 True 后不再产出
    count = {"n": 0}

    def stop_after_3() -> bool:
        count["n"] += 1
        return count["n"] > 3
    assert len(list(job.frames(should_stop=stop_after_3))) == 3, "should_stop 应能中途停止"
    print(f"[7] OK 渲染 8 帧 {frames[0][1].size}；should_stop 可中途停止")

    assert not any(mod.startswith("PySide6") for mod in sys.modules), "全程不应导入 PySide6"
    print("\nALL M45 COMPOSE/RENDER PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
