"""TexturePacker 图集解析与单帧抽取（Cocos2d-x / Format 3）。

支持 local-role 等平铺目录下的 .plist + .png 图集资源：
1. 解析 plist 元数据（帧尺寸、偏移量、旋转、原始画布尺寸）。
2. AtlasFrame 兼容 Path 鸭子类型（提供 name, stem, is_file 等），可无缝作为帧引用。
3. 坐标逆变换：根据 offset 与 sourceSize 精确还原到原画布位置，确保叠层与动画不抖动。
4. LRU 纹理缓存：避免多帧重复加载同一张图集 PNG。
"""
from __future__ import annotations

import plistlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    BBox = tuple[int, int, int, int]


def _parse_int_tuple(s: str) -> list[int]:
    """解析 '{x, y}' 或 '{{x, y}, {w, h}}' 为整数列表。"""
    try:
        cleaned = str(s).replace("{", "").replace("}", "")
        return [int(round(float(part.strip()))) for part in cleaned.split(",") if part.strip()]
    except (ValueError, TypeError, AttributeError):
        return []


@dataclass(frozen=True)
class AtlasFrame:
    """图集单帧引用，鸭子类型兼容 Path。"""
    plist_path: Path
    png_path: Path
    frame_key: str                            # 如 "0001.png"
    stem: str                                 # 如 "0001"
    name: str                                 # 如 "0001.png"
    rect: tuple[int, int, int, int]           # (x, y, w, h) 在图集中的逻辑区域
    rotated: bool                             # 是否顺时针打包旋转
    offset: tuple[int, int]                   # (ox, oy) 相对原画布中心的偏移
    source_size: tuple[int, int]              # (src_w, src_h) 原始画布尺寸
    canvas_bbox: tuple[int, int, int, int] | None  # 还原到原画布坐标的 bbox；全透明为 None
    canvas_size: tuple[int, int]              # 原画布尺寸 (src_w, src_h)

    def is_file(self) -> bool:
        return self.png_path.is_file()

    def __fspath__(self) -> str:
        return str(self.png_path)

    def __str__(self) -> str:
        return f"{self.plist_path}#{self.frame_key}"

    def get_cropped_image(self, atlas_img: Image.Image | None = None) -> Image.Image:
        """从图集提取该帧裁剪好的有效图像（已处理旋转）。"""
        if atlas_img is None:
            atlas_img = get_atlas_texture(self.png_path)

        x, y, w, h = self.rect
        if self.rotated:
            # 顺时针旋转打入图集，物理区域宽为 h，高为 w；裁切后逆时针旋转 90 度还原
            crop_box = (x, y, x + h, y + w)
            sub = atlas_img.crop(crop_box)
            return sub.transpose(Image.ROTATE_90)
        else:
            crop_box = (x, y, x + w, y + h)
            return atlas_img.crop(crop_box)


# ---------------------------------------------------------------------------
# 全局缓存与服务函数
# ---------------------------------------------------------------------------

_MAX_TEXTURE_CACHE = 40
_texture_cache: OrderedDict[Path, Image.Image] = OrderedDict()
_plist_cache: dict[Path, list[AtlasFrame]] = {}


def get_atlas_texture(png_path: Path) -> Image.Image:
    """获取图集纹理 PIL.Image（带 LRU 缓存）。"""
    png_path = Path(png_path)
    if png_path in _texture_cache:
        _texture_cache.move_to_end(png_path)
        return _texture_cache[png_path]

    im = Image.open(png_path).convert("RGBA")
    _texture_cache[png_path] = im
    if len(_texture_cache) > _MAX_TEXTURE_CACHE:
        _texture_cache.popitem(last=False)
    return im


def clear_atlas_cache() -> None:
    """清空图集纹理与 plist 缓存。"""
    _texture_cache.clear()
    _plist_cache.clear()


