"""M32 GUI 冒烟：HUD 面板显示/隐藏 + 内容 + 动作按钮角标 + 开关持久化。"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication([])

from src.app import MainWindow
from src.core.template import load_templates

tpl = load_templates()[0]


def make_part(base: Path, name: str, dirs=("E",), acts=("idle",), n=8):
    d = base / name
    for dr in dirs:
        for a in acts:
            (d / dr / a).mkdir(parents=True)
            for i in range(1, n + 1):
                (d / dr / a / f"{i:04d}.png").write_bytes(b"x")
    return d


tmp = Path(tempfile.mkdtemp())
try:
    # 套装：body 8帧 + weapon 7帧（不一致） → HUD 应显示 ⚠
    outfit = tmp / "501521005_女侠客_部件"
    make_part(outfit, "501031005_weapon", n=7)
    make_part(outfit, "501521005_body", n=8)

    w = MainWindow()
    w.show()                      # offscreen 下不 show 则子控件 isVisible 恒 False
    w._on_folder_dropped(outfit)
    app.processEvents()

    # 显式选中套装组（自动选中第一项是部件视图）
    w._on_group_selected(str(outfit))
    app.processEvents()

    hud = w.anim_view._hud
    assert hud.isVisible(), "HUD 默认应可见"
    html = hud._label.text()
    # 标题随匹配表可能为中文名，这里只断言结构性内容
    assert "套装 · 2件" in html, html[:200]
    assert "合计" in html and "8 帧" in html
    assert "weapon 7帧" in html, "应含帧数不一致标记"
    print("[1] OK HUD 显示: 标题/套装/合计/不一致标记")

    # 动作按钮角标：当前方向下 idle 行动按钮应有角标
    btn = w.matrix.act_stack._buttons.get("idle")
    assert btn is not None and btn._badge.isVisible(), "idle 按钮应有帧数角标"
    assert btn._badge.text() == "8", btn._badge.text()
    print("[2] OK 动作按钮角标: idle → 8（主件口径）")

    # 切到 weapon 视角（组内部件仍是组视图）→ 改选散件视图验证单部件
    # 单部件：拖入 body 目录本身
    body_dir = outfit / "501521005_body"
    w._on_folder_dropped(body_dir)
    app.processEvents()
    assert hud.isVisible()
    html2 = hud._label.text()
    assert "16 帧" in html2 or "8 帧" in html2, html2[:300]
    print("[3] OK 单部件 HUD: body 8帧/组合 合计 16 帧(2动作时)")

    # 关闭开关 → 隐藏；信号发出且 QSettings 已写
    fired = []
    w.anim_view.hud_toggled.connect(lambda v: fired.append(v))
    w.anim_view._hud_btn.setChecked(False)
    app.processEvents()
    assert not hud.isVisible() and fired == [False]
    assert w._settings.value("hud/visible", True, type=bool) is False
    # 恢复开启
    w.anim_view._hud_btn.setChecked(True)
    app.processEvents()
    assert hud.isVisible() and fired == [False, True]
    print("[4] OK 开关联动: 隐藏/显示 + 信号 + QSettings")

    print("\nALL PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
