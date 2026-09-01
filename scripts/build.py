#!/usr/bin/env python3
"""MMY-FrameQuickView 打包脚本。

用法：
    .venv/Scripts/python.exe scripts/build.py [onedir|onefile]

输出：
    releases/v{版本}_{时间戳}/MY-FrameQuickView.exe
    releases/v{版本}_{时间戳}/README.txt
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"


def run(cmd: list[str], cwd: Path) -> None:
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 MMY-FrameQuickView")
    parser.add_argument("mode", nargs="?", default="onefile", choices=["onefile", "onedir"])
    args = parser.parse_args()

    # 加时分秒：同一天多次打包不再互相覆盖（旧产物可留存对比）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    release_dir = PROJECT_ROOT / "releases" / f"v{VERSION}_{timestamp}"
    release_dir.mkdir(parents=True, exist_ok=True)

    # 1. 清理旧 dist/build
    for d in (PROJECT_ROOT / "dist", PROJECT_ROOT / "build"):
        if d.exists():
            shutil.rmtree(d)

    # 2. 运行 PyInstaller
    spec = PROJECT_ROOT / "FrameQuickView.spec"
    pyinstaller = sys.executable.replace("python.exe", "pyinstaller.exe")
    if not Path(pyinstaller).exists():
        pyinstaller = sys.executable.replace("python.exe", "Scripts\\pyinstaller.exe")
    run([pyinstaller, str(spec), "--clean", "--noconfirm"], PROJECT_ROOT)

    # 3. 复制产物 + README
    exe_src = PROJECT_ROOT / "dist" / "MMY-FrameQuickView.exe"
    if args.mode == "onedir":
        exe_src = PROJECT_ROOT / "dist" / "MMY-FrameQuickView" / "MMY-FrameQuickView.exe"
    if not exe_src.exists():
        print(f"错误：未找到产物 {exe_src}", file=sys.stderr)
        return 1

    shutil.copy2(exe_src, release_dir / "MMY-FrameQuickView.exe")

    readme = release_dir / "README.txt"
    readme.write_text(
        f"""MMY-FrameQuickView v{VERSION}
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

使用方式：
1. 双击 MMY-FrameQuickView.exe 启动
2. 把「角色资源父目录」或单个「部件文件夹」拖入上方拖拽区
3. 左栏按 ID 分组，点击组头查看同 ID 叠层，点击部件查看单部件
4. B 区下方切换方向 / 动作，播放 GIF 动画，勾选棋盘格检查 alpha 毛边
5. 顶栏「模板」可切换规则；「编辑/新建」可调整模板

模板文件保存在程序同目录 templates/ 下，可直接复制或修改。
""",
        encoding="utf-8",
    )

    print(f"打包完成：{release_dir}")
    print(f"  - {release_dir / 'MMY-FrameQuickView.exe'}")
    print(f"  - {readme}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
