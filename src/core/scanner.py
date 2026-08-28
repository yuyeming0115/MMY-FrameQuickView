"""文件夹扫描 + 模板匹配 + 缺漏检测 + 同ID分组。

设计要点（v0.3 定稿）：
- 帧号跨动作连续编号（idle 0001-0008, attack 0017-0024），
  断档检测只要求「动作内 min→max 区间连续」，不要求从 0001 开始。
- 文件夹命名兼容纯 ID（505026）与 {id}_{part}（50104101_shadow）。
- 扫描阶段只读文件名，不解码图像，保证毫秒级响应。
"""
from __future__ import annotations

import re
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
    character_type: str = ""               # protagonist / non_protagonist（归一化后）
    effective_type: str = ""               # 查漏实际使用的类型（覆盖 wings/mount/npc/空）
    missing_directions: list[str] = field(default_factory=list)
    # 方向存在但「约定动作」缺失: {direction: [action, ...]}（按角色类型+方向基准）
    missing_actions: dict[str, list[str]] = field(default_factory=list)
    # 模板外多余动作: {direction: [action, ...]}（如旧工程的 catch/sprint）
    extra_actions: dict[str, list[str]] = field(default_factory=dict)
    # 警告级缺漏（如 fills 缺失）：不进红色 missing，只在左栏/状态栏橙色提示
    warning_directions: list[str] = field(default_factory=list)
    warning_actions: dict[str, list[str]] = field(default_factory=dict)
    # 扁平结构资源（特效类）：无方向/动作层级，matrix key 为虚拟方向（如「特效」）
    is_flat: bool = False

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def has_issues(self) -> bool:
        if self.missing_directions or self.missing_actions or self.extra_actions:
            return True
        return any(not ad.continuous for d in self.matrix.values() for ad in d.values())

    @property
    def has_warnings(self) -> bool:
        return bool(self.warning_directions or self.warning_actions)

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
    # 按角色类型+方向基准的缺漏（组视图按钮矩阵/状态栏用）
    character_type: str = ""
    effective_type: str = ""               # 查漏实际使用的类型（覆盖 wings/mount/npc）
    missing_directions: list[str] = field(default_factory=list)
    missing_actions: dict[str, list[str]] = field(default_factory=dict)
    # 模板外多余动作（组内并集）: {direction: [action, ...]}
    extra_actions: dict[str, list[str]] = field(default_factory=dict)
    # 警告级缺漏（如 fills 缺失）：组内非 fills 部件存在、但整组没有 fills
    missing_fills: list[str] = field(default_factory=list)   # 缺的方向
    # 组内 fills 部件的警告级缺漏
    fills_warning_directions: list[str] = field(default_factory=list)
    fills_warning_actions: dict[str, list[str]] = field(default_factory=dict)
    # 资源分类（模板 categories 规则匹配：主角/伙伴/怪物/BOSS/NPC/坐骑/翅膀/特效）
    category: str = ""
    # 组内全部为扁平结构资源（特效类）→ 跳过方向/动作查漏
    is_flat: bool = False

    @property
    def has_issues(self) -> bool:
        if self.pairing_issues or self.missing_directions or self.missing_actions or self.extra_actions:
            return True
        return any(p.has_issues for p in self.parts)

    @property
    def has_warnings(self) -> bool:
        if self.missing_fills or self.fills_warning_directions or self.fills_warning_actions:
            return True
        return any(p.has_warnings for p in self.parts)


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


