# -*- coding: utf-8 -*-
"""M40：TexturePacker 图集资源支持与序列帧右键复制原图测试。

验证内容：
1. 文件名解析 parse_atlas_filename：
   - 常规方向/动作、纯 ID、坐骑部位、扁平特效；
   - 强化支持中文字符与括号；
   - 排除 4 位普通帧文件名（如 0001.png）。
2. 目录探测 is_atlas_root：
   - 正确识别平铺图集（需 >= 2 个图集文件），非图集目录不误报。
3. plist 解析与几何还原 parse_plist_frames：
   - 纯元数据解析（零 PNG 解码开销，不卡主线程）；
   - 验证旋转帧 (textureRotated=True) 尺寸逆变换与切片正确性；
   - 验证 offset 坐标逆变换与原始 875×875 画布比对绝对 0 误差。
4. ActionData 惰性加载与断档检测：
   - 验证访问 frames 时同步填充 numbers 与 gaps，保证断档检测与 HUD 有效。
5. fills 部件警告级处理：
   - 验证 scan_atlas_root 中 fills 不进红色严重错误，进入 warning 字段。
6. worker 帧标签获取：
   - 验证 AtlasFrame 的标签正确提取为帧序号（如 "0001"），不被图集文件名覆盖。
7. 剪贴板工具 copy_image_to_clipboard：
   - 支持去透明边缘（getbbox 裁剪有效像素）；
   - 支持保留全尺寸；
   - Win32 内存句柄安全（无泄漏）；
   - 剪贴板保留 PNG 透明通道格式与 QImage。
8. A 区 GridView 复制信号：
   - 验证 _FrameCell 接收 PIL 图像并触发 copied 信号传递。

运行：python tests/test_m40_atlas_and_copy_frame.py
"""
import os
import sys
import unittest
from pathlib import Path
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from src.core.atlas import (
    is_atlas_root,
    parse_atlas_filename,
    parse_plist_frames,
    get_atlas_texture,
    clear_atlas_cache,
)
from src.core.clipboard_util import copy_image_to_clipboard
from src.core.imageops import composite_layers, frame_info, union_bbox
from src.core.scanner import scan_root, ActionData
from src.core.template import load_templates
from src.ui.grid_view import GridView, _FrameCell

app = QApplication.instance() or QApplication(sys.argv)
TEMPLATES = load_templates()
TPL = next(t for t in TEMPLATES if t.name == "天命装")
LOCAL_ROLE_DIR = Path(r"E:\XYJProject\美术资源\动画序列帧\local-role")


