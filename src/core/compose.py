"""叠层配方（M45 阶段 A-2）：回答「这个角色、这个方向×动作，要叠哪些层」。

这是什么
--------
输入：选中的资源（一个部件 PartData，或一个组 IdGroup）+ 方向 + 动作 + 穿搭选择
输出：LayerStack —— 从下到上每一层用哪些帧文件、哪些是特效层、特效偏移多少

拿到 LayerStack 后交给 core/render.py 的 SequenceRender 渲染，就得到一串合成好的画面。
预览、导出 GIF、命令行批量导出都走这两步，保证「看到什么就导出什么」。

从哪来的
--------
从 app.py 的 MainWindow 里原样搬过来（_layers_for_current、_display_parts、
穿戴时装/头发/武器/翅膀/特效那一组方法），**判断逻辑和顺序一行没改**，只是：
- 以前直接读界面状态（self.matrix.current()、self._group、self._dressed_xxx、QSettings），
  现在全部改成参数传进来：方向/动作直接传，穿搭打包成 LookConfig，全局资料打包成 ComposeContext
- 本文件不依赖 Qt，可以脱离界面单独使用
- MainWindow 里同名的方法还在，只是变成「收集界面状态 → 调这里」的一两行（测试不用改）

改动前后画面是否完全一致，用 scripts/golden_frames.py check 验证（逐像素比对）。

用法示例
--------
    ctx = ComposeContext(groups=result.groups, tpl=tpl, namemap=namemap,
                         fx_by_key={p.name: p for p in result.fx_library},
                         wing_by_key={p.name: p for p in result.wing_library})
    look = LookConfig(dressed_look={"weapon": "501031001"})   # 给这个角色穿上 501031001 武器
    stack = build_stack(group, "SE", "attack", look, ctx)
    job = SequenceRender(stack.layers, flat_mask=stack.flat_mask,
                         fx_offsets=stack.fx_offsets, canvas_align=True)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .template import extract_weapon_tag

if TYPE_CHECKING:            # 只给编辑器/类型检查用，运行时不导入，避免循环引用
    from .namemap import NameMap
    from .scanner import IdGroup, PartData
    from .template import Template


# 跨 ID 穿戴的三个槽位（M38）：时装 / 头发 / 武器
LOOK_SLOTS = ("body", "hair", "weapon")


# =====================================================================
# 输入输出的数据结构
# =====================================================================
@dataclass
class ComposeContext:
    """全局资料：加载一个资源目录后就确定了，和「当前选中谁」无关。"""
    groups: list = field(default_factory=list)          # 扫描出的全部组（形象组合 M37、穿戴库 M38 要在里面找）
    tpl: "Template | None" = None                       # 规则模板（layer_order 层序、look_types 类型词）
    namemap: "NameMap | None" = None                    # ID-中文名匹配表（形象配对、武器标签推断要用中文名）
    fx_by_key: dict = field(default_factory=dict)       # 全局特效库 {资源名: PartData}（M26）
    wing_by_key: dict = field(default_factory=dict)     # 全局翅膀库 {资源名: PartData}（M28）


@dataclass
class LookConfig:
    """一个角色的「穿搭快照」。以前这些值散在 MainWindow 的属性和 QSettings 里。

    dressed_look：时装/头发/武器槽位的「记住的选择」{槽位: 组 key}
        - 没有这个槽位的键  → 用户没选过 → 武器槽位走自动推断（M42，按名字里的「剑」「斧」）
        - 值为 ""           → 用户明确选了「无」（脱下），不再自动推断
        - 值为某个组 key    → 穿这个；如果这个组在当前目录里已经不存在，回退自动推断
    wing / fx：穿戴的翅膀 / 特效资源名，"" = 不穿
    hidden_parts：显示层里被关掉的部件 key（和 toggle_key() 的结果对应）
    fx_offset_of：查某个特效的手动微调偏移的函数，(资源名) → (dx, dy)
        用函数而不是字典：偏移存在 QSettings 里，用到哪个才读哪个，和原来的行为一致
    """
    dressed_look: dict = field(default_factory=dict)
    wing: str = ""
    fx: str = ""
    hidden_parts: set = field(default_factory=set)
    fx_offset_of: Callable[[str], tuple] = lambda _key: (0, 0)


@dataclass
class LayerStack:
    """一个方向×动作的叠层配方（字段和 SequenceRender / DecodeWorker 的参数一一对应）。"""
    layers: list = field(default_factory=list)          # 每层的帧文件列表，下标 0 = 最底层
    flat_mask: list = field(default_factory=list)       # True = 特效层（按层自身范围居中）
    fx_offsets: dict = field(default_factory=dict)      # {层下标: (dx, dy)}，只有特效层有
    part_keys: list = field(default_factory=list)       # 每层的名字，和 layers 严格同序
    fx_layer_indices: set = field(default_factory=set)  # 可以 Ctrl+方向键微调的层（不含穿戴翅膀，M28）

    def as_tuple(self) -> tuple:
        """旧接口的返回格式 (layers, flat_mask, fx_offsets, part_keys)。"""
        return self.layers, self.flat_mask, self.fx_offsets, self.part_keys


# =====================================================================
# 组里有哪些部件
# =====================================================================
def render_parts(grp: "IdGroup") -> list:
    """M36：组视图参与叠层/渲染/统计的部件。

    worldboss 是独立变体（叠到本体上没有意义）→ 常规排除；
    组内只有 worldboss（该 ID 无主体）时退化为全部渲染，否则组视图无内容。
    """
    core = [p for p in grp.parts if p.part != "worldboss"]
    return core or list(grp.parts)


def look_split(grp: "IdGroup", ctx: ComposeContext) -> tuple | None:
    """M37：组的「形象前缀 + 类型词」（匹配表中文名去尾部类型词）；无命中返回 None。

    例：「天命·男〔剑〕时装」→ ("天命·男〔剑〕", "时装")
    """
    if ctx.namemap is None or ctx.tpl is None or not ctx.tpl.look_types:
        return None
    if grp.is_flat or grp.is_variant or not grp.parts:
        return None
    first = grp.parts[0]
    cn = ctx.namemap.lookup(first.name, grp.res_id)
    if not cn:
        return None
    return ctx.tpl.look_split(cn)


def display_parts(grp: "IdGroup", ctx: ComposeContext) -> list:
    """M37：组视图实际显示的部件 = 本组 + 同「形象」的其他 ID 组部件。

    形象配对：匹配表中文名去掉尾部类型词（模板 look_types）后前缀相同的
    其他组（如 天命·男〔剑〕时装/发/武器）→ 跨 ID 组合出整装预览；
    按 layer_order 排序（影子最底 → body → hair → weapon）。
    特效/世界BOSS变体/套装组不参与；无匹配表或前缀无命中时退化为本组部件。
    """
    core = render_parts(grp)
    split = look_split(grp, ctx)
    if split is None:
        return core
    look_key, _word = split
    out = list(core)
    for g in ctx.groups:
        if g is grp or g.is_outfit or g.is_variant or g.is_flat:
            continue
        s = look_split(g, ctx)
        if s is not None and s[0] == look_key:
            out.extend(render_parts(g))
    if len(out) == len(core):
        return core
    if ctx.tpl is not None:
        out.sort(key=lambda p: ctx.tpl.layer_rank(p.part))
    return out


def toggle_key(p: "PartData", parts: list) -> str:
    """显隐开关的 key：部件名；重名时追加 `·ID`（如两个 shadow → shadow·501031001）。

    M37：判重范围 = 组合后的显示部件列表（跨 ID 形象组合会把多个组的
    shadow/body 等重名部件放进同一面板）。
    """
    base = p.part or p.name
    same = [q for q in parts if (q.part or q.name) == base]
    return base if len(same) == 1 else f"{base}·{p.res_id}"


def is_modular_look(display: list) -> bool:
    """M44.2：显示的部件是不是「模块化角色」（拆成身体/头发/武器部件的主角类资源）。

    显示部件里只要有一个部位是 body / hair / weapon 就算。
    - 主角时装组、头发组、武器组、套装组 → True
    - 纯 ID 整包资源（怪物/NPC/伙伴，part=None）、翅膀、坐骑、特效、世界BOSS变体 → False
    只有 True 时才提供「穿戴时装/头发/武器」，也只有 True 时才把记忆的穿戴叠上去。
    """
    return any(p.part in LOOK_SLOTS for p in display)


# =====================================================================
# 跨 ID 穿戴：时装 / 头发 / 武器（M38、M42）
# =====================================================================
def group_by_key(key: str, ctx: ComposeContext):
    """按组 key 找组；key 为空或找不到返回 None。"""
    if not key:
        return None
    return next((g for g in ctx.groups if g.key == key), None)


def look_group_label(grp: "IdGroup", ctx: ComposeContext) -> str:
    """可穿戴形象组的下拉显示名：匹配表中文名优先，无则组名/ID。"""
    cn = None
    if ctx.namemap is not None and grp.parts:
        cn = ctx.namemap.lookup(grp.parts[0].name, grp.res_id)
    return cn or grp.display_name or grp.res_id


def look_library(slot: str, ctx: ComposeContext) -> list:
    """M38：可穿戴的形象组列表 [(组key, 显示名)]——拥有该部位的常规组。"""
    out: list = []
    for g in ctx.groups:
        if g.is_outfit or g.is_variant or g.is_flat:
            continue
        if not any(p.part == slot for p in g.parts):
            continue
        out.append((g.key, look_group_label(g, ctx)))
    return out


def auto_weapon_key(grp: "IdGroup", ctx: ComposeContext) -> str:
    """M42：按角色名中的武器标签（如「斧」「剑」）自动推断默认 0 阶武器组 key。

    仅作用于带模块化身体/头发部件（body/hair）的常规组，避免纯 ID 整包资源
    （怪物/NPC/伙伴）、独立翅膀、坐骑等误叠加主角武器。
    """
    if not ctx.groups or ctx.namemap is None:
        return ""
    if grp.is_outfit or grp.is_variant or grp.is_flat:
        return ""
    if any(p.part == "weapon" for p in grp.parts):
        return ""
    if not any(p.part in ("body", "hair") for p in grp.parts):
        return ""
    tag = extract_weapon_tag(look_group_label(grp, ctx))
    if not tag:
        return ""
    for w_key, w_label in look_library("weapon", ctx):
        if extract_weapon_tag(w_label) == tag:
            return w_key
    return ""


def resolve_dressed(slot: str, grp: "IdGroup", look: LookConfig, ctx: ComposeContext) -> str:
    """M38/M42：这个组在某个槽位实际生效的穿戴组 key（显式记忆优先，其次自动推断）。"""
    if slot in look.dressed_look:
        val = look.dressed_look[slot]
        # 显式脱下("") 或所选组在当前目录有效时严格遵循记忆；
        # 若记住的旧武器 key 在当前目录已失效，则回退默认推断（不写回配置）
        if val == "" or group_by_key(val, ctx) is not None:
            return val
    if slot == "weapon":
        return auto_weapon_key(grp, ctx)
    return ""


def dressed_look_parts(grp: "IdGroup", display: list, look: LookConfig, ctx: ComposeContext) -> list:
    """M38：组穿戴的时装/头发/武器组的全部渲染部件（叠层与空帧抽样共用）。

    显示部件（含形象组合）已有该部位 → 跳过该槽位，不重复叠加。
    """
    if grp is None:
        return []
    # M44.2：非模块化角色（如怪物）不叠任何穿戴——下拉已隐藏，用户无法脱下，
    # 万一 QSettings 里留着旧的穿戴记忆，也不能再把它叠上去
    if not is_modular_look(display):
        return []
    have = {p.part for p in display}
    out: list = []
    for slot in LOOK_SLOTS:
        if slot in have:
            continue
        g = group_by_key(resolve_dressed(slot, grp, look, ctx), ctx)
        if g is None:
            continue
        out.extend(render_parts(g))
    if out and ctx.tpl is not None:
        out.sort(key=lambda p: ctx.tpl.layer_rank(p.part))
    return out


def _dressed_look_entries(grp, display, direction, action, look, ctx) -> list:
    """M38：穿戴形象组部件的排序条目 `(排序键, 帧列表, 是否特效层, 层名)`。

    ⚠ 是否特效层必须为 False（普通层语义）：这些部件与主体出自**同一张渲染
    画布**（如 875×875），按原始画布坐标叠加才与手上位置对齐。若走特效层
    语义（flat=True，按层自身并集框居中），武器会被强制挪到画布中心而脱手
    ——实测 2026-09-29（昆仑剑侠武器穿到天命时装，画布对齐开关均无法补救）。
    """
    out: list = []
    for p in dressed_look_parts(grp, display, look, ctx):
        ad = p.action_data(direction, action)
        if ad is None:
            ad = next((a for col in p.matrix.values() for a in col.values()), None)
        if ad is None or not ad.frames:
            continue
        rank = (0, ctx.tpl.layer_rank(p.part)) if ctx.tpl is not None else (0, 0)
        out.append((rank, ad.frames, False, p.name))
    return out


# =====================================================================
# 全局穿戴：翅膀 / 特效（M26、M28）
# =====================================================================
def _append_dressed_part(stack: LayerStack, grp, dress_key: str, by_key: dict,
                         direction: str, action: str, look: LookConfig) -> int | None:
    """M26/M28：把「穿戴」的全局资源追加为顶层（原地修改 stack）。

    - dress_key 为空或找不到 → 不追加
    - 该资源若已在本组内（旧目录结构自动并入）→ 跳过，避免重复叠加
    - **取帧方式**：flat 资源（特效）取自身序列；常规资源（翅膀）按当前
      (方向,动作) 取帧，缺失时退回该资源的第一个可用组合
    - 仅 flat 资源（如特效）设置 flat_mask=True 并应用 fx_offsets 居中微调；
      常规穿戴资源（如翅膀）保持 flat_mask=False 按原始画布对齐
    - 偏移按**资源名**存取 → 同一资源调一次偏移，所有套装复用
    - 返回追加到的层下标（未追加返回 None），供调用方标记可微调的特效层
    """
    if not dress_key:
        return None
    p = by_key.get(dress_key)
    if p is None:
        return None
    if any(q.name == p.name for q in grp.parts):
        return None                      # 已在组内，不重复叠加
    if p.is_flat:
        ad = next((a for col in p.matrix.values() for a in col.values()), None)
    else:
        ad = p.action_data(direction, action)
        if ad is None:
            ad = next((a for col in p.matrix.values() for a in col.values()), None)
    if ad is None or not ad.frames:
        return None
    idx = len(stack.layers)
    stack.layers.append(ad.frames)
    stack.flat_mask.append(p.is_flat)
    stack.part_keys.append(p.name)
    if p.is_flat:
        stack.fx_offsets[idx] = look.fx_offset_of(p.name)
    return idx


# =====================================================================
# 总入口
# =====================================================================
def build_stack(target, direction: str, action: str,
                look: LookConfig, ctx: ComposeContext) -> LayerStack:
    """target 在 direction×action 下要叠哪些层。

    target：一个部件（PartData，左栏选中组里的某一项）或一个组（IdGroup）。
    调用方负责保证 direction、action 都不为空（原来的 _layers_for_current 也是先判断再进来）。
    """
    stack = LayerStack()

    # ---------------- 单部件视图 ----------------
    # 判断依据：组有 parts 属性，部件没有
    if not hasattr(target, "parts"):
        part = target
        ad = part.action_data(direction, action)
        if ad:
            stack.layers = [ad.frames]
            stack.flat_mask = [part.is_flat]
            stack.part_keys = [part.name]
            if part.is_flat:
                stack.fx_offsets = {0: look.fx_offset_of(part.name)}
        # M28：单部件视图下，特效自身就是可微调层
        stack.fx_layer_indices = {0} if part.is_flat else set()
        return stack

    # ---------------- 组视图 ----------------
    grp = target
    # 显示部件（M37：含同形象跨 ID 组合）与穿戴形象组（M38/M42）
    # 统一按模板 layer_order 排序（全部影子最底 → body → hair → weapon → 组内特效），
    # 避免穿戴武器的影子盖在人物身体之上
    display = display_parts(grp, ctx)
    tpl = ctx.tpl
    entries: list = []
    for p in display:
        key = toggle_key(p, display)
        if key in look.hidden_parts:          # 显示层里被关掉的部件不叠
            continue
        ad = p.action_data(direction, action)
        if ad is None and p.is_flat:          # 特效没有方向/动作：任何组合都叠它自己的序列
            ad = next((a for col in p.matrix.values() for a in col.values()), None)
        if tpl is None:
            rank = (1 if p.is_flat else 0, 0)
        elif p.is_flat:
            rank = (1, len(tpl.layer_order))  # 组内特效排在所有普通部件之上
        else:
            rank = (0, tpl.layer_rank(p.part))
        entries.append((rank, ad.frames if ad else [], p.is_flat, key))
    entries.extend(_dressed_look_entries(grp, display, direction, action, look, ctx))
    entries.sort(key=lambda e: e[0])          # 稳定排序：同一排序键保持原先后顺序

    for idx, (_rank, frames, is_flat, key) in enumerate(entries):
        stack.layers.append(frames)
        stack.flat_mask.append(is_flat)
        stack.part_keys.append(key)
        if is_flat:
            stack.fx_offsets[idx] = look.fx_offset_of(key)
    # M28：记录「真正的特效层」下标（组内 flat 部件）——穿戴的翅膀虽也是
    # flat 层，但按 (方向,动作) 取帧、对齐本就准确，不参与 Ctrl+方向键微调。
    stack.fx_layer_indices = {i for i, f in enumerate(stack.flat_mask) if f}
    # M28：先追加穿戴的翅膀（在角色之上）→ 再追加穿戴特效（置顶最上层）
    _append_dressed_part(stack, grp, look.wing, ctx.wing_by_key, direction, action, look)
    idx = _append_dressed_part(stack, grp, look.fx, ctx.fx_by_key, direction, action, look)
    if idx is not None:
        stack.fx_layer_indices.add(idx)       # 穿戴的特效可微调
    return stack