def _scan_flat_folder(folder: Path, tpl: Template, rule: dict) -> dict[str, dict[str, ActionData]]:
    """扁平结构扫描（特效类）：文件直接放在部件根目录，命名 `前缀_序号.ext`。

    按文件名前缀分组为多个帧序列，映射为 {虚拟方向: {前缀: ActionData}}，
    帧号断档检测与常规序列一致（min→max 区间连续，不要求从 1 开始）。
    frame_pattern 匹配去扩展名的 stem：group(1)=前缀（动作名），group(2)=帧号。
    """
    rx = re.compile(rule.get("frame_pattern", r"^(.+?)_(\d+)$"), re.IGNORECASE)
    exts = tpl.ext_set()
    virtual_dir = rule.get("virtual_direction", "特效")
    seqs: dict[str, list[tuple[int, Path]]] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return {}
    for fp in entries:
        if not fp.is_file() or fp.suffix.lower() not in exts:
            continue
        m = rx.match(fp.stem)
        if m:
            seqs.setdefault(m.group(1), []).append((int(m.group(2)), fp))
    matrix: dict[str, dict[str, ActionData]] = {}
    for prefix, pairs in seqs.items():
        pairs.sort(key=lambda x: x[0])
        numbers = [n for n, _ in pairs]
        gaps = [n for n in range(numbers[0], numbers[-1] + 1)
                if n not in set(numbers)] if numbers else []
        matrix.setdefault(virtual_dir, {})[prefix] = ActionData(
            frames=[p for _, p in pairs], numbers=numbers, gaps=gaps)
    return matrix


def _is_flat_folder(folder: Path, tpl: Template) -> bool:
    """判定 folder 是否为扁平结构资源：名字解析为纯 ID 且命中模板 flat 规则。"""
    parsed = tpl.parse_folder_name(folder.name)
    if parsed is None or parsed[1] is not None:
        return False
    return tpl.flat_rule(parsed[0]) is not None


def _looks_like_part_folder(folder: Path, tpl: Template) -> bool:
    """结构检测兜底：folder 子目录中有 ≥2 个是模板已知 direction/action。

    用于识别 parse_folder_name 无法命中的整体资源（如新工程的 503026_神龙，
    命名为 {id}_{中文名}，既非 {id}_{part} 也非纯 ID）。靠实际子目录结构
    判定，避免误把分类文件夹（如 504004_黑龙王，其下是 Render_Output）当资源。
    """
    try:
        children = [c.name for c in folder.iterdir() if c.is_dir()]
    except OSError:
        return False
    known = set(tpl.directions) | set(tpl.actions)
    hits = sum(1 for name in children if name in known)
    return hits >= 2


def _is_leaf_part_folder(folder: Path, tpl: Template) -> bool:
    """判定 folder 是否为「叶子部件文件夹」：直接子目录含 ≥1 个 direction。

    用于区分真部件文件夹（内层直接是 E/N/NW/S/SE 方向）与「ID 壳 / 分类文件夹」：
    如 怪物/503006/503006_body，503006 名字是纯 ID 但内层是 503006_body 等部件壳，
    不是部件文件夹，需继续下钻。而 503026_神龙（整体资源）内层直接是 direction，
    是叶子部件文件夹。名字解析交给 scan_part 内的 _parse_folder_or_struct。
    """
    try:
        children = [c.name for c in folder.iterdir() if c.is_dir()]
    except OSError:
        return False
    dirs = set(tpl.directions)
    return any(name in dirs for name in children)


def _parse_folder_or_struct(folder: Path, tpl: Template) -> tuple[str, str | None] | None:
    """先按名字解析，失败时用子目录结构兜底（整体资源）。

    - 名字命中 {id}_{part} 或纯 ID → 直接返回
    - 名字不命中但子目录结构像部件 → 视为整体资源，res_id 取文件夹名的数字前缀
      （如 503026_神龙 → res_id=503026）；无数字前缀则用整个文件夹名
    - 都不命中 → 返回 None（交给上层继续递归）
    """
    parsed = tpl.parse_folder_name(folder.name)
    if parsed is not None:
        return parsed
    if _looks_like_part_folder(folder, tpl):
        m = re.match(r"^(\d+)", folder.name)
        res_id = m.group(1) if m else folder.name
        return res_id, None
    return None