def parse_plist_frames(plist_path: Path) -> list[AtlasFrame]:
    """解析 Cocos2d-x Format 3 plist 文件，返回有序的 AtlasFrame 列表。"""
    plist_path = Path(plist_path)
    if plist_path in _plist_cache:
        return _plist_cache[plist_path]

    png_path = plist_path.with_suffix(".png")
    frames: list[AtlasFrame] = []

    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
    except Exception:
        return []

    raw_frames = data.get("frames", {})
    if not isinstance(raw_frames, dict):
        return []

    # 按帧名（如 0001.png, 0002.png）自然排序
    def _sort_key(item: tuple[str, dict]) -> tuple[int, str]:
        m = re.search(r"(\d+)", item[0])
        return (int(m.group(1)) if m else 0, item[0])

    sorted_items = sorted(raw_frames.items(), key=_sort_key)

    for frame_key, info in sorted_items:
        if not isinstance(info, dict):
            continue

        rect_nums = _parse_int_tuple(str(info.get("textureRect", "")))
        if len(rect_nums) == 4:
            rect = (rect_nums[0], rect_nums[1], rect_nums[2], rect_nums[3])
        else:
            rect = (0, 0, 0, 0)

        offset_nums = _parse_int_tuple(str(info.get("spriteOffset", "")))
        ox = offset_nums[0] if len(offset_nums) >= 1 else 0
        oy = offset_nums[1] if len(offset_nums) >= 2 else 0

        src_nums = _parse_int_tuple(str(info.get("spriteSourceSize", "")))
        src_w = src_nums[0] if len(src_nums) >= 1 else 0
        src_h = src_nums[1] if len(src_nums) >= 2 else 0

        rotated = bool(info.get("textureRotated", False))

        # 原始画布坐标计算（纯 plist 元数据数学计算，零图像解码开销）
        w, h = rect[2], rect[3]
        if w == 0 or h == 0:
            canvas_bbox = None
        else:
            orig_l = round((src_w - w) / 2) + ox
            orig_t = round((src_h - h) / 2) - oy
            canvas_bbox = (orig_l, orig_t, orig_l + w, orig_t + h)

        stem = Path(frame_key).stem
        frames.append(AtlasFrame(
            plist_path=plist_path,
            png_path=png_path,
            frame_key=frame_key,
            stem=stem,
            name=frame_key,
            rect=rect,
            rotated=rotated,
            offset=(ox, oy),
            source_size=(src_w, src_h),
            canvas_bbox=canvas_bbox,
            canvas_size=(src_w, src_h),
        ))

    _plist_cache[plist_path] = frames
    return frames


# ---------------------------------------------------------------------------
# 文件名正则与探测
# ---------------------------------------------------------------------------

# 规则1：常规方向/动作图集，如 501031001_shadow_E_attack.plist 或 505026_NW_idle.plist
_ATLAS_ACTION_RX = re.compile(
    r"^([0-9]+)(?:_([a-zA-Z0-9_一-龥\(\)（）]+?))?_(E|N|NW|S|SE)_([a-zA-Z0-9_]+)\.(plist|png)$",
    re.IGNORECASE,
)

# 规则2：扁平特效图集，如 50105101.plist（ID 通常 ≥ 5 位）
_ATLAS_FLAT_RX = re.compile(
    r"^([0-9]{5,})\.(plist|png)$",
    re.IGNORECASE,
)


def parse_atlas_filename(name: str) -> tuple[str, str | None, str | None, str | None, str] | None:
    """解析图集文件名。

    返回: (res_id, part, direction, action, ext) 或 None
    对于扁平特效，direction 与 action 返回 None。
    """
    m = _ATLAS_ACTION_RX.match(name)
    if m:
        res_id, part, direction, action, ext = m.groups()
        return res_id, part, direction, action, ext.lower()

    m2 = _ATLAS_FLAT_RX.match(name)
    if m2:
        res_id, ext = m2.groups()
        return res_id, None, None, None, ext.lower()

    return None


def is_atlas_root(folder: Path) -> bool:
    """探测 folder 是否为 TexturePacker 平铺图集目录。

    判定依据：根目录下直接包含匹配图集命名规则的 .plist 文件。
    """
    try:
        folder = Path(folder)
        if not folder.is_dir():
            return False
        # 只要直接子文件中找到若干个匹配图集命名的 plist 文件即可快速确认
        count = 0
        for entry in folder.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".plist":
                if parse_atlas_filename(entry.name) is not None:
                    count += 1
                    if count >= 2:  # 命中至少 2 个即确认为平铺图集目录
                        return True
        return False
    except OSError:
        return False
