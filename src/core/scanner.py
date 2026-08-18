"""文件夹扫描 + 模板匹配 + 缺漏检测 + 同ID分组。

设计要点（v0.3 定稿）：
- 帧号跨动作连续编号（idle 0001-0008, attack 0017-0024），
  断档检测只要求「动作内 min→max 区间连续」，不要求从 0001 开始。
- 文件夹命名兼容纯 ID（505026）与 {id}_{part}（50104101_shadow）。
- 扫描阶段只读文件名，不解码图像，保证毫秒级响应。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .template import Template


@dataclass
class ActionData:
    """单个 方向×动作 的帧数据。"""
    frames: list[Path] = field(default_factory=list)   # 按帧号排序
    numbers: list[int] = field(default_factory=list)
    gaps: list[int] = field(default_factory=list)      # min→max 内缺失的帧号

    @property
    def count(self) -> int:
        return len(self.frames)

    @property
    def continuous(self) -> bool:
        return not self.gaps


@dataclass
class PartData:
    """一个部件文件夹的完整扫描结果。"""
    folder: Path
    res_id: str
    part: str | None                       # None = 纯ID整体资源
    matrix: dict[str, dict[str, ActionData]] = field(default_factory=dict)
    missing_directions: list[str] = field(default_factory=list)
    # 方向存在但动作缺失: {direction: [action, ...]}
    missing_actions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def has_issues(self) -> bool:
        if self.missing_directions or self.missing_actions:
            return True
        return any(not ad.continuous for d in self.matrix.values() for ad in d.values())

    def action_data(self, direction: str, action: str) -> ActionData | None:
        return self.matrix.get(direction, {}).get(action)

    def available_directions(self) -> list[str]:
        return list(self.matrix.keys())

    def available_actions(self, direction: str) -> list[str]:
        return list(self.matrix.get(direction, {}).keys())


@dataclass
class IdGroup:
    """同 ID 的分组（叠层配套校验的基本单位）。"""
    res_id: str
    parts: list[PartData] = field(default_factory=list)
    # 配套异常：某部位缺失而组内其他部件拥有的 (direction, action)
    pairing_issues: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.pairing_issues) or any(p.has_issues for p in self.parts)


@dataclass
class ScanResult:
    root: Path
    parts: list[PartData] = field(default_factory=list)
    groups: list[IdGroup] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)   # 不匹配模板的子文件夹名


def _scan_action_folder(action_dir: Path, tpl: Template) -> ActionData:
    """读取动作文件夹内的帧文件，提取帧号并检测断档。"""
    rx = tpl.frame_regex()
    exts = tpl.ext_set()
    pairs: list[tuple[int, Path]] = []
    for fp in action_dir.iterdir():
        if not fp.is_file() or fp.suffix.lower() not in exts:
            continue
        m = rx.match(fp.name)
        if m:
            pairs.append((int(m.group(1)), fp))
    pairs.sort(key=lambda x: x[0])
    numbers = [n for n, _ in pairs]
    gaps: list[int] = []
    if numbers:
        present = set(numbers)
        gaps = [n for n in range(numbers[0], numbers[-1] + 1) if n not in present]
    return ActionData(frames=[p for _, p in pairs], numbers=numbers, gaps=gaps)


def scan_part(folder: Path, tpl: Template) -> PartData | None:
    """扫描单个部件文件夹。folder_pattern 不匹配或内部无有效结构时返回 None。"""
    parsed = tpl.parse_folder_name(folder.name)
    if parsed is None:
        return None
    res_id, part = parsed

    matrix: dict[str, dict[str, ActionData]] = {}
    # hierarchy: ["direction","action"]（默认）或 ["action","direction"]
    first, second = tpl.hierarchy[0], tpl.hierarchy[1]
    first_list = tpl.directions if first == "direction" else tpl.actions
    second_list = tpl.actions if second == "action" else tpl.directions

    for first_name in first_list:
        first_dir = folder / first_name
        if not first_dir.is_dir():
            continue
        col: dict[str, ActionData] = {}
        for second_name in second_list:
            second_dir = first_dir / second_name
            if not second_dir.is_dir():
                continue
            ad = _scan_action_folder(second_dir, tpl)
            if ad.count > 0:
                col[second_name] = ad
        if col:
            matrix[first_name] = col

    if not matrix:
        return None

    # matrix 的 key 统一为 (direction, action) 语义
    if first != "direction":
        matrix = _transpose(matrix)

    missing_directions = [d for d in tpl.directions if d not in matrix]
    missing_actions: dict[str, list[str]] = {}
    for d in matrix:
        miss = [a for a in tpl.actions if a not in matrix[d]]
        if miss:
            missing_actions[d] = miss

    return PartData(
        folder=folder, res_id=res_id, part=part, matrix=matrix,
        missing_directions=missing_directions, missing_actions=missing_actions,
    )


def _transpose(matrix: dict[str, dict[str, ActionData]]) -> dict[str, dict[str, ActionData]]:
    """action→direction 层级时，转置为 direction→action。"""
    out: dict[str, dict[str, ActionData]] = {}
    for action, col in matrix.items():
        for direction, ad in col.items():
            out.setdefault(direction, {})[action] = ad
    return out


def scan_root(root: Path, tpl: Template) -> ScanResult:
    """扫描入口：拖入部件文件夹 → 单部件；拖入父级目录 → 扫描一级子文件夹。"""
    root = Path(root)
    # 情况1：拖入的本身就是部件文件夹
    single = scan_part(root, tpl)
    if single is not None:
        result = ScanResult(root=root.parent, parts=[single])
        result.groups = _group_parts(result.parts, tpl)
        return result

    # 情况2：父级目录
    result = ScanResult(root=root)
    if not root.is_dir():
        return result
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        pd = scan_part(child, tpl)
        if pd is not None:
            result.parts.append(pd)
        else:
            result.ignored.append(child.name)
    result.groups = _group_parts(result.parts, tpl)
    return result


def _group_parts(parts: list[PartData], tpl: Template) -> list[IdGroup]:
    """按 res_id 分组，并做同 ID 配套校验（交集对比）。"""
    by_id: dict[str, list[PartData]] = {}
    for p in parts:
        by_id.setdefault(p.res_id, []).append(p)

    groups: list[IdGroup] = []
    for res_id, members in by_id.items():
        # 组内排序：layer_order 越靠前（越底层）排越前
        members.sort(key=lambda p: tpl.layer_rank(p.part))
        grp = IdGroup(res_id=res_id, parts=members)
        if len(members) > 1:
            grp.pairing_issues = _check_pairing(members)
        groups.append(grp)
    groups.sort(key=lambda g: g.res_id)
    return groups


def _check_pairing(members: list[PartData]) -> list[str]:
    """同 ID 配套校验：主体部件拥有的 (方向,动作)，shadow 等底层部件是否也具备。"""
    issues: list[str] = []
    # 以组内所有部件的并集为参照
    union: set[tuple[str, str]] = set()
    for p in members:
        for d, col in p.matrix.items():
            for a in col:
                union.add((d, a))
    for p in members:
        own = {(d, a) for d, col in p.matrix.items() for a in col}
        missing = union - own
        if missing:
            label = p.part or p.name
            sample = sorted(missing)[:3]
            issues.append(
                f"{label} 缺 {len(missing)} 组配套: " +
                ", ".join(f"{d}/{a}" for d, a in sample) +
                (" …" if len(missing) > 3 else "")
            )
    return issues
