"""M32 GUI 冒烟：HUD 面板显示/隐藏 + 内容 + 动作按钮角标 + 开关持久化。"""
import os
import re
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
    assert "weapon 7帧" in html or "⚠" in html, "应含帧数不一致标记"
    # M32.1 紧凑模式：默认不渲染完整账目表（完整合计带「N方向 × M动作」）
    assert "方向 ×" not in html, "默认应为紧凑模式"
    hud._set_expanded(True)
    exp = hud._label.text()
    assert "方向 ×" in exp, "悬停应展开完整账目"
    # M32.2：展开视图按方向归组 + 不显示帧号范围
    assert "0001" not in exp and "–" not in exp, "不应显示帧号范围"
    # M32.3：「方向-总帧数」内联（如 E-14帧）+ wordWrap 开启防右侧裁切
    assert re.search(r">[A-Za-z\u4e00-\u9fff]+</span><span[^>]*>-\d+帧</span>", exp), \
        "方向行应内联总帧数"
    assert hud._label.wordWrap(), "QLabel 应开 wordWrap 防裁切"
    hud._set_expanded(False)
    assert "方向 ×" not in hud._label.text(), "移开应收起"
    print("[1] OK HUD 显示: 紧凑默认/悬停展开/收起 + 合计 + 不一致标记")

    # 动作按钮角标：当前方向下 idle 行动按钮应有角标
    btn = w.matrix.act_stack._buttons.get("idle")
    assert btn is not None and btn._badge.isVisible(), "idle 按钮应有帧数角标"
    assert btn._badge.text() == "8", btn._badge.text()
    # M32.2 方向按钮角标：E 方向 = 该方向动作数 1；E 含 mismatch 行 → 红角标（异常优先）
    dbtn = w.matrix.dir_stack._buttons.get("E")
    assert dbtn is not None and dbtn._badge.isVisible(), "E 方向按钮应有动作数角标"
    assert dbtn._badge.text() == "1", dbtn._badge.text()
    assert "#C74444" in dbtn._badge.styleSheet(), "含异常的方向角标应为红色"
    # idle 行自带 mismatch → 动作角标同样红色（异常优先，紫色断言见 [3]）
    assert "#C74444" in btn._badge.styleSheet(), "含异常的动作角标应为红色"
    print("[2] OK 按钮角标: 动作 idle→8（红=行内有不一致），方向 E→1（红）")

    # 切到 weapon 视角（组内部件仍是组视图）→ 改选散件视图验证单部件
    # 单部件：拖入 body 目录本身
    body_dir = outfit / "501521005_body"
    w._on_folder_dropped(body_dir)
    app.processEvents()
    assert hud.isVisible()
    html2 = hud._label.text()
    assert "16 帧" in html2 or "8 帧" in html2, html2[:300]
    # M32.2/3 分色：无异常时方向角标=蓝、动作角标=紫
    dbtn2 = w.matrix.dir_stack._buttons.get("E")
    assert "#4C8FD6" in dbtn2._badge.styleSheet(), "无异常方向角标应为蓝色"
    abtn2 = w.matrix.act_stack._buttons.get("idle")
    assert "#9C6ADE" in abtn2._badge.styleSheet(), "动作角标应为紫色"
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
