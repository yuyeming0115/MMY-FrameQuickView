"""M18：四层结构（分类 → ID壳 → 部件 → 方向 → 动作）扫描验证。

真实目录形态：E:\\...\\怪物\\503006\\503006_body\\E\\idle\\0001.png ...
相比 M15 多一层「ID壳」（503006 名字是纯 ID，但内层是 503006_body 等部件）。

若 _find_part_folders 以「名字命中」判叶子，503006 会被误当部件文件夹收集且不下钻，
导致 scan_part 找 direction 层失败 → 全部 ignored → 「未识别到部件文件夹」。

验证：
1. 拖入 怪物 → 识别出 503006 的 body/shadow/weapon 三个部件，组成 res_id 503006 组
2. ignored 为空
3. 拖入 ID壳 503006 本身 → 同样能下钻识别
"""
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.scanner import scan_root, _is_leaf_part_folder
from src.core.template import load_templates

TPL = load_templates()[0] if load_templates() else None
if TPL is None:
    from src.core.template import Template
    TPL = Template(
        name="测试",
        parts=["shadow", "weapon", "hair", "body", "wings", "ride_front", "ride_back", "fills"],
        directions=["E", "N", "NW", "S", "SE"],
        actions=["idle", "run", "attack", "skill", "hurt", "block", "dead", "ride_idle", "ride_run"],
        hierarchy=["direction", "action"],
        action_rules={},
    )


def _mk_png(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def _build_tree(root: Path):
    base = root / "怪物" / "503006"
    for part in ("body", "shadow", "weapon"):
        for d in ("E", "N"):
            for act in ("idle", "run"):
                _mk_png(base / f"503006_{part}" / d / act / "0001.png")
    # 干扰：无 ID 前缀的分类文件夹，不应被误识别
    (root / "怪物" / "README").mkdir(parents=True, exist_ok=True)
    return root


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m18_"))
    try:
        root = _build_tree(tmp / "轩辕剑（昆仑）")
        monster = root / "怪物"
        shell = monster / "503006"

        print("== 情况 A：拖入 怪物（分类）==")
        res = scan_root(monster, TPL)
        print(f"  部件数: {len(res.parts)}, 组数: {len(res.groups)}, 忽略: {res.ignored}")
        assert len(res.parts) == 3, f"应识别 3 个部件，实际 {len(res.parts)}"
        assert not res.ignored, f"不应有忽略，实际 {res.ignored}"
        parts = sorted(p.part for p in res.parts)
        assert parts == ["body", "shadow", "weapon"], f"部件应为 body/shadow/weapon，实际 {parts}"
        assert all(p.res_id == "503006" for p in res.parts), "res_id 应全为 503006"
        assert len(res.groups) == 1 and res.groups[0].res_id == "503006", "应聚成 1 个组"
        print("  ✓ 分类层正确下钻识别 ID壳 下部件")

        print("\n== 情况 B：直接拖入 ID壳 503006 ==")
        res2 = scan_root(shell, TPL)
        print(f"  部件数: {len(res2.parts)}, 忽略: {res2.ignored}")
        assert len(res2.parts) == 3, f"应识别 3 个部件，实际 {len(res2.parts)}"
        print("  ✓ ID壳 本身也能下钻识别")

        print("\n== 情况 C：叶子判定单元验证 ==")
        assert _is_leaf_part_folder(shell, TPL) is False, "503006 (ID壳) 不应判为叶子部件夹"
        assert _is_leaf_part_folder(shell / "503006_body", TPL) is True, "503006_body 应为叶子部件夹"
        assert _is_leaf_part_folder(root / "怪物" / "README", TPL) is False, "README 不应误判"
        print("  ✓ _is_leaf_part_folder 判定正确")

        print("\nALL M18 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()