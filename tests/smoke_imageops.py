"""imageops 冒烟测试（不依赖 PySide6）：bbox 计算 + 叠层合成。

纯生成测试帧，验证：
- 单层 bbox 正确
- 并集 bbox 正确
- 多层按序 alpha 合成（上层覆盖下层）
- None 层跳过（该处透明）
- 单帧合成尺寸 = 自身 bbox 尺寸
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image

from src.core import imageops


def _save(arr: np.ndarray, path: Path) -> Path:
    Image.fromarray(arr, "RGBA").save(path)
    return path


def main() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="mmy_io_"))

    # 帧A：红方块占原图 (10,10)-(20,20)
    a = np.zeros((40, 40, 4), np.uint8)
    a[10:20, 10:20] = (255, 0, 0, 255)
    # 帧B：绿方块占原图 (15,15)-(25,25)
    b = np.zeros((40, 40, 4), np.uint8)
    b[15:25, 15:25] = (0, 255, 0, 255)
    pa = _save(a, tmp / "a.png")
    pb = _save(b, tmp / "b.png")

    # bbox
    ba = imageops.frame_bbox(pa)
    bb = imageops.frame_bbox(pb)
    assert ba == (10, 10, 20, 20), ba
    assert bb == (15, 15, 25, 25), bb

    # union（画布窗口）
    ub = imageops.union_bbox([ba, bb])
    assert ub == (10, 10, 25, 25), ub

    # 多层合成 [A(底), B(上)]：画布坐标 = 原图 - ub左上
    img = imageops.composite_layers([pa, pb], ub)
    arr = np.asarray(img)
    # 绿：原图(15:25)->canvas(5:15)
    assert tuple(arr[7, 7]) == (0, 255, 0, 255), arr[7, 7]
    # 红：原图(10:20)->canvas(0:10)
    assert tuple(arr[2, 2]) == (255, 0, 0, 255), arr[2, 2]
    # 交叠区(原图17,17->canvas7,7) 应为绿（B 在上覆盖）
    assert tuple(arr[7, 7]) == (0, 255, 0, 255)

    # None 层跳过：canvas(12,12)=原图(22,22) 在 A 外、B 设 None → 透明
    img2 = imageops.composite_layers([pa, None], ub)
    arr2 = np.asarray(img2)
    assert tuple(arr2[2, 2]) == (255, 0, 0, 255), arr2[2, 2]
    assert tuple(arr2[12, 12]) == (0, 0, 0, 0), arr2[12, 12]

    # 单帧自身 bbox 合成尺寸
    img3 = imageops.composite_layers([pa], ba)
    assert img3.size == (10, 10), img3.size

    print("IMAGEOPS SMOKE OK")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