def scan_part(folder: Path, tpl: Template, char_type_of=None) -> PartData | None:
    """扫描单个部件文件夹。folder_pattern 不匹配或内部无有效结构时返回 None。

    char_type_of: 可选 Callable[res_id] -> str|None，返回匹配表中的原始类型字段；
                  内部的角色类型归一化交给 tpl.resolve_char_type。
    """
    parsed = _parse_folder_or_struct(folder, tpl)
    if parsed is None:
        return None
    res_id, part = parsed
    char_type = tpl.resolve_char_type(char_type_of(res_id) if char_type_of else None)

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
        # 常规方向/动作结构无有效内容 → 尝试扁平结构（特效类，仅纯 ID 整体资源）。
        # 带 _part 后缀的文件夹不做扁平兜底，防止把常规部件误判成特效。
        if part is not None:
            return None
        rule = tpl.flat_rule(res_id)
        if rule is None:
            return None
        matrix = _scan_flat_folder(folder, tpl, rule)
        if not matrix:
            return None
        # 扁平资源无方向/动作约定 → 不做查漏，仅帧号断档检测（ActionData.gaps）
        return PartData(
            folder=folder, res_id=res_id, part=None, matrix=matrix,
            character_type=char_type, effective_type="flat", is_flat=True,
        )

    # matrix 的 key 统一为 (direction, action) 语义
    if first != "direction":
        matrix = _transpose(matrix)

    # 检测模板外多余动作：遍历各方向实际子目录，不在 tpl.actions 但有有效帧的
    known_actions = set(tpl.actions)
    extra_actions: dict[str, list[str]] = {}
    for d in matrix:
        dir_path = folder / d
        if not dir_path.is_dir():
            continue
        try:
            children = [c for c in dir_path.iterdir() if c.is_dir()]
        except OSError:
            continue
        extras: list[str] = []
        for c in children:
            if c.name in known_actions:
                continue
            ad = _scan_action_folder(c, tpl)
            if ad.count > 0:
                extras.append(c.name)
        if extras:
            extra_actions[d] = extras

    missing_directions = [d for d in tpl.directions if d not in matrix]
    missing_actions: dict[str, list[str]] = {}
    # shadow 是配套部件（影子），动作跟随主体，不单独查漏。
    # fills 参与查漏，但作为「警告级」：缺失写入 warning_* 字段（左栏橙点/状态栏橙字），
    # 不进红色 missing（不打红按钮矩阵）。其漏检基准 = 本角色类型×方向约定动作。
    # 翅膀部件用 wings 规则；坐骑部件用 mount 规则；其他按角色类型。
    # NPC 兜底：匹配表未标类型（default non_protagonist）且实际无任何
    # attack/skill/hurt/block/dead/ride_* 动作 → 按 npc 规则（5方向仅 idle/run）。
    if part == "shadow":
        part_type = ""  # 空 → expected_actions 返回 [] → 不查漏
    elif part == "wings":
        part_type = "wings"
    elif part in ("ride_front", "ride_back"):
        part_type = "mount"
    else:
        part_type = char_type
        if char_type == tpl.default_character_type:
            # 收集该部件实际拥有的所有动作
            owned_actions: set[str] = set()
            for col in matrix.values():
                owned_actions.update(col.keys())
            combat = {"attack", "skill", "hurt", "block", "dead"}
            ride = {"ride_idle", "ride_run"}
            # 无战斗动作也无坐骑动作 → NPC（只有 idle/run）
            if not (owned_actions & combat) and not (owned_actions & ride):
                part_type = "npc"
    # fills 的查漏基准：与主体一致（按角色类型），但结果记入警告级
    missing_directions_ = missing_directions
    missing_actions_ = missing_actions
    for d in (matrix if part != "fills" else set(tpl.directions)):
        if part == "fills" and d not in matrix:
            missing_actions_.setdefault(d, list(tpl.expected_actions(part_type, d)))
            continue
        expected = tpl.expected_actions(part_type, d)
        miss = [a for a in expected if a not in matrix[d]]
        if miss:
            missing_actions_[d] = miss

    if part == "fills":
        warning_directions = [d for d in tpl.directions if d not in matrix]
        warning_actions = {d: a for d, a in missing_actions_.items() if a}
        return PartData(
            folder=folder, res_id=res_id, part=part, matrix=matrix,
            character_type=char_type, effective_type=part_type,
            missing_directions=[], missing_actions={},
            extra_actions=extra_actions,
            warning_directions=warning_directions, warning_actions=warning_actions,
        )

    return PartData(
        folder=folder, res_id=res_id, part=part, matrix=matrix,
        character_type=char_type, effective_type=part_type,
        missing_directions=missing_directions, missing_actions=missing_actions,
        extra_actions=extra_actions,
    )