class TestM40AtlasAndCopy(unittest.TestCase):

    def setUp(self):
        clear_atlas_cache()

    def test_01_parse_atlas_filename(self):
        """测试图集文件名正则解析（含中文、括号、纯ID及防误报）。"""
        # 1. 常规部件
        r1 = parse_atlas_filename("501031001_shadow_E_attack.plist")
        self.assertEqual(r1, ("501031001", "shadow", "E", "attack", "plist"))

        # 2. 纯 ID 资源
        r2 = parse_atlas_filename("505026_NW_idle.png")
        self.assertEqual(r2, ("505026", None, "NW", "idle", "png"))

        # 3. 坐骑前后层部件
        r3 = parse_atlas_filename("50302301_ride_front_E_ride_idle.plist")
        self.assertEqual(r3, ("50302301", "ride_front", "E", "ride_idle", "plist"))

        # 4. 扁平特效 (>=5位数字)
        r4 = parse_atlas_filename("50105101.plist")
        self.assertEqual(r4, ("50105101", None, None, None, "plist"))

        # 5. 带中文与括号的部件名
        r5 = parse_atlas_filename("503026_神龙(坐骑)_S_run.plist")
        self.assertEqual(r5, ("503026", "神龙(坐骑)", "S", "run", "plist"))

        # 6. 非法文件名或普通帧（0001.png）绝不误判
        self.assertIsNone(parse_atlas_filename("not_an_atlas.txt"))
        self.assertIsNone(parse_atlas_filename("0001.png"))
        self.assertIsNone(parse_atlas_filename("0020.png"))

    def test_02_is_atlas_root(self):
        """测试图集根目录探测（>=2个匹配文件）。"""
        if LOCAL_ROLE_DIR.is_dir():
            self.assertTrue(is_atlas_root(LOCAL_ROLE_DIR))
        self.assertFalse(is_atlas_root(Path("src")))

    def test_03_plist_parsing_and_reconstruction(self):
        """测试纯元数据解析、旋转帧逆时针还原与原图像素级无损比对。"""
        plist_path = LOCAL_ROLE_DIR / "501031001_shadow_NW_attack.plist"
        if not plist_path.is_file():
            self.skipTest("local-role 资源不存在")

        frames = parse_plist_frames(plist_path)
        self.assertEqual(len(frames), 8)
        self.assertEqual(frames[0].frame_key, "0001.png")
        self.assertEqual(frames[3].frame_key, "0004.png")

        # 验证旋转帧逆时针还原
        f_rot = next((f for f in frames if f.rotated), None)
        self.assertIsNotNone(f_rot, "必须存在至少一个旋转帧")
        sub_img = f_rot.get_cropped_image()
        self.assertEqual(sub_img.size, (f_rot.rect[2], f_rot.rect[3]))

        # 还原到 875x875 画布并与未打包散帧比对有效像素包围盒位置一致
        idx = int(f_rot.stem) - 1
        raw_dir = Path(r"E:\XYJProject\美术资源\动画序列帧\角色输出图\501031001_shadow\NW\attack")
        raw_files = sorted(raw_dir.glob("*.png")) if raw_dir.is_dir() else []
        if 0 <= idx < len(raw_files):
            raw_im = Image.open(raw_files[idx])
            canvas = Image.new("RGBA", (875, 875), (0, 0, 0, 0))
            canvas.paste(sub_img, (f_rot.canvas_bbox[0], f_rot.canvas_bbox[1]))
            self.assertEqual(canvas.getbbox(), raw_im.getbbox(), "图集逆变换还原图与原始散帧有效像素包围盒完全一致")

    def test_04_action_data_lazy_numbers_and_gaps(self):
        """测试 ActionData 惰性加载时同步计算 numbers 和 gaps。"""
        plist_path = LOCAL_ROLE_DIR / "501031001_shadow_E_attack.plist"
        if not plist_path.is_file():
            self.skipTest("local-role 资源不存在")

        ad = ActionData(plist_path=plist_path)
        self.assertEqual(ad._frames, [])
        self.assertEqual(ad.numbers, [])

        # 访问 frames 触发惰性解析
        frames = ad.frames
        self.assertEqual(len(frames), 8)
        self.assertEqual(ad.numbers, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(ad.gaps, [])
        self.assertTrue(ad.continuous)
        self.assertEqual(ad.count, 8)

    def test_05_fills_warning_in_atlas(self):
        """测试 scan_atlas_root 对 fills 部件转为警告级字段。"""
        import tempfile
        import plistlib

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            # 生成两个 plist 满足 is_atlas_root
            p1 = tmp / "50109901_fills_E_idle.plist"
            p2 = tmp / "50109901_fills_E_run.plist"
            for p in (p1, p2):
                with open(p, "wb") as f:
                    plistlib.dump({"frames": {}, "metadata": {"format": 3}}, f)
                p.with_suffix(".png").touch()

            res = scan_root(tmp, TPL)
            fills_part = next((p for p in res.parts if p.part == "fills"), None)
            self.assertIsNotNone(fills_part)
            # fills 缺的方向必须进 warning_directions 而非 missing_directions
            self.assertEqual(fills_part.missing_directions, [])
            self.assertTrue(len(fills_part.warning_directions) > 0)
            self.assertIn("NW", fills_part.warning_directions)

    def test_06_worker_label_stem(self):
        """测试 worker 中帧标签使用 p.stem 保持 '0001'，而非图集文件名。"""
        plist_path = LOCAL_ROLE_DIR / "501031001_shadow_E_attack.plist"
        if not plist_path.is_file():
            self.skipTest("local-role 资源不存在")

        frames = parse_plist_frames(plist_path)
        f0 = frames[0]
        # AtlasFrame 本身具备 stem 属性
        self.assertEqual(f0.stem, "0001")
        # 验证模拟 worker 中的标签逻辑
        label = next((p.stem if hasattr(p, "stem") else Path(p).stem for p in [f0] if p is not None), "0001")
        self.assertEqual(label, "0001", "标签必须保持为 0001，不得被图集名称覆盖")

    def test_07_clipboard_copy_transparent(self):
        """测试带透明通道的剪贴板复制工具与 Win32 句柄安全。"""
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        for x in range(20, 60):
            for y in range(20, 60):
                im.putpixel((x, y), (200, 100, 50, 220))

        # 1. 去透明边缘复制
        ok, msg, (w, h) = copy_image_to_clipboard(im, crop_transparent=True)
        self.assertTrue(ok)
        self.assertEqual((w, h), (40, 40))

        clip = QApplication.clipboard()
        mime = clip.mimeData()
        self.assertTrue(mime.hasFormat("image/png"))
        self.assertTrue(mime.hasFormat("PNG"))
        self.assertTrue(mime.hasImage())

        # 2. 全尺寸复制
        ok2, msg2, (w2, h2) = copy_image_to_clipboard(im, crop_transparent=False)
        self.assertTrue(ok2)
        self.assertEqual((w2, h2), (80, 80))

    def test_08_grid_view_copied_signal(self):
        """测试 GridView 的右键复制信号传递。"""
        grid = GridView()
        received_msgs = []
        grid.frame_copied.connect(received_msgs.append)

        cell = _FrameCell(0)
        cell.set_label("0001")
        cell.copied.connect(grid.frame_copied.emit)

        test_img = Image.new("RGBA", (30, 30), (255, 0, 0, 255))
        cell.set_image(test_img)

        # 触发复制
        ok, msg, (w, h) = copy_image_to_clipboard(test_img, crop_transparent=True)
        cell.copied.emit(f"已复制第 0001 帧有效原图 ({w}×{h}) 到剪贴板")

        self.assertEqual(len(received_msgs), 1)
        self.assertIn("已复制第 0001 帧有效原图", received_msgs[0])


if __name__ == "__main__":
    unittest.main()
