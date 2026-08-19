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
