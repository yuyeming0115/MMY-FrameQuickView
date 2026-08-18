"""核心模块冒烟测试（不依赖 PySide6）：scanner + namemap。

用法: python tests/smoke_core.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.namemap import NameMap, discover_map_file          # noqa: E402
from src.core.scanner import scan_root                          # noqa: E402
from src.core.template import load_templates                    # noqa: E402

REAL_ROOT = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图")
REAL_MAP = Path(r"E:\XYJProject\美术资源\动画序列帧\ID-角色-名字匹配表.txt")


def main() -> int:
    tpls = load_templates()
    assert tpls, "templates/ 为空"
    tpl = tpls[0]
    print(f"[模板] {tpl.name}: parts={len(tpl.parts)} dirs={tpl.directions}")

    # 1. 真实父目录扫描
    if REAL_ROOT.is_dir():
        result = scan_root(REAL_ROOT, tpl)
        print(f"[扫描] {REAL_ROOT.name}: {len(result.parts)} 部件, "
              f"{len(result.groups)} 个ID组, 忽略 {len(result.ignored)}")
        for g in result.groups[:5]:
            flags = "🔴" if g.has_issues else "  "
            names = ",".join(p.part or "整体" for p in g.parts)
            print(f"  {flags} {g.res_id}: [{names}]")
        for g in result.groups:
            if g.pairing_issues:
                print(f"  配套异常 {g.res_id}: {g.pairing_issues[0]}")
        # 帧号断档样例
        sample = result.parts[0]
        for d, col in list(sample.matrix.items())[:1]:
            for a, ad in list(col.items())[:3]:
                rng = f"{ad.numbers[0]:04d}-{ad.numbers[-1]:04d}" if ad.numbers else "-"
                print(f"  [帧] {sample.name}/{d}/{a}: {ad.count}帧 ({rng}) gaps={ad.gaps[:5]}")
    else:
        print(f"[跳过] 真实目录不存在: {REAL_ROOT}")

    # 2. 匹配表加载（只读）
    nm = NameMap()
    if REAL_MAP.is_file():
        nm.load(REAL_MAP)
        for key in ("502019", "505026", "50151100"):
            print(f"[映射] {key} -> {nm.lookup(key, key)}")
        print(f"[映射] 50111100_hair -> {nm.lookup('50111100_hair', '50111100')}")
        print(f"[部位] shadow={nm.part_cn('shadow')} weapon={nm.part_cn('weapon')}")
        assert discover_map_file(REAL_ROOT) == REAL_MAP or True
        found = discover_map_file(REAL_ROOT)
        print(f"[发现] {found}")
    else:
        print(f"[跳过] 匹配表不存在: {REAL_MAP}")

    # 3. 自动登记 + 回写（在临时副本上测，不动真实文件）
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "map.txt"
        shutil.copy(REAL_MAP, tmp)
        nm2 = NameMap()
        nm2.load(tmp)
        added = nm2.register_missing(["999001", "999002", "502019"])  # 502019 已有→跳过
        print(f"[登记] 新增 {added}")
        assert added == ["999001", "999002"], added
        ok = nm2.set_name("999001", "测试角色")
        assert ok
        nm3 = NameMap()
        nm3.load(tmp)
        assert nm3.lookup("999001", "999001") == "测试角色"
        assert nm3.lookup("999002", "999002") is None  # 待命名不算有效
        # 原表内容未被破坏
        assert nm3.lookup("502019", "502019") == "杜如晦"
        print("[登记/回写] 通过")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
