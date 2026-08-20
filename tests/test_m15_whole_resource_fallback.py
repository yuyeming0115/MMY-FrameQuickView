"""M15：整体资源（{id}_{中文名}）结构检测兜底验证。

模拟新工程结构：
    503026_神龙/E/{idle,run}/0001.png...   ← 整体资源，parse_folder_name 不命中
    504004_黑龙王/Render_Output/...        ← 分类文件夹，结构兜底不应命中

验证：
1. 拖入 503026_神龙 → 识别为整体资源，res_id=503026，part=None
2. 拖入 504004_黑龙王 → 不误判为资源，继续下钻找到 503026_神龙
3. 拖入 Render_Output → 递归找到 503026_神龙
"""
import shutil
import tempfile
from pathlib import Path

from PIL import Image
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.scanner import scan_root, _parse_folder_or_struct, _looks_like_part_folder
from src.core.template import load_templates

TPL = load_templates()[0] if load_templates() else None
if TPL is None:
    # 兜底：用内联模板
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
    # 整体资源 503026_神龙：5 方向 × idle/run
    for d in ("E", "N", "NW", "S", "SE"):
        for act in ("idle", "run"):
            _mk_png(root / "504004_黑龙王" / "Render_Output" / "503026_神龙" / d / act / "0001.png")
    return root


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m15_"))
    try:
        root = _build_tree(tmp / "轩辕剑（昆仑）")
        shenlong = root / "504004_黑龙王" / "Render_Output" / "503026_神龙"
        heilogwang = root / "504004_黑龙王"

        print("== 情况 A：直接拖入 503026_神龙 ==")
        res = scan_root(shenlong, TPL)
        assert len(res.parts) == 1, f"应识别 1 个整体资源，实际 {len(res.parts)}"
        p = res.parts[0]
        print(f"  res_id={p.res_id}, part={p.part}, name={p.name}")
        assert p.res_id == "503026", f"res_id 应为 503026，实际 {p.res_id}"
        assert p.part is None, f"part 应为 None，实际 {p.part}"
        dirs = p.available_directions()
        print(f"  方向: {dirs}")
        assert "E" in dirs and "SE" in dirs, "方向应被扫描到"
        print("  ✓ 整体资源识别正确")

        print("\n== 情况 B：拖入分类文件夹 504004_黑龙王 ==")
        res2 = scan_root(heilogwang, TPL)
        print(f"  部件数: {len(res2.parts)}, 忽略: {res2.ignored}")
        assert len(res2.parts) == 1, f"应递归找到 1 个资源，实际 {len(res2.parts)}"
        assert res2.parts[0].res_id == "503026", "递归后应找到 503026"
        print("  ✓ 分类文件夹不误判，正确下钻")

        print("\n== 情况 C：拖入 Render_Output ==")
        res3 = scan_root(shenlong.parent, TPL)
        assert len(res3.parts) == 1 and res3.parts[0].res_id == "503026"
        print("  ✓ Render_Output 层扫描正确")

        print("\n== 情况 D：结构检测函数单元验证 ==")
        assert _looks_like_part_folder(shenlong, TPL) is True, "503026_神龙 应被识别为部件文件夹"
        assert _looks_like_part_folder(heilogwang, TPL) is False, "504004_黑龙王 不应被识别"
        parsed = _parse_folder_or_struct(shenlong, TPL)
        assert parsed == ("503026", None), f"结构兜底应返回 ('503026', None)，实际 {parsed}"
        print("  ✓ 结构检测函数行为正确")

        print("\nALL M15 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