def _transpose(matrix: dict[str, dict[str, ActionData]]) -> dict[str, dict[str, ActionData]]:
    """action→direction 层级时，转置为 direction→action。"""
    out: dict[str, dict[str, ActionData]] = {}
    for action, col in matrix.items():
        for direction, ad in col.items():
            out.setdefault(direction, {})[action] = ad
    return out


def scan_root(root: Path, tpl: Template, char_type_of=None, max_depth: int = 4) -> ScanResult:
    """扫描入口：支持多级目录，递归找到任意深度的部件文件夹。

    - 拖入部件文件夹：单部件（root 记为父目录）。
    - 拖入角色文件夹（如 拓跋影(双刀)）：递归一层找到其下部件。
    - 拖入根目录（如 新ID）：递归找到 角色/部件 全部。

    关键：scan_part 一律用「真实完整路径」调用，绝不用 root/部件名 重新拼接，
    避免把 角色/部件 错拼成 根/部件（导致后台解码时文件不存在）。
    """
    root = Path(root)
    # 情况1：拖入的本身就是部件文件夹
    single = scan_part(root, tpl, char_type_of)
    if single is not None:
        result = ScanResult(root=root.parent, parts=[single])
        result.groups = _group_parts(result.parts, tpl, char_type_of)
        return result

    # 情况2：父级 / 多级目录 → 递归收集所有部件文件夹
    result = ScanResult(root=root)
    if not root.is_dir():
        return result
    for pf in _find_part_folders(root, tpl, max_depth):
        pd = scan_part(pf, tpl, char_type_of)
        if pd is not None:
            result.parts.append(pd)
        else:
            result.ignored.append(pf.name)
    result.groups = _group_parts(result.parts, tpl, char_type_of)
    return result


def _find_part_folders(root: Path, tpl: Template, max_depth: int) -> list[Path]:
    """BFS 收集 root 下所有「叶子部件文件夹」（内层直接是 direction）。

    - 叶子部件文件夹：收集，且**不再下钻**（其下是 方向/动作，不是部件）。
    - 非叶子目录（角色名/分类文件夹/ID壳）：子目录是部件文件夹而非 direction，
      继续向下递归，直到 max_depth。名字是否命中不作为叶子判据——如 怪物/503006/
      名字是纯ID但内层是 503006_body 壳，不是部件文件夹，必须继续下钻。
    - 用真实完整路径返回，调用方直接 scan_part(该路径) 即得正确帧路径。
    """
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name)
        except (PermissionError, OSError):
            continue
        for c in children:
            if not c.is_dir():
                continue
            if _is_leaf_part_folder(c, tpl):
                found.append(c)                 # 叶子部件文件夹：收集，不继续下钻
            elif _is_flat_folder(c, tpl):
                found.append(c)                 # 扁平结构资源（特效类）：文件直接在根目录
            else:
                stack.append((c, depth + 1))    # 角色/分类/ID壳文件夹：继续递归
    return found


