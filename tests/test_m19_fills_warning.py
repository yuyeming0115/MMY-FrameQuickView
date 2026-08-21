"""M19：fills 部件警告级（橙色）缺失检测验证。

旧工程可能漏掉 fills 填充部件。要求：
1. fills 参与查漏，但缺失记入 warning_*（橙色），不进红色 missing_
2. 组内存在主体部件但整组无 fills → grp.missing_fills（缺的方向）
3. fills 存在但某些方向/动作缺失 → part.warning_directions/warning_actions，且 has_issues 为 False
4. 按键矩阵/红点不受影响（fills 不进 missing_）
"""
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.scanner import scan_root
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


def _mk_part(base: Path, pid: str, part: str, dirs: list[str], actions):
    for d in dirs:
        for a in actions:
            _mk_png(base / f"{pid}_{part}" / d / a / "0001.png")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m19_"))
    try:
        base = tmp / "根部_" / "角色A"
        pid = "503111"
        # 主体：body 5 方向 idle/run
        _mk_part(base, pid, "body", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        # shadow 5 方向 idle/run
        _mk_part(base, pid, "shadow", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        # 无 fills → 组级 missing_fills 应显示

        print("== 情况 A：组内无 fills ==")
        res = scan_root(base, TPL)
        grp = res.groups[0]
        assert grp.missing_fills, f"组应缺 fills，实际 {grp.missing_fills}"
        assert grp.has_warnings, "组应有警告级 has_warnings"
        assert grp.has_issues is False or not grp.pairing_issues, "仅缺 fills 不应触发红色 issues"
        print(f"  missing_fills={grp.missing_fills}")
        parts = sorted(p.part for p in grp.parts)
        assert "fills" not in parts, "组不应包含 fills"
        print("  ✓ 组级缺 fills 正确识别为警告级")

        print("\n== 情况 B：fills 存在但缺方向/动作 ==")
        base2 = tmp / "根部_" / "角色B"
        _mk_part(base2, "503222", "body", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        _mk_part(base2, "503222", "shadow", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        # fills 只有 E/N 两方向
        _mk_part(base2, "503222", "fills", ["E", "N"], ["idle", "run"])
        res2 = scan_root(base2, TPL)
        grp2 = res2.groups[0]
        fills_parts = [p for p in grp2.parts if p.part == "fills"]
        assert len(fills_parts) == 1, "应包含 1 个 fills 部件"
        fp = fills_parts[0]
        assert not fp.has_issues, "fills 缺失不应触发红色 issues"
        assert fp.has_warnings, "fills 应有警告级 has_warnings"
        missing_dirs = set(fp.warning_directions)
        assert "NW" in missing_dirs and "S" in missing_dirs, f"应警告缺 NW/S，实际 {missing_dirs}"
        assert not fp.missing_directions, "红色 missing_directions 应为空"
        assert not fp.missing_actions, "红色 missing_actions 应为空"
        print(f"  fills warning_directions={fp.warning_directions}")
        print(f"  fills warning_actions={fp.warning_actions}")
        print("  ✓ fills 缺方向转为警告级，不进红色")

        print("\n== 情况 C：fills 齐全无警示 ==")
        base3 = tmp / "根部_" / "角色C"
        _mk_part(base3, "503333", "body", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        _mk_part(base3, "503333", "fills", ["E", "N", "NW", "S", "SE"], ["idle", "run"])
        res3 = scan_root(base3, TPL)
        grp3 = res3.groups[0]
        assert not grp3.missing_fills, "fills 齐全不应有 missing_fills"
        assert not any(p.has_warnings for p in grp3.parts), "fills 齐全不应有警告"
        print("  ✓ fills 齐全无警示")

        print("\nALL M19 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()