"""HUD 信息统计聚合（M32）：从扫描结果计算帧数账目，零新增 IO。

口径（2026-09-10 确认）：
- 单组合「方向-动作」= 帧数 + 帧号范围 + 断档。
- 套装组：组合取组内非特效部件的**并集**；每行帧数以**主件**为准
  （body 优先，缺 body 取首个主体部件；主件没有该组合时取首个有它的
  部件并标注 source）。组内其他部件帧数不一致 → mismatch（配套异常可视化）。
- 特效（扁平结构）各自成行，帧号不补零；不参与 mismatch 比对。
- 总帧数：单部件 = 全部组合 count 和；套装 grand = 并集行 count 和（主件口径），
  各部件 ID 小计单独返回（HUD tooltip 用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .scanner import ActionData, IdGroup, PartData
from .template import Template


@dataclass
class ComboStat:
    """一行「方向-动作」账目。"""
    direction: str
    action: str
    count: int = 0
    first: int | None = None
    last: int | None = None
    gaps: list[int] = field(default_factory=list)
    is_flat: bool = False                  # 特效行（帧号显示不补零）
    source: str = ""                       # 帧数来源部件标签（主件缺失该组合时标注）
    mismatch: list[str] = field(default_factory=list)   # 帧数不一致的部件 ["weapon 7帧"]

    @property
    def has_issues(self) -> bool:
        return bool(self.gaps or self.mismatch)


@dataclass
class HudStats:
    """HUD 面板一帧完整账目（app 组装，anim_view 渲染）。"""
    title: str = ""
    subtitle: str = ""                     # 如「套装 · 6件」/「部件 body」
    rows: list[ComboStat] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)   # 各部件标签 → 总帧数
    grand: int = 0                         # 合计（主件口径）
    current: tuple[str | None, str | None] = (None, None)  # 当前选中 (方向,动作) 高亮


def part_label(p: PartData) -> str:
    """部件标签（HUD/角标显示用）：部位名优先，纯 ID 整体资源用文件夹名。"""
    return p.part or p.name


def part_total(p: PartData) -> int:
    """一个部件（=一个 ID 的资源）全部组合的总帧数。"""
    return sum(ad.count for col in p.matrix.values() for ad in col.values())


def _sort_key(tpl: Template | None):
    """按模板 directions/actions 顺序排序；模板外追加末尾（字母序）。"""
    dirs = list(tpl.directions) if tpl else []
    acts = list(tpl.actions) if tpl else []

    def key(row: ComboStat):
        di = dirs.index(row.direction) if row.direction in dirs else len(dirs)
        ai = acts.index(row.action) if row.action in acts else len(acts)
        return (di, ai, row.direction, row.action)
    return key


def _rows_from_ad(direction: str, action: str, ad: ActionData,
                  is_flat: bool = False) -> ComboStat:
    return ComboStat(
        direction=direction, action=action, count=ad.count,
        first=ad.numbers[0] if ad.numbers else None,
        last=ad.numbers[-1] if ad.numbers else None,
        gaps=list(ad.gaps), is_flat=is_flat,
    )


def part_combos(p: PartData, tpl: Template | None = None) -> list[ComboStat]:
    """单部件的全部 (方向,动作) 账目行。"""
    rows: list[ComboStat] = []
    for d, col in p.matrix.items():
        for a, ad in col.items():
            rows.append(_rows_from_ad(d, a, ad, is_flat=p.is_flat))
    rows.sort(key=_sort_key(tpl))
    return rows


def _pick_primary(parts: list[PartData]) -> PartData | None:
    """套装主件：body 优先 → 首个非 shadow/fills 主体 → 首个非特效 → 首个。"""
    if not parts:
        return None
    return (next((p for p in parts if p.part == "body"), None)
            or next((p for p in parts if p.part not in ("shadow", "fills", None)
                     and not p.is_flat), None)
            or next((p for p in parts if not p.is_flat), None)
            or parts[0])


def group_combos(grp: IdGroup, tpl: Template | None = None) -> HudStats:
    """套装组/ID 组的完整账目。

    返回 HudStats（rows=并集组合行；totals=各部件总帧数小计；grand=主件口径合计）。
    """
    parts = grp.parts
    totals = {part_label(p): part_total(p) for p in parts}
    stats = HudStats(totals=totals)
    if not parts:
        return stats

    primary = _pick_primary(parts)
    if primary is None:
        return stats

    # 组内全部是特效（扁平组）→ 每个部件直接列自己的行
    if all(p.is_flat for p in parts):
        rows: list[ComboStat] = []
        for p in parts:
            rows.extend(part_combos(p, tpl))
        rows.sort(key=_sort_key(tpl))
        stats.rows = rows
        stats.grand = sum(r.count for r in rows)
        return stats

    # 非特效部件：并集组合 + 主件口径
    normal = [p for p in parts if not p.is_flat]
    combos: set[tuple[str, str]] = set()
    for p in normal:
        for d, col in p.matrix.items():
            for a in col:
                combos.add((d, a))
    rows = []
    for d, a in combos:
        # 帧数来源：主件优先，否则首个拥有该组合的部件（parts 已按 layer_rank 排序）
        ad: ActionData | None = primary.action_data(d, a)
        source = ""
        if ad is None:
            for p in normal:
                ad = p.action_data(d, a)
                if ad is not None:
                    source = part_label(p)
                    break
        if ad is None:
            continue
        row = _rows_from_ad(d, a, ad)
        row.source = source
        base = row.count
        for p in normal:
            if p is primary:
                continue
            other = p.action_data(d, a)
            if other is not None and other.count != base:
                row.mismatch.append(f"{part_label(p)} {other.count}帧")
        rows.append(row)
    rows.sort(key=_sort_key(tpl))
    # M25：特效层各自成行（虚拟方向「特效」+ 序列前缀），追加在主体行之后
    for p in parts:
        if p.is_flat:
            rows.extend(part_combos(p, tpl))
    stats.rows = rows
    stats.grand = sum(r.count for r in rows)
    return stats


def build_hud_stats(target: PartData | IdGroup, tpl: Template | None = None) -> HudStats:
    """统一入口：单部件或组 → HudStats（title/subtitle 由调用方补齐）。"""
    if isinstance(target, IdGroup):
        return group_combos(target, tpl)
    stats = HudStats(totals={part_label(target): part_total(target)})
    stats.rows = part_combos(target, tpl)
    stats.grand = part_total(target)
    return stats
