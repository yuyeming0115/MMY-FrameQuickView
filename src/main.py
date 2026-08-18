"""MMY-FrameQuickView 入口。"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

try:
    from .app import MainWindow          # python -m src.main
except ImportError:                      # python src/main.py / PyInstaller
    from src.app import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MMY-FrameQuickView")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