def _group_parts(parts: list[PartData], tpl: Template, char_type_of=None) -> list[IdGroup]:
    """按 res_id 分组，并计算组级缺漏 + 同 ID 配套校验（交集对比）。"""
    by_id: dict[str, list[PartData]] = {}
    for p in parts:
        by_id.setdefault(p.res_id, []).append(p)

    groups: list[IdGroup] = []
    for res_id, members in by_id.items():
        # 组内排序：layer_order 越靠前（越底层）排越前
        members.sort(key=lambda p: tpl.layer_rank(p.part))
        char_type = tpl.resolve_char_type(char_type_of(res_id) if char_type_of else None)
        grp = IdGroup(res_id=res_id, parts=members, character_type=char_type)
        # 资源分类标注（左栏 chips 过滤用）：部件特征优先于 ID 前缀
        grp.category = tpl.classify(res_id, [p.part for p in members])
        # 扁平资源组（特效类）：无方向/动作约定，跳过全部查漏，仅保留帧号断档检测
        grp.is_flat = bool(members) and all(p.is_flat for p in members)
        if grp.is_flat:
            grp.effective_type = "flat"
            groups.append(grp)
            continue
        # 组级方向/动作缺漏：以「组内所有部件并集拥有」为参照，对照类型×方向基准
        # 部位类型覆盖：组内所有部件都是 wings → wings 规则；都是 ride_* → mount 规则
        owned_dirs: set[str] = set()
        owned_per_dir: dict[str, set[str]] = {}
        for p in members:
            for d, col in p.matrix.items():
                owned_dirs.add(d)
                owned_per_dir.setdefault(d, set()).update(col.keys())
        grp.missing_directions = [d for d in tpl.directions if d not in owned_dirs]
        # 判定组级查漏类型：排除 shadow/fills（配套部件，不参与类型判定）
        # 非 shadow/fills 部件全 wings → wings；全 ride_* → mount；
        # 否则用 char_type，但若 char_type=default 且组内无任何战斗/坐骑动作 → NPC 兜底。
        type_parts = [p.part for p in members if p.part not in ("shadow", "fills", None)]
        if type_parts and all(pt == "wings" for pt in type_parts):
            group_type = "wings"
        elif type_parts and all(pt in ("ride_front", "ride_back") for pt in type_parts):
            group_type = "mount"
        else:
            group_type = char_type
            if char_type == tpl.default_character_type:
                owned_all: set[str] = set()
                for acts in owned_per_dir.values():
                    owned_all |= acts
                combat = {"attack", "skill", "hurt", "block", "dead"}
                ride = {"ride_idle", "ride_run"}
                if not (owned_all & combat) and not (owned_all & ride):
                    group_type = "npc"
        grp.effective_type = group_type
        grp.missing_actions = {}
        for d in tpl.directions:
            if d in grp.missing_directions:
                continue
            expected = tpl.expected_actions(group_type, d)
            miss = [a for a in expected if a not in owned_per_dir.get(d, set())]
            if miss:
                grp.missing_actions[d] = miss
        # 组级多余动作：组内各部件 extra_actions 的并集
        grp.extra_actions = {}
        extra_per_dir: dict[str, set[str]] = {}
        for p in members:
            for d, acts in p.extra_actions.items():
                extra_per_dir.setdefault(d, set()).update(acts)
        for d, acts in extra_per_dir.items():
            grp.extra_actions[d] = sorted(acts)
        # 组级 fills 警告：组内存在主体部件，但整组无 fills → 缺 fills（警告级）
        non_fill_parts = [p for p in members if p.part != "fills"]
        fills_parts = [p for p in members if p.part == "fills"]
        if non_fill_parts and not fills_parts:
            nf_dirs: set[str] = set()
            for p in non_fill_parts:
                nf_dirs |= set(p.available_directions())
            grp.missing_fills = sorted(nf_dirs)
        # 有 fills 部件时，合并其自身警告级缺失到组级
        for p in fills_parts:
            grp.fills_warning_directions = sorted(set(grp.fills_warning_directions) | set(p.warning_directions))
            for d, acts in p.warning_actions.items():
                cur = set(grp.fills_warning_actions.get(d, []))
                cur.update(acts)
                grp.fills_warning_actions[d] = sorted(cur)
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
