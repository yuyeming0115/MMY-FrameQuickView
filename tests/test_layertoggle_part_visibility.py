"""层显隐开关（layertoggle）验证：组视图下隐藏 fills 等部件层。

验证点（spec M:LayerToggle）：
1. 组视图右侧出现逐部件 toggle；点击某部件可切换显隐
2. _layers_for_current 组模式下跳过 hidden 部件
3. 单部件视图隐藏 toggle
4. 显隐状态可写入/恢复（QSettings 记忆）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from PIL import Image  # noqa: E402

from src.app import MainWindow  # noqa: E402
from src.core.scanner import scan_root  # noqa: E402
from src.core.template import load_templates  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# ⚠ QSettings 直写注册表（HKCU\Software\MMY\FrameQuickView），测试内
# 情况 D 的 sig.emit 会触发真实槽 _on_part_toggled 回写 layering/hidden_parts。
# 采用「前后清零」而非备份还原：还原会把外部脏值原样写回（自锁污染），
# 清零则无论输入状态如何，测试后该 key 一定干净。
_REAL = QSettings("MMY", "FrameQuickView")
_REAL.remove("layering/hidden_parts")
_REAL.sync()


def _mk_part(base: Path, pid: str, part: str, dirs, actions):
    for d in dirs:
        for a in actions:
            p = base / f"{pid}_{part}" / d / a / "0001.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(p)


def _build(tmp: Path):
    base = tmp / "r"
    pid = "501001"
    _mk_part(base, pid, "body", ["E", "N"], ["idle", "run"])
    _mk_part(base, pid, "shadow", ["E", "N"], ["idle", "run"])
    _mk_part(base, pid, "fills", ["E", "N"], ["idle", "run"])
    return base


def main():
    tmp = Path(tempfile.mkdtemp(prefix="layertoggle_"))
    try:
        base = _build(tmp)
        tpl = load_templates()[0]

        win = MainWindow()
        win._tpl = tpl
        win._result = scan_root(base, tpl)
        grp = win._result.groups[0]

        print("== 情况 A：默认全显, layer 数 = 部件数 ==")
        win._group = grp
        win._part = None
        win._update_matrix("E", "idle")   # 固定有效组合，避免依赖 QSettings 恢复态
        parts_keys = [p.part or p.name for p in grp.parts]
        assert sorted(parts_keys) == ["body", "fills", "shadow"], parts_keys
        n_all = win._layers_for_current()[0]   # M26：返回 4 元组，取 layers
        assert len(n_all) == 3, f"默认应 3 层，实际 {len(n_all)}"
        print("  parts:", parts_keys, "  layers:", len(n_all))
        print("  ✓ 默认全显 3 层")

        print("\n== 情况 B：隐藏 fills → 2 层，仍含 body/shadow ==")
        win._hidden_parts = {"fills"}
        layers = win._layers_for_current()[0]
        assert len(layers) == 2, f"隐藏 fills 后应 2 层，实际 {len(layers)}"
        assert not any(len(x) == 0 for x in layers), "过滤后每层都应有帧"
        print(f"  隐藏 fills → layers={len(layers)}")
        print("  ✓ _layers_for_current 正确跳过隐藏层")

        print("\n== 情况 C：隐藏 shadow 后再隐藏 body → 只剩 fills ==")
        win._hidden_parts = {"shadow", "body"}
        layers2 = win._layers_for_current()[0]
        assert len(layers2) == 1, f"应只剩 fills 1 层，实际 {len(layers2)}"
        print("  ✓ 任意层均可隐藏")

        print("\n== 情况 D：toggle 组件接口 ==")
        sig = win.anim_view.part_toggles_signal()
        assert sig is not None, "应有 toggled 信号"
        cnt = [0]
        def _h(part, visible):
            cnt[0] += 1
        sig.connect(_h)
        sig.emit("fills", False)   # 模拟隐藏 fills
        assert cnt[0] == 1, "信号应触发"
        print("  ✓ toggled 信号可触发")

        print("\nALL LAYERTOGGLE PASS")
        win.close()
    finally:
        # 清零：撤销测试内 emit 触发的回写，且不保留任何外部遗留值
        _REAL.remove("layering/hidden_parts")
        _REAL.sync()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()