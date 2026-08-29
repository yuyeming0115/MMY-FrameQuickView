"""ID-中文名映射表：加载 / 查找 / 自动登记 / 回写。

文件格式（与用户手写表兼容，纯文本易 diff 易分享）：
    # 注释行
    502019<TAB>杜如晦
    50111100_hair<TAB>主角1昆仑剑侠（剑）<TAB>头发

省心维护四件套（设计文档 v0.3 第 3.4 节）：
0. 自动发现 —— 拖入目录及父级自动找 *匹配表*.txt，无需配置
1. 自动登记 —— 扫描后表里没有的 ID 自动追加 `ID\\t（待命名）`，维护=补空
2. 列表内联改名 —— 左栏 F2/双击改名，回车写回本文件
3. 文件热更新 —— 外部编辑后自动重载（QFileSystemWatcher，app 层接线）
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PLACEHOLDER = "（待命名）"

# 部位中文名兜底（映射表第3列可覆盖/扩充）
PART_CN_DEFAULT = {
    "hair": "头发", "body": "身体", "weapon": "武器", "wings": "翅膀",
    "shadow": "影子", "fills": "填充", "ride_front": "骑乘前", "ride_back": "骑乘后",
}


class NameMap:
    def __init__(self, path: Path | None = None):
        self.path: Path | None = path
        self._names: dict[str, str] = {}        # key(文件夹名或纯ID) -> 名字
        self._part_cn: dict[str, str] = dict(PART_CN_DEFAULT)
        self._types: dict[str, str] = {}         # 纯ID -> 角色类型(主角/怪物/...)

    # ---------------- 加载 ----------------
    def load(self, path: Path) -> None:
        self.path = Path(path)
        self._names.clear()
        text = self._read_text(self.path)
        if text is None:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 优先 TAB 分隔，兼容多空格分隔
            cols = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line)
            cols = [c.strip() for c in cols if c.strip()]
            if len(cols) < 2:
                continue
            key, name = cols[0], cols[1]
            if name == PLACEHOLDER:
                continue            # 待命名不算有效映射
            self._names[key] = name  # 重复 key 后出现者生效
            if len(cols) >= 3:
                if "_" in key:
                    # 部件行（50111100_hair）：第3列 = 部位中文名 → 回填 part_cn
                    part = key.rsplit("_", 1)[-1]
                    if cols[2]:
                        self._part_cn[part] = cols[2]
                else:
                    # 纯 ID 行（502019）：第3列 = 角色类型（主角/怪物/...）
                    if cols[2]:
                        self._types[key] = cols[2]

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    # ---------------- 查找 ----------------
    def lookup(self, folder_name: str, res_id: str) -> str | None:
        """先按完整文件夹名（50111100_hair），再按纯 ID（50111100）。"""
        return self._names.get(folder_name) or self._names.get(res_id)

    def part_cn(self, part: str | None) -> str:
        if part is None:
            return "整体"
        return self._part_cn.get(part, part)

    def shadow_owner(self, res_id: str, siblings: list[tuple[str, str | None]]) -> str | None:
        """影子部件的归属主件（同 ID 的非 shadow/fills 部件）；body 优先于 weapon。"""
        parts = [pt for rid, pt in siblings
                 if rid == res_id and pt and pt not in ("shadow", "fills")]
        for prefer in ("body", "weapon"):
            if prefer in parts:
                return prefer
        return parts[0] if parts else None

    def part_cn_in(self, part: str | None, res_id: str,
                   siblings: list[tuple[str, str | None]]) -> tuple[str, str | None]:
        """组内部件中文名 → (显示名, 归属主件 | None)。

        影子归属同 ID 主件 → `武器影子` / `身体影子`（组内多个影子时可区分）；
        其余部件与 part_cn 一致。
        """
        cn = self.part_cn(part)
        if part == "shadow":
            owner = self.shadow_owner(res_id, siblings)
            if owner:
                return f"{self.part_cn(owner)}{cn}", owner
        return cn, None

    def char_type(self, res_id: str) -> str | None:
        """纯 ID 对应的角色类型（来自匹配表第3列）；未标注返回 None。"""
        return self._types.get(res_id)

    def display(self, folder_name: str, res_id: str) -> str:
        """组头显示文本：有名字 → `502019 · 杜如晦`；无 → 原 ID。"""
        name = self.lookup(folder_name, res_id)
        return f"{res_id} · {name}" if name else res_id

    # ---------------- 自动登记 ----------------
    def register_missing(self, res_ids: list[str]) -> list[str]:
        """把表里没有的 ID 追加到文件末尾（`ID\\t（待命名）`）。返回新登记的 ID。"""
        if self.path is None:
            return []
        missing = [i for i in dict.fromkeys(res_ids)
                   if i not in self._names and i not in self._registered_keys()]
        if not missing:
            return []
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"\n# ── 自动登记 {datetime.now():%Y-%m-%d %H:%M} ──\n")
            for rid in missing:
                f.write(f"{rid}\t{PLACEHOLDER}\n")
        return missing

    def _registered_keys(self) -> set[str]:
        """含待命名在内的全部已登记 key（避免重复追加）。"""
        if self.path is None:
            return set()
        text = self._read_text(self.path) or ""
        keys = set()
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                cols = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line)
                if cols and cols[0].strip():
                    keys.add(cols[0].strip())
        return keys

    # ---------------- 回写（内联改名） ----------------
    def set_name(self, res_id: str, new_name: str) -> bool:
        """把某 ID 的名字写回文件（就地改行，保留注释与分组结构）。"""
        if self.path is None or not new_name.strip():
            return False
        text = self._read_text(self.path)
        if text is None:
            return False
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            cols = s.split("\t") if "\t" in s else re.split(r"\s{2,}", s)
            if cols and cols[0].strip() == res_id:
                # 保留原行缩进与后续列，仅替换名字列
                parts = line.split("\t")
                if len(parts) >= 2:
                    parts[1] = new_name.strip()
                    lines[idx] = "\t".join(parts)
                else:
                    lines[idx] = f"{res_id}\t{new_name.strip()}"
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self._names[res_id] = new_name.strip()
                return True
        # ID 尚不存在 → 追加
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{res_id}\t{new_name.strip()}\n")
        self._names[res_id] = new_name.strip()
        return True


def discover_map_file(folder: Path, last_known: Path | None = None) -> Path | None:
    """自动发现匹配表：从拖入目录向上递归到盘根找 *匹配表*.txt，返回最近的命中。

    资源目录往往很深（如 ``动画序列帧/角色输出图/50112101``），而匹配表放在
    项目根（``动画序列帧/ID-角色-名字匹配表.txt``）。只查拖入目录+父级两层会
    漏掉，因此这里一路向上递归；命中多个时取「离拖入目录最近」的那个。

    ``last_known`` 为上次成功用过的匹配表路径，向上递归无果时作兜底。
    """
    folder = Path(folder)
    chain: list[Path] = []
    p = folder
    while True:
        chain.append(p)
        parent = p.parent
        if parent == p:       # 已到盘根
            break
        p = parent
    for base in chain:
        try:
            hits = sorted(base.glob("*匹配表*.txt"))
        except OSError:
            continue
        if hits:
            return hits[0]
    if last_known is not None and Path(last_known).exists():
        return Path(last_known)
    return None
