"""M21 冒烟：分类规则 + 扁平特效扫描（真实目录 + 临时断帧目录）。"""
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.scanner import scan_root
from core.template import load_templates

tpl = load_templates()[0]

# 1) 真实特效目录（E 盘）
fx = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图\50105101")
if fx.exists():
    r = scan_root(fx, tpl)
    assert r.parts and r.parts[0].is_flat, "特效应识别为扁平资源"
    p = r.parts[0]
    acts = p.matrix.get("特效", {})
    assert "changrao" in acts, f"应有 changrao 序列: {list(acts)}"
    ad = acts["changrao"]
    assert ad.count == 20 and ad.continuous, f"20 帧连续: {ad.count}, gaps={ad.gaps}"
    g = r.groups[0]
    assert g.category == "特效" and g.is_flat
    assert not g.has_issues, "完整序列不应有缺漏"
    print(f"[1] OK 真实特效: {p.res_id} 序列={list(acts)} {ad.count}帧 连续")

# 2) 临时目录：断帧 + 分类覆盖（坐骑/翅膀/主角/伙伴/怪物）
tmp = Path(tempfile.mkdtemp())
try:
    (tmp / "50105999").mkdir()
    for i in [1, 2, 3, 5, 6]:   # 缺 4
        (tmp / "50105999" / f"aote_{i}.png").write_bytes(b"x")
    r = scan_root(tmp, tpl)
    g = r.groups[0]
    assert g.category == "特效" and g.is_flat
    assert g.has_issues, "断帧应标红"
    ad = g.parts[0].matrix["特效"]["aote"]
    assert ad.gaps == [4], f"缺帧 4: {ad.gaps}"
    print(f"[2] OK 断帧检测: gaps={ad.gaps} has_issues={g.has_issues}")

    cats = []
    for name, parts in [
        ("50106101", ["wings", "shadow"]),
        ("50309901", ["ride_front", "ride_back"]),
        ("50122101", ["body", "shadow"]),
        ("502099", []),
        ("5030421", []),
        ("504099", []),
        ("505099", []),
    ]:
        d = tmp / (name if not parts else f"{name}_{parts[0]}")
        d.mkdir()
        for extra in parts[1:]:
            (tmp / f"{name}_{extra}").mkdir()
        # 每个（壳）文件夹下造一个方向/动作帧，保证被扫描收集
        for sub in ([d] + [tmp / f"{name}_{e}" for e in parts[1:]]):
            (sub / "E" / "idle").mkdir(parents=True)
            (sub / "E" / "idle" / "0001.png").write_bytes(b"x")
    r = scan_root(tmp, tpl)
    got = {g.res_id: g.category for g in r.groups}
    assert got.get("50106101") == "翅膀", got
    assert got.get("50309901") == "坐骑", got
    assert got.get("50122101") == "主角", got
    assert got.get("502099") == "伙伴", got
    assert got.get("5030421") == "怪物", got
    assert got.get("504099") == "BOSS", got
    assert got.get("505099") == "NPC", got
    assert got.get("50105999") == "特效", got
    print(f"[3] OK 分类: {got}")

    # 4) 常规部件带 _part 后缀不做扁平兜底
    d = tmp / "50105888_body"
    d.mkdir()
    (d / "qita_1.png").write_bytes(b"x")
    r = scan_root(tmp / "50105888_body", tpl)
    assert not r.parts, "带部件后缀的文件夹不应走扁平兜底"
    print("[4] OK 部件后缀不误判")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS")
