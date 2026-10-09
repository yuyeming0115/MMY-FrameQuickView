# -*- coding: utf-8 -*-
"""M39：画布对齐默认开启 + 左栏自适应加宽（默认完整展示名字）。

验证：
1. 全新 QSettings 下启动：画布对齐默认开（display/canvas_align 缺省=True）
2. 拖入目录后左栏按最长条目自适应加宽（只加宽不收窄），完整名字可见
3. 自动重扫不重新加宽（不覆盖用户手动拖窄的宽度）
4. 显式重新拖入 → 再次加宽

运行：python tests/test_m39_ui_defaults.py
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


def _mk_seq(base: Path, n: int = 8):
    for i in range(1, n + 1):
        p = base / f"{i:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def build_tree(root: Path):
    for folder in ("501112101_body", "501112101_shadow", "501111101_hair",
                   "501031001_weapon", "501031001_shadow"):
        _mk_seq(root / folder / "SE" / "idle")
    (root / "匹配表.txt").write_text(
        "501112101\t天命·男〔剑〕时装\n501111101\t天命·男〔剑〕发\n"
        "501031001\t昆仑剑侠〔剑〕武器0阶\n", encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="m39_"))
    try:
        build_tree(tmp)
        app = QApplication.instance() or QApplication([])
        settings = QSettings("MMY", "FrameQuickView")
        snap_keys = ["display/canvas_align", "layout/splitter", "layout/panes",
                     "last/folder", "last/selection", "layering/hidden_parts"]
        saved_qs = {}
        for k in snap_keys:
            saved_qs[k] = settings.value(k)
            settings.remove(k)
        settings.sync()
        from src.app import MainWindow
        win = MainWindow()
        win.show()
        win._tpl.outfit_merge_max = 2
        try:
            print("== 1. 画布对齐默认开 ==")
            assert win._canvas_align is True, "全新 QSettings 下画布对齐应默认开"
            assert win.canvas_btn.isChecked(), "工具栏按钮应同步为选中态"
            print("  ✓ 默认开启")

            print("== 2. 左栏自适应加宽（完整名字） ==")
            win._on_folder_dropped(tmp)
            app.processEvents()
            w_pref = win.part_list.preferred_width()
            left = win._splitter.sizes()[0]
            assert w_pref and w_pref > 220, f"理想宽度应大于最小宽: {w_pref}"
            assert abs(left - w_pref) <= 2, f"左栏应加宽到理想宽度: {left} != {w_pref}"
            assert win.part_list.tree.header().sectionSize(0) >= w_pref - 40, \
                "列宽应同步到容器宽"
            print(f"  ✓ 左栏加宽到 {left}px（理想 {w_pref}px）")

            print("== 3. 自动重扫不覆盖手动拖窄 ==")
            total = sum(win._splitter.sizes())
            win._splitter.setSizes([220, total - 220])
            win._auto_rescan()
            app.processEvents()
            left_after = win._splitter.sizes()[0]
            assert left_after <= 240, f"自动重扫不应重新加宽: {left_after}"
            print(f"  ✓ 拖窄到 220 后重扫保持 {left_after}px")

            print("== 4. 显式重新拖入 → 再次加宽 ==")
            win._on_folder_dropped(tmp)
            app.processEvents()
            left2 = win._splitter.sizes()[0]
            assert abs(left2 - w_pref) <= 2, f"显式拖入应重新加宽: {left2}"
            print(f"  ✓ 重新加宽到 {left2}px")
        finally:
            win.close()
            for k, v in saved_qs.items():
                if v is None:
                    settings.remove(k)
                else:
                    settings.setValue(k, v)
            settings.sync()
        print("\nALL M39 PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
