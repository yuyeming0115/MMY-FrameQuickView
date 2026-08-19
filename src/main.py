"""MMY-FrameQuickView 入口。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

try:
    from .app import MainWindow          # python -m src.main
except ImportError:                      # python src/main.py / PyInstaller
    from src.app import MainWindow


def _resolve_icon_path() -> Path:
    """解析图标路径：开发态从项目根 assets/，打包态从 _MEIPASS/assets/。"""
    candidates = [
        Path(__file__).resolve().parent.parent / "assets" / "icon.ico",  # 开发态
        Path(sys._MEIPASS) / "assets" / "icon.ico" if hasattr(sys, "_MEIPASS") else None,  # PyInstaller
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return Path("assets/icon.ico")  # 兜底（QIcon 找不到时不报错）


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MMY-FrameQuickView")
    app.setWindowIcon(QIcon(str(_resolve_icon_path())))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
