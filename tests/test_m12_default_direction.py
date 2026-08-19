"""验证：默认方向（首次进入部件/组，direction=None）优先 SE，缺 SE 时回退首个可用方向。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '.')

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from src.core.scanner import scan_part, scan_root
from src.core.template import Template
from src.ui.button_matrix import ButtonMatrix, DEFAULT_DIRECTION


TPL = Template(
    name="测试",
    folder_pattern="{id}(_{part})?",
    parts=["shadow", "weapon", "hair", "body", "body_shadow", "weapon_shadow"],
    directions=["E", "N", "NW", "S", "SE"],
    actions=["idle", "run", "attack"],
    hierarchy=["direction", "action"],
    action_rules={},
    default_character_type="non_protagonist",
)


def _mk_frames(part_dir: Path, dirs: list[str]) -> None:
    """在某个部件目录下造 方向/idle/0001.png 帧。"""
    for d in dirs:
        fdir = part_dir / d / "idle"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # 占位（非真图），scan_root 只认文件存在


def _matrix() -> ButtonMatrix:
    m = ButtonMatrix()
    m.set_template(TPL)
    return m


ok = True

# ---- [1] 部件含 SE → 默认 SE ----
d = Path(tempfile.mkdtemp())
part_dir = d / "角色A" / "501031003_shadow"
_mk_frames(part_dir, ["E", "SE"])
pd = scan_part(part_dir, TPL)
m = _matrix()
m.show_part(pd, None, None)
got = m.current()[0]
print(f"[1] 部件含 SE: 默认方向 = {got!r} (expect 'SE')")
ok = ok and got == "SE"

# ---- [2] 部件缺 SE（仅 E）→ 回退首个可用方向 E ----
d2 = Path(tempfile.mkdtemp())
part_dir2 = d2 / "501031003_shadow"
_mk_frames(part_dir2, ["E"])
pd2 = scan_part(part_dir2, TPL)
m2 = _matrix()
m2.show_part(pd2, None, None)
got2 = m2.current()[0]
print(f"[2] 部件缺 SE(仅 E): 默认方向 = {got2!r} (expect 'E' 回退)")
ok = ok and got2 == "E"

# ---- [3] 组含 SE → 默认 SE ----
d3 = Path(tempfile.mkdtemp())
root = d3 / "新ID"
p1 = root / "角色A" / "501031003_shadow"
p2 = root / "角色A" / "501031003_body"
_mk_frames(p1, ["E", "SE"])
_mk_frames(p2, ["SE", "N"])
res = scan_root(root, TPL)
grp = next((g for g in res.groups if g.res_id == "501031003"), None)
assert grp is not None, "组 501031003 未生成"
m3 = _matrix()
m3.show_group(grp, None, None)
got3 = m3.current()[0]
print(f"[3] 组含 SE: 默认方向 = {got3!r} (expect 'SE')")
ok = ok and got3 == "SE"

# ---- [4] 常量值确认 ----
print(f"[4] DEFAULT_DIRECTION = {DEFAULT_DIRECTION!r} (expect 'SE')")
ok = ok and DEFAULT_DIRECTION == "SE"

print("ALL M12 PASS" if ok else "M12 FAIL")
sys.exit(0 if ok else 1)
