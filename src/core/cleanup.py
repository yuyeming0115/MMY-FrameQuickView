"""多余动作识别与安全清理（移入系统回收站）。

职责：
1. 结合模板 `action_rules` 与 `scanner.extra_actions` 识别多余动作：
   - A 类（规则外动作）：存在但超出当前角色类型×方向约定（如主角 E/N/S 的 attack）
   - B 类（模板外未知动作）：目录存在但动作名完全不在模板 `actions` 中
   - 关键防误判：shadow 部件查漏类型为 ""，判断多余时继承所属组或自身 `character_type`
2. 解析各动作对应的磁盘路径（兼容散帧正/反层级目录与 M40 平铺图集 `.plist/.png`）。
3. 通过 `QFile.moveToTrash` 安全移入系统回收站（绝不做永久硬删除）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFile

from .scanner import IdGroup, PartData
from .template import Template


@dataclass(frozen=True)
class CleanupItem:
    """单条待清理动作记录（用于弹窗清单确认与移入回收站）。"""

    part_name: str
    direction: str
    action: str
    paths: tuple[Path, ...]
    frame_count: int
    reason: str


def cleanup_target_parts(parts: list[PartData]) -> list[PartData]:
    """筛选参与多余动作判定/清理的部件（跳过 flat 特效，非纯 worldboss 组排除 worldboss）。"""
    core = [p for p in parts if not p.is_flat and p.part != "worldboss"]
    return core or [p for p in parts if not p.is_flat]


def part_rule_type(part: PartData, group: IdGroup | None = None) -> str:
    """判定部件用于「多余动作」校验的规则类型。

    - 扁平特效（is_flat）无方向/动作约定 → 返回 ""（豁免）
    - shadow 部件本身 `effective_type == ""`，继承所属组（优先）或自身 `character_type`，
      防止主角影子上的 ride_idle/ride_run 被误当成 non_protagonist 多余动作。
    """
    if part.is_flat:
        return ""
    if part.part == "shadow":
        if group is not None:
            return group.effective_type or group.character_type
        return part.character_type
    return part.effective_type or part.character_type


def action_disk_paths(
    part: PartData,
    direction: str,
    action: str,
    tpl: Template,
) -> tuple[Path, ...]:
    """解析某部件 (方向, 动作) 在磁盘上的实际路径。

    - 图集模式（M40）：返回存在的 `(plist, png)` 成对文件
    - 散帧模式：按 `tpl.hierarchy` 返回存在的动作子目录 `(action_dir,)`
    """
    if part.is_flat or not direction or not action:
        return ()
    ad = part.action_data(direction, action)
    if ad is not None and ad.plist_path is not None:
        files: list[Path] = []
        if ad.plist_path.is_file():
            files.append(ad.plist_path)
        png = ad.plist_path.with_suffix(".png")
        if png.is_file():
            files.append(png)
        return tuple(files)

    if tpl.hierarchy and tpl.hierarchy[0] == "action":
        target = part.folder / action / direction
    else:
        target = part.folder / direction / action
    return (target,) if target.is_dir() else ()


def part_extra_actions(
    part: PartData,
    tpl: Template,
    group: IdGroup | None = None,
) -> dict[str, list[str]]:
    """计算单部件的全部多余动作 `{direction: [action, ...]}`。

    合并两类来源：
    - A 类：`part.matrix` 中存在但不在 `tpl.expected_actions(rule_type, d)` 内的动作
    - B 类：`part.extra_actions` 中的模板外未知动作
    """
    if part.is_flat:
        return {}
    by_dir: dict[str, set[str]] = {}

    rule_type = part_rule_type(part, group)
    if rule_type and rule_type in tpl.action_rules:
        for d, cols in part.matrix.items():
            expected = set(tpl.expected_actions(rule_type, d))
            for a in cols:
                if a not in expected:
                    by_dir.setdefault(d, set()).add(a)

    for d, acts in part.extra_actions.items():
        if acts:
            by_dir.setdefault(d, set()).update(acts)

    return {d: sorted(acts) for d, acts in by_dir.items() if acts}


def _count_frames(
    part: PartData,
    direction: str,
    action: str,
    paths: tuple[Path, ...],
    tpl: Template,
) -> int:
    ad = part.action_data(direction, action)
    if ad is not None:
        return ad.count
    if paths and paths[0].is_dir():
        exts = tpl.ext_set()
        rx = tpl.frame_regex()
        try:
            return sum(
                1 for fp in paths[0].iterdir()
                if fp.is_file() and fp.suffix.lower() in exts and rx.match(fp.name)
            )
        except OSError:
            return 0
    return 0


def _is_unknown_action(part: PartData, direction: str, action: str, tpl: Template) -> bool:
    return action in part.extra_actions.get(direction, []) and action not in tpl.actions


def _action_reason(
    part: PartData,
    direction: str,
    action: str,
    tpl: Template,
    group: IdGroup | None,
) -> str:
    if _is_unknown_action(part, direction, action, tpl):
        return "模板外未知动作"
    if action in part_extra_actions(part, tpl, group).get(direction, []):
        return "超出模板规则"
    return "手动删除"


def collect_extra_cleanup_items(
    parts: list[PartData],
    tpl: Template,
    group: IdGroup | None = None,
) -> list[CleanupItem]:
    """收集部件列表中的全部多余动作待清理项（跳过 flat 特效与不存在路径）。"""
    items: list[CleanupItem] = []
    for part in parts:
        if part.is_flat:
            continue
        extras = part_extra_actions(part, tpl, group)
        for d in sorted(extras):
            for a in extras[d]:
                paths = action_disk_paths(part, d, a, tpl)
                if not paths:
                    continue
                reason = "模板外未知动作" if _is_unknown_action(part, d, a, tpl) else "超出模板规则"
                items.append(CleanupItem(
                    part_name=part.name,
                    direction=d,
                    action=a,
                    paths=paths,
                    frame_count=_count_frames(part, d, a, paths, tpl),
                    reason=reason,
                ))
    return items


def collect_action_cleanup_items(
    parts: list[PartData],
    direction: str,
    action: str,
    tpl: Template,
    group: IdGroup | None = None,
) -> list[CleanupItem]:
    """收集部件列表中指定 `(direction, action)` 的待清理项（供右键单点清理）。"""
    items: list[CleanupItem] = []
    for part in parts:
        if part.is_flat:
            continue
        paths = action_disk_paths(part, direction, action, tpl)
        if not paths:
            continue
        items.append(CleanupItem(
            part_name=part.name,
            direction=direction,
            action=action,
            paths=paths,
            frame_count=_count_frames(part, direction, action, paths, tpl),
            reason=_action_reason(part, direction, action, tpl, group),
        ))
    return items


def move_to_trash(
    paths: list[Path],
    trash_fn: Callable[[Path], bool] | None = None,
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """安全将文件或目录移入系统回收站（严禁永久硬删除）。

    返回 `(已移入列表, [(失败路径, 错误信息), ...])`。
    """
    fn = trash_fn or (lambda p: bool(QFile.moveToTrash(str(p))))
    seen: set[str] = set()
    moved: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for raw in paths:
        p = Path(raw)
        norm = os.path.normcase(os.path.abspath(str(p)))
        if norm in seen:
            continue
        seen.add(norm)
        if not p.exists():
            failed.append((p, "路径不存在"))
            continue
        try:
            ok = fn(p)
        except Exception as exc:
            failed.append((p, str(exc)))
            continue
        if ok and not p.exists():
            moved.append(p)
        else:
            failed.append((p, "移入回收站失败（可能被占用、无权限或当前路径不支持回收站）"))

    return moved, failed
