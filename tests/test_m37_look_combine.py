# -*- coding: utf-8 -*-
"""M37：跨 ID 形象组合预览 + fills 检测默认关闭。

背景：501112101_body / 501111101_hair / 501031001_weapon 是**不同 ID** 的部件，
靠匹配表中文名的公共前缀（如「天命·男〔剑〕」）关联为同一形象。选中任一组时，
显示层自动带出同形象其他组部件（影子仍最底），可逐个关掉。

验证：
1. Template.look_split：类型词切分、边界（词在开头/无命中）
2. look_types 持久化（to_dict/from_dict 往返）
3. 组视图：选中时装组 → 显示层 5 部件（身体影子/身体/头发/武器影子/武器），
   A/B 区 5 层，状态栏「👤 形象组合 +3 部件」
4. 关掉「头发」→ 4 层；重新打开恢复
5. 无同前缀组（逍遥仙武器）→ 行为与现状一致、无提示
6. 无类型词名字（冰火双翼）不参与配对
7. 全新 QSettings 下 fills 检测默认关

运行：python tests/test_m37_look_combine.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.core.scanner import scan_root
from src.core.template import Template, load_templates

TPL = next(t for t in load_templates() if t.name == "天命装")
TPL.outfit_merge_max = 2        # 小型测试树：禁用 M23 套装合并，验证按形象配对

LOOK_NAMES = {
    "501112101": "天命·男〔剑〕时装",
    "501111101": "天命·男〔剑〕发",
    "501031001": "天命·男〔剑〕武器",
    "501031003": "逍遥仙〔扇〕武器",
    "50104101": "冰火双翼",
}

MAP_TEXT = "\n".join(f"{rid}\t{name}" for rid, name in LOOK_NAMES.items()) + "\n"


def _mk_seq(base: Path, n: int = 8):
    for i in range(1, n + 1):
        p = base / f"{i:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def build_tree(root: Path):
    for d in ("SE", "NW"):                       # 两方向，模拟真实结构
        for folder in ("501112101_body", "501112101_shadow", "501111101_hair",
                       "501031001_weapon", "501031001_shadow", "501031003_weapon",
                       "50104101_wings"):
            _mk_seq(root / folder / d / "idle")
    (root / "匹配表.txt").write_text(MAP_TEXT, encoding="utf-8")


def main():
    # ---- 1/2：look_split 单元 + 模板持久化 ----
    print("== 1. look_split 解析 ==")
    assert TPL.look_split("天命·男〔剑〕时装") == ("天命·男〔剑〕", "时装")
    assert TPL.look_split("天命·男〔剑〕发") == ("天命·男〔剑〕", "发")
    assert TPL.look_split("昆仑剑侠〔剑〕武器0阶") == ("昆仑剑侠〔剑〕", "武器")
    assert TPL.look_split("逍遥仙〔扇〕武器")[0] == "逍遥仙〔扇〕"
    assert TPL.look_split("冰火双翼") is None, "无类型词不配对"
    assert TPL.look_split("武器") is None, "类型词在开头=前缀为空，不配对"
    print("  ✓ 类型词切分与边界正确")

    print("== 2. look_types 持久化 ==")
    rt = Template.from_dict(TPL.to_dict())
    assert rt.look_types == TPL.look_types
    old = Template.from_dict({k: v for k, v in TPL.to_dict().items() if k != "look_types"})
    assert old.look_types == [], "旧模板缺字段 → 功能关闭（行为不变）"
    print("  ✓ to_dict/from_dict 往返 + 旧模板兼容")

    tmp = Path(tempfile.mkdtemp(prefix="m37_"))
    try:
        build_tree(tmp)
        res = scan_root(tmp, TPL)
        assert not res.ignored, f"不应有忽略项: {res.ignored}"
        assert not any(g.is_outfit for g in res.groups), "小型树不应误判套装（阈值=2）"

        print("== 3~5. 组视图形象组合（offscreen） ==")
        app = QApplication.instance() or QApplication([])
        settings = QSettings("MMY", "FrameQuickView")
        snap_keys = ["last/folder", "last/selection", "checks/fills",
                     "display/layer_panel", "layering/hidden_parts"]
        saved_qs = {k: settings.value(k) for k in snap_keys}
        settings.remove("layering/hidden_parts")     # 从确定态开始
        settings.remove("checks/fills")              # 验证默认值
        settings.sync()
        from src.app import MainWindow
        win = MainWindow()
        win.show()
        # 小型测试树须禁用 M23 套装合并——app 用自己的模板实例，这里同步改它
        win._tpl.outfit_merge_max = 2
        try:
            assert win._fills_check is False, "全新 QSettings 下 fills 检测应默认关"
            win._on_folder_dropped(tmp)
            app.processEvents()

            # 选中「时装」组（body+shadow）→ 形象组合出 头发/武器影子/武器
            win.part_list._select_by_key("GRP:501112101")
            _settle(app, win)
            display = win._display_parts(win._group)
            assert [p.part for p in display] == ["shadow", "shadow", "body", "hair", "weapon"], \
                f"显示部件应按 layer_order 排列: {[p.part for p in display]}"
            items = win.anim_view._toggles._items
            assert len(items) == 5, f"显示层应 5 个 chips: {len(items)}"
            labels = [b.text().replace("☑ ", "").replace("☐ ", "") for _, b in items]
            # 稳定排序：本体组影子在前，同形象组按组序（501031001 → 501111101）追加
            assert labels == ["身体影子", "武器影子", "身体", "头发", "武器"], f"chips 文案: {labels}"
            assert "👤 形象组合 +3 部件" in win.statusBar().currentMessage()
            layers, _fm, _off, _keys = win._layers_for_current()
            assert len(layers) == 5 and all(len(l) == 8 for l in layers), \
                f"A/B 区应 5 层×8帧: {[len(l) for l in layers]}"
            print("  ✓ 时装组 → 5 部件组合叠显（影子最底），状态栏提示 +3")

            # 关掉「头发」→ 4 层；恢复 → 5 层
            win._on_part_toggled("hair", False)
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 4, f"关掉头发后应 4 层: {len(layers)}"
            win._on_part_toggled("hair", True)
            _settle(app, win)
            layers, *_ = win._layers_for_current()
            assert len(layers) == 5, "重新打开头发应恢复 5 层"
            print("  ✓ chips 逐个开关生效")

            # 逍遥仙武器：无同前缀组 → 无配对、无提示
            win.part_list._select_by_key("GRP:501031003")
            _settle(app, win)
            assert len(win._display_parts(win._group)) == 1
            assert "形象组合" not in win.statusBar().currentMessage()
            print("  ✓ 无同形象组时行为与现状一致")

            # 冰火双翼（无类型词）不参与任何配对
            wing_grp = next(g for g in win._result.groups if g.res_id == "50104101")
            assert win._look_split(wing_grp) is None
            print("  ✓ 无类型词名字不参与配对")
        finally:
            win.close()
            for k, v in saved_qs.items():
                if v is None:
                    settings.remove(k)
                else:
                    settings.setValue(k, v)
            settings.sync()
        print("\nALL M37 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _settle(app, win):
    """处理事件队列 + 等后台线程（解码/空帧抽样）投递完成。"""
    import time
    for _ in range(60):
        app.processEvents()
        wg, wa = win.grid_view._worker, win.anim_view._worker
        wb = [w for w in win._blank_workers if w.isRunning()]
        if (wg is None or not wg.isRunning()) and (wa is None or not wa.isRunning()) and not wb:
            break
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


if __name__ == "__main__":
    main()
