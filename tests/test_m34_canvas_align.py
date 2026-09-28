# -*- coding: utf-8 -*-
"""M34：固定画布对齐模式——跨视图角色位置纹丝不动。

背景：默认模式 =「内容并集 bbox + 普通层组中心居中」双重内容自适应——
武器把窗口撑大、把锚点中心拉偏，导致「有武器 vs 无武器」跨视图比对时
角色视觉位置漂移。

画布对齐模式（canvas_align=True）：
- 窗口 = 原始渲染画布 (0, 0, W, H)，W/H = 普通层帧尺寸取 max
- 普通层按原始画布坐标放置（shift=0，不平移）
- 特效层保持「层并集居中 + 用户微调」不变

运行：python tests/test_m34_canvas_align.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from src.ui.worker import DecodeWorker

app = QApplication.instance() or QApplication([])

CANVAS = (200, 200)                 # 渲染画布尺寸
BODY_BOX = (80, 60, 120, 140)       # 角色身体位于画布中部
WEAPON_BOX = (20, 100, 70, 130)     # 武器向左下伸出
BODY_COLOR = (120, 200, 90, 255)
WEAPON_COLOR = (220, 180, 60, 255)


def make_png(path: Path, box: tuple, color: tuple, canvas: tuple = CANVAS) -> None:
    """在画布 range box 内画不透明矩形（帧内容 bbox = box，画布=canvas）。"""
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=color)
    img.save(path)


def decode(layers, canvas_align: bool = False, flat_mask=None) -> list[Image.Image]:
    """同步执行 DecodeWorker.run()（不起事件循环），收集 frame_ready 返回 PIL 帧列表。"""
    out: list[Image.Image] = []
    w = DecodeWorker(layers, flat_mask=flat_mask, canvas_align=canvas_align)
    w.frame_ready.connect(lambda i, img, label: out.append(img))
    w.run()
    return out


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    a = np.asarray(img)[..., 3]
    ys, xs = np.nonzero(a)
    assert len(xs) > 0, "输出帧不应为全透明"
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


tmp = Path(tempfile.mkdtemp())
try:
    # 同一画布：身体居中、武器左下；两帧内容微错位模拟动画
    body1 = tmp / "b1.png"
    body2 = tmp / "b2.png"
    weapon1 = tmp / "w1.png"
    weapon2 = tmp / "w2.png"
    make_png(body1, BODY_BOX, BODY_COLOR)
    make_png(body2, (84, 64, 116, 136), BODY_COLOR)
    make_png(weapon1, WEAPON_BOX, WEAPON_COLOR)
    make_png(weapon2, (24, 104, 66, 126), WEAPON_COLOR)

    # ---- [1] 画布模式 · 单 body：输出 = 整块画布，内容在原始坐标 ----
    out1 = decode([[body1, body2]], canvas_align=True)
    assert len(out1) == 2, f"[1] 应输出 2 帧，实际 {len(out1)}"
    assert out1[0].size == CANVAS, \
        f"[1] 输出应为整块画布 {CANVAS}，实际 {out1[0].size}"
    assert alpha_bbox(out1[0]) == BODY_BOX, \
        f"[1] 内容应在原始画布坐标 {BODY_BOX}，实际 {alpha_bbox(out1[0])}"
    print(f"[1] OK 画布模式单 body：{out1[0].size}，内容在原始坐标")

    # ---- [2] 核心断言：画布模式 body+weapon 两层，body 像素与单 body 视图完全一致 ----
    out2 = decode([[body1, body2], [weapon1, weapon2]], canvas_align=True)
    assert out2[0].size == CANVAS, f"[2] 输出应为整块画布，实际 {out2[0].size}"
    b1 = np.asarray(out1[0].crop(BODY_BOX))
    b2 = np.asarray(out2[0].crop(BODY_BOX))
    assert not (b1 != b2).any(), \
        "[2] 画布模式 body 像素应跨视图完全一致（武器不带动身体）"
    assert alpha_bbox(out2[0]) == (20, 60, 120, 140), \
        f"[2] body∪weapon 应为原始坐标并集 (20,60,120,140)，实际 {alpha_bbox(out2[0])}"
    print("[2] OK 画布模式跨视图：body 像素位置完全一致（核心断言）")

    # ---- [3] 特效层画布模式仍居中（纯特效组退化路径）----
    fx = tmp / "fx.png"
    make_png(fx, (10, 10, 50, 50), (90, 140, 240, 255))
    out3 = decode([[fx]], canvas_align=True, flat_mask=[True])
    assert out3[0].size == CANVAS, f"[3] 纯特效组输出应仍为画布尺寸，实际 {out3[0].size}"
    assert alpha_bbox(out3[0]) == (80, 80, 120, 120), \
        f"[3] 特效层应仍居中 (80,80)-(120,120)，实际 {alpha_bbox(out3[0])}"
    print("[3] OK 特效层画布模式仍居中")

    # ---- [4] 默认模式行为回归不变（并集 bbox + 居中）----
    ref1 = decode([[body1, body2]])                       # 默认：单 body
    ref2 = decode([[body1, body2], [weapon1, weapon2]])   # 默认：带武器
    assert ref1[0].size == (40, 80), \
        f"[4] 默认模式单 body 应按并集 bbox 裁剪 (40, 80)，实际 {ref1[0].size}"
    assert ref2[0].size == (100, 80), \
        f"[4] 默认模式带武器应为并集 bbox (100, 80)，实际 {ref2[0].size}"
    assert ref1[0].size != ref2[0].size, \
        "[4] 默认模式跨视图尺寸不同（漂移来源——正是画布模式的价值）"
    print("[4] OK 默认模式回归：并集 bbox 行为不变")

    print("ALL M34 PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
