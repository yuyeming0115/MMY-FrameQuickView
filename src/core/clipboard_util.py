"""剪贴板图像复制工具：支持透明通道保留与去透明边缘。

专为游戏美术/概念设计工作流打造：
1. 支持「去透明边缘」：通过 alpha 像素包围盒（getbbox）裁剪掉外围透明留白，
   仅保留角色/特效核心画面，便于在 Photoshop 中快速排版拼接。
2. 完整保留 Alpha 通道：在 Windows 平台双重注册 PNG 剪贴板格式与 Qt MIME 格式，
   确保在 Photoshop、SAI、Clip Studio、微信、QQ 等软件中 Ctrl+V 粘贴为纯净透明图层，
   杜绝黑底或白底问题。
"""
from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import QByteArray, QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    pass


def _write_win32_png_clipboard(png_bytes: bytes) -> bool:
    """在 Windows 原生剪贴板中注册并写入 PNG 格式数据（兼容各版本 Photoshop）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 配置函数签名（防止 64 位指针截断）
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

        user32.OpenClipboard.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        png_fmt = user32.RegisterClipboardFormatW("PNG")
        if not png_fmt:
            return False

        if not user32.OpenClipboard(None):
            return False

        try:
            # GMEM_MOVEABLE (0x0002)
            h_mem = kernel32.GlobalAlloc(0x0002, len(png_bytes))
            if not h_mem:
                return False
            p_mem = kernel32.GlobalLock(h_mem)
            if not p_mem:
                kernel32.GlobalFree(h_mem)
                return False
            ctypes.memmove(p_mem, png_bytes, len(png_bytes))
            kernel32.GlobalUnlock(h_mem)
            # 写入剪贴板（成功则由系统接管句柄所有权，失败必须显式 GlobalFree）
            res = user32.SetClipboardData(png_fmt, h_mem)
            if not res:
                kernel32.GlobalFree(h_mem)
                return False
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def copy_image_to_clipboard(
    image: Image.Image,
    crop_transparent: bool = True,
) -> tuple[bool, str, tuple[int, int]]:
    """将 PIL.Image 复制到系统剪贴板。

    Args:
        image: PIL.Image (RGBA)
        crop_transparent: 是否自动裁剪四周透明像素留白

    Returns:
        (success, msg, (width, height))
    """
    if image is None:
        return False, "无有效图像数据", (0, 0)

    im = image.convert("RGBA")

    # 1. 弃掉透明像素（如果启用且存在有效像素）
    if crop_transparent:
        bbox = im.getbbox()
        if bbox is not None:
            im = im.crop(bbox)
        # 若 bbox 为 None（全透明图），保留原图不抛错

    w, h = im.size
    if w <= 0 or h <= 0:
        return False, "图像尺寸无效", (0, 0)

    # 2. 导出标准 PNG 字节流
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    # 3. Qt 剪贴板写入
    clip = QApplication.clipboard()
    if clip is None:
        return False, "剪贴板不可用", (w, h)

    mime = QMimeData()
    q_bytes = QByteArray(png_bytes)
    mime.setData("image/png", q_bytes)
    mime.setData("PNG", q_bytes)

    # 普通 QImage 兜底供常规文字/富文本编辑器读取
    qimg = QImage.fromData(png_bytes, "PNG")
    if not qimg.isNull():
        mime.setImageData(qimg)

    clip.setMimeData(mime)

    # 4. Windows 原生补充注册 PNG（强化 PS / 绘图软件透明度兼容）
    _write_win32_png_clipboard(png_bytes)

    desc = f"已复制到剪贴板 ({w}×{h}"
    desc += " · 去透明)" if crop_transparent else " · 原尺寸)"
    return True, desc, (w, h)
