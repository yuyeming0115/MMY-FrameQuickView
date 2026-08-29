"""规则模板加载 / 解析 / 保存。

模板为 JSON 文件，存于 templates/ 目录，字段见 开发文档/01-需求方案与UI设计-v0.3.md 第 4 节。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Template:
    name: str
    folder_pattern: str = "{id}(_{part})?"
    parts: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    hierarchy: list[str] = field(default_factory=lambda: ["direction", "action"])
    layer_order: list[str] = field(default_factory=list)
    frame_pattern: str = r"^(\d{4})\.png$"
    extensions: list[str] = field(default_factory=lambda: [".png"])
    # 角色类型 → 方向 → 该方向「应当存在」的动作列表（查漏补缺的基准）。
    # 用于覆盖「主角 vs 伙伴/怪物/boss/npc/坐骑/翅膀」两套不同约定。
    action_rules: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # 匹配表中未标注类型时的默认角色类型。
    default_character_type: str = "non_protagonist"
    # 分类规则（有序匹配，先具体后宽泛）：左栏分类 chips 过滤 + 特效扁平结构识别。
    # 每条：{"name": 分类名, "part_any": [部件名...], "id_prefix": [ID前缀...],
    #        "flat": bool, "frame_pattern": stem正则, "virtual_direction": 虚拟方向名}
    categories: list[dict] = field(default_factory=list)
    # 套装组合并阈值：同父文件夹下跨 ID 部件数 ≤ 该值才合并为套装组（防大库误合并）。
    outfit_merge_max: int = 16

    # 角色类型归一化：匹配表第3列 → action_rules 的 key。
    # 主角类 → protagonist；伙伴/怪物/boss → non_protagonist；
    # NPC → npc；翅膀 → wings；坐骑 → mount；未标注 → default_character_type。
    CHAR_TYPE_ALIASES = {
        "主角": "protagonist",
        "protagonist": "protagonist",
        "hero": "protagonist",
        "player": "protagonist",
        "伙伴": "non_protagonist",
        "companion": "non_protagonist",
        "怪物": "non_protagonist",
        "monster": "non_protagonist",
        "boss": "non_protagonist",
        "npc": "npc",
        "坐骑": "mount",
        "mount": "mount",
        "翅膀": "wings",
        "wings": "wings",
    }

    def resolve_char_type(self, raw: str | None) -> str:
        """把匹配表里的类型字段归一化为 action_rules 的 key。"""
        if raw is None:
            return self.default_character_type
        return self.CHAR_TYPE_ALIASES.get(raw.strip().lower(), self.default_character_type)

    def expected_actions(self, char_type: str, direction: str) -> list[str]:
        """某角色类型在某方向「应当存在」的动作；方向/类型不在规则内返回空（不查漏）。"""
        return list(self.action_rules.get(char_type, {}).get(direction, []))

    # ---------- 分类 ----------
    def _match_category(self, res_id: str, parts: list[str | None]) -> dict | None:
        """按 categories 有序匹配（部件特征优先于 ID 前缀）返回命中的分类；未命中返回 None。

        - part_any：组内任一部件名命中（如 ride_* → 坐骑、wings → 翅膀）
        - id_prefix：资源 ID 前缀命中（如 50105 → 特效、502 → 伙伴）
        顺序即优先级：坐骑/翅膀/特效等具体规则须排在 主角(501)/怪物(503) 等宽泛前缀之前。
        """
        for cat in self.categories:
            part_any = cat.get("part_any") or []
            if part_any and any(p in part_any for p in parts):
                return cat
            prefixes = cat.get("id_prefix") or []
            if prefixes and any(res_id.startswith(pf) for pf in prefixes):
                return cat
        return None

    def classify(self, res_id: str, parts: list[str | None]) -> str:
        cat = self._match_category(res_id, parts)
        return cat.get("name", "") if cat else ""

    def infer_char_type(self, res_id: str, parts: list[str | None]) -> str | None:
        """类别推断角色类型：命中带 char_type 字段的分类时返回该类型。

        用于匹配表未标注类型时的兜底（如 501 前缀 → protagonist）。
        匹配表显式标注始终优先于类别推断；分类未配 char_type 返回 None。
        """
        cat = self._match_category(res_id, parts)
        if cat is None:
            return None
        return cat.get("char_type") or None

    def flat_rule(self, res_id: str) -> dict | None:
        """资源 ID 命中的扁平结构（特效类）规则；未命中返回 None。"""
        for cat in self.categories:
            if not cat.get("flat"):
                continue
            prefixes = cat.get("id_prefix") or []
            if any(res_id.startswith(pf) for pf in prefixes):
                return cat
        return None

    def category_names(self) -> list[str]:
        return [c.get("name", "") for c in self.categories if c.get("name")]

    # ---------- 解析辅助 ----------
    def frame_regex(self) -> re.Pattern:
        return re.compile(self.frame_pattern, re.IGNORECASE)

    def parse_folder_name(self, name: str) -> tuple[str, str | None] | None:
        """解析部件文件夹名 → (资源ID, 部位|None)；不匹配返回 None。

        规则：优先匹配 {id}_{part}（part 按长度降序避免 ride_front 被 front 截胡），
        其次接受纯 ID（无部位后缀的整体资源）。
        """
        for part in sorted(self.parts, key=len, reverse=True):
            suffix = "_" + part
            if name.lower().endswith(suffix.lower()):
                res_id = name[: -len(suffix)]
                if res_id:
                    return res_id, part
        if name and " " not in name:
            # 纯 ID（无下划线后缀）。允许数字或字母数字组合，保守起见不强制 isdigit
            if re.fullmatch(r"[A-Za-z0-9]+", name):
                return name, None
        return None

    def ext_set(self) -> set[str]:
        return {e.lower() for e in self.extensions}

    def layer_rank(self, part: str | None) -> int:
        """叠层顺序：layer_order 中越前越底层；未列入的排最上。"""
        if part is None:
            return len(self.layer_order)
        try:
            return self.layer_order.index(part)
        except ValueError:
            return len(self.layer_order)

    # ---------- 持久化 ----------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "folder_pattern": self.folder_pattern,
            "parts": self.parts,
            "directions": self.directions,
            "actions": self.actions,
            "hierarchy": self.hierarchy,
            "layer_order": self.layer_order,
            "frame_pattern": self.frame_pattern,
            "extensions": self.extensions,
            "action_rules": self.action_rules,
            "default_character_type": self.default_character_type,
            "categories": self.categories,
            "outfit_merge_max": self.outfit_merge_max,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return cls(
            name=data.get("name", "未命名"),
            folder_pattern=data.get("folder_pattern", "{id}(_{part})?"),
            parts=list(data.get("parts", [])),
            directions=list(data.get("directions", [])),
            actions=list(data.get("actions", [])),
            hierarchy=list(data.get("hierarchy", ["direction", "action"])),
            layer_order=list(data.get("layer_order", [])),
            frame_pattern=data.get("frame_pattern", r"^(\d{4})\.png$"),
            extensions=list(data.get("extensions", [".png"])),
            action_rules=dict(data.get("action_rules", {})),
            default_character_type=data.get("default_character_type", "non_protagonist"),
            categories=list(data.get("categories", [])),
            outfit_merge_max=int(data.get("outfit_merge_max", 16)),
        )


def templates_dir() -> Path:
    """定位 templates 目录：优先 exe/脚本旁，其次项目根。"""
    here = Path(__file__).resolve()
    project_root = here.parents[2]
    return project_root / "templates"


def load_templates(directory: Path | None = None) -> list[Template]:
    """扫描目录加载全部模板；目录不存在返回空列表。"""
    directory = directory or templates_dir()
    result: list[Template] = []
    if not directory.is_dir():
        return result
    for fp in sorted(directory.glob("*.json")):
        try:
            result.append(Template.from_dict(json.loads(fp.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[template] 跳过损坏模板 {fp.name}: {exc}")
    return result


def save_template(tpl: Template, directory: Path | None = None) -> Path:
    directory = directory or templates_dir()
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", tpl.name)
    fp = directory / f"{safe}.json"
    fp.write_text(json.dumps(tpl.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return fp
