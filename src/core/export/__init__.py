"""动画导出（M45 阶段 B）：把一串合成好的帧写成 GIF / APNG 文件。

整条流水线（三步，全部不依赖 Qt）：
    ① core/compose.py   build_stack()     → 叠哪些层（LayerStack）
    ② core/render.py    SequenceRender    → 一串对齐好的 PIL 图片
    ③ 本模块            export_frames()   → 裁剪 / 放大 / 铺背景 / 编码成文件

以后要加新格式（PNG 序列、精灵图…）：在本目录新建一个文件写 write_xxx()，
再在 export_frames() 里加一行分发即可，界面代码不用动。

用法示例：
    opts = ExportOptions(fmt="gif", fps=12, background="#1E2023")
    frames = [img for _i, img, _label in SequenceRender(...).frames()]
    info = export_frames(frames, Path("E:/导出/SE_idle.gif"), opts)
    print(info)   # {'frames': 8, 'size': (180, 210), 'frame_ms': 80, 'fps': 12.5}
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# 支持的格式：内部代号 → 文件扩展名
FORMATS = {"gif": "gif", "apng": "png", "webp": "webp"}


@dataclass
class ExportOptions:
    """导出选项：导出弹窗里的每一项都对应这里一个字段。"""
    fmt: str = "gif"                    # "gif" / "apng" / "webp"
    fps: int = 12                       # 目标帧率（GIF 会按 10 毫秒取整，见 frame_ms）
    loop: bool = True                   # True = 无限循环；False = 只播放一次
    background: str | None = "#1E2023"  # 背景色（十六进制）；None = 透明
    alpha_threshold: int = 128          # GIF 透明模式：透明度低于它的像素算全透明
    dither: bool = False                # GIF 减色时是否抖动（像素风一般关掉更干净）
    crop_padding: int | None = 8        # 按内容裁剪后四周留白（像素）；None = 不裁剪（与预览一致）
    scale: int = 1                      # 放大倍数 1 / 2 / 3（最近邻，像素边缘不糊）

    @property
    def ext(self) -> str:
        return FORMATS[self.fmt]


# ---------------------------------------------------------------------
# 帧时长
# ---------------------------------------------------------------------
def frame_ms(fmt: str, fps: int) -> int:
    """每帧显示多少毫秒。

    GIF 格式只能存「百分之一秒」（10 毫秒）为单位的时长：12fps = 83.3ms 只能存成 80ms。
    另外浏览器会把小于 20ms 的 GIF 帧当成 100ms 播（反而变慢），所以最少 20ms。
    APNG 可以精确到毫秒。
    """
    fps = max(1, int(fps))
    if fmt == "gif":
        return max(20, int(round(1000 / fps / 10)) * 10)
    return max(1, int(round(1000 / fps)))


def actual_fps(fmt: str, fps: int) -> float:
    """实际播放帧率（GIF 取整后会和目标略有不同，弹窗里显示给用户看）。"""
    return round(1000 / frame_ms(fmt, fps), 1)


# ---------------------------------------------------------------------
# 帧预处理：裁剪留白 → 放大
# ---------------------------------------------------------------------
def content_bbox(frames: list[Image.Image]) -> tuple[int, int, int, int] | None:
    """全部帧里「有像素的区域」的并集（透明的地方不算）；整串全透明返回 None。

    用并集而不是每帧各自裁：保证所有帧尺寸一样、角色位置不跳。
    """
    boxes = [f.getchannel("A").getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def prepare_frames(frames: list[Image.Image], opts: ExportOptions) -> list[Image.Image]:
    """按选项裁剪、放大，返回 RGBA 帧（背景在各格式的写入函数里处理）。"""
    frames = [f.convert("RGBA") for f in frames]
    if opts.crop_padding is not None:
        box = content_bbox(frames)
        if box is None:
            raise ValueError("整串都是全透明的空帧（占位图），没有可导出的内容")
        p = max(0, int(opts.crop_padding))
        # 裁剪框超出画布的部分，PIL 会自动补透明像素，所以留白可以比画布还大
        box = (box[0] - p, box[1] - p, box[2] + p, box[3] + p)
        frames = [f.crop(box) for f in frames]
    if opts.scale and opts.scale > 1:
        frames = [f.resize((f.width * opts.scale, f.height * opts.scale), Image.NEAREST)
                  for f in frames]
    return frames


def flatten(frames: list[Image.Image], color: str) -> list[Image.Image]:
    """把带透明的帧铺到纯色背景上（返回 RGB）。color 形如 "#1E2023"。"""
    rgb = hex_to_rgb(color)
    out = []
    for f in frames:
        bg = Image.new("RGBA", f.size, rgb + (255,))
        bg.alpha_composite(f)
        out.append(bg.convert("RGB"))
    return out


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


# ---------------------------------------------------------------------
# 文件名
# ---------------------------------------------------------------------
def safe_name(name: str) -> str:
    """把名字里 Windows 文件名不允许的字符换成下划线：\\ / : * ? " < > |"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().rstrip(". ")
    return s or "未命名"


def default_output_dir(root: Path) -> Path:
    """默认导出目录：扫描根目录**旁边**的「{目录名}_导出」。

    不放在扫描目录里面：否则文件监听（M24）会触发重扫，导出的文件还可能被当成资源扫进来。
    """
    return root.parent / f"{root.name}_导出"


def is_inside(path: Path, root: Path) -> bool:
    """path 是否在 root 目录里面（含 root 本身）。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------
def export_frames(frames: list[Image.Image], out_path: Path, opts: ExportOptions) -> dict:
    """把一串帧写成文件，返回导出信息（帧数、尺寸、每帧毫秒、实际帧率）。"""
    if not frames:
        raise ValueError("没有可导出的帧")
    if opts.fmt not in FORMATS:
        raise ValueError(f"不支持的格式：{opts.fmt}")
    frames = prepare_frames(frames, opts)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if opts.fmt == "gif":
        from .gif import write_gif
        write_gif(frames, out_path, opts)
    elif opts.fmt == "apng":
        from .apng import write_apng
        write_apng(frames, out_path, opts)
    else:
        from .webp import write_webp
        write_webp(frames, out_path, opts)
    return {
        "frames": len(frames),
        "size": frames[0].size,
        "frame_ms": frame_ms(opts.fmt, opts.fps),
        "fps": actual_fps(opts.fmt, opts.fps),
    }
