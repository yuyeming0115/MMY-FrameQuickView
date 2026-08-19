"""M11：多级目录递归扫描 + phantom 路径修复验证。

模拟真实结构：
    根 / 角色名文件夹 / 部件文件夹 / 方向 / 动作 / 帧.png

验证：
1. 拖「根」(新ID) → 递归找到所有角色下的部件，且帧路径包含真实角色层。
2. 拖「角色文件夹」(逍遥仙(扇)) → 找到其下部件。
3. 帧路径绝不含 phantom（缺失角色层），即绝不会出现 根/部件/方向/动作/帧 这种错位路径。
4. 单部件文件夹直接拖入仍正常。
"""
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.scanner import scan_root
from src.core.template import Template

TPL = Template(
    name="测试",
    parts=["shadow", "weapon", "hair", "body"],
    directions=["E", "N", "NW", "S", "SE"],
    actions=["idle", "run", "attack", "ride_idle", "ride_run", "block", "dead", "hurt", "skill"],
    hierarchy=["direction", "action"],
    action_rules={},
)


def _mk_png(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def _build_tree(root: Path):
    # 角色A：2 个部件
    for part in ("501031003_shadow", "501031003_weapon"):
        for d in ("E", "N"):
            _mk_png(root / "逍遥仙(扇)" / part / d / "idle" / "0001.png")
    # 角色B：1 个部件
    _mk_png(root / "拓跋影(双刀)" / "501031005_shadow" / "E" / "idle" / "0001.png")
    # 一个非部件（干扰）文件夹，里面再套一层部件
    _mk_png(root / "某分类" / "502019_hair" / "E" / "idle" / "0001.png")
    return root


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m11_"))
    try:
        root = _build_tree(tmp / "新ID")

        print("== 情况 A：拖入根目录 新ID ==")
        res = scan_root(root, TPL)
        ids = sorted(g.res_id for g in res.groups)
        print("  分组 res_id:", ids)
        print("  部件数:", len(res.parts), "忽略:", res.ignored)
        assert "501031003" in ids and "501031005" in ids and "502019" in ids, "多级递归未找全"
        # 验证帧路径含真实角色层，且无 phantom（根/部件 缺层）
        phantom = [str(p) for p in (f for pd in res.parts for col in pd.matrix.values() for ad in col.values() for f in ad.frames)
                   if "新ID\\501031003_shadow" in str(p) or "/新ID/501031003_shadow" in str(p)]
        assert not phantom, f"出现 phantom 路径: {phantom[:3]}"
        sample = next(iter(res.parts[0].matrix.values()))["idle"].frames[0]
        print("  样例帧路径:", sample)
        assert "逍遥仙(扇)" in str(sample) or "拓跋影(双刀)" in str(sample) or "某分类" in str(sample)
        print("  ✓ 递归找全 + 真实完整路径（无 phantom）")

        print("== 情况 B：拖入角色文件夹 逍遥仙(扇) ==")
        res2 = scan_root(root / "逍遥仙(扇)", TPL)
        ids2 = sorted(g.res_id for g in res2.groups)
        print("  分组 res_id:", ids2, "部件数:", len(res2.parts))
        assert ids2 == ["501031003"], ids2
        assert len(res2.parts) == 2, "角色文件夹下应找到 2 个部件"
        print("  ✓ 角色文件夹扫描正确")

        print("== 情况 C：直接拖入单部件文件夹 502019_hair ==")
        res3 = scan_root(root / "某分类" / "502019_hair", TPL)
        assert len(res3.parts) == 1 and res3.parts[0].res_id == "502019"
        assert res3.root == root / "某分类"
        print("  ✓ 单部件扫描正确，root =", res3.root.name)

        print("\nALL M11 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    main()
