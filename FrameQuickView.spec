# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MMY-FrameQuickView.

Usage:
    .venv/Scripts/pyinstaller.exe FrameQuickView.spec --clean

Output:
    dist/MY-FrameQuickView.exe
"""
import sys
from pathlib import Path

project_root = Path(SPECPATH).resolve()
templates_dir = project_root / "templates"
src_dir = project_root / "src"

# 自动收集 src 下所有子模块，避免 hidden import 遗漏
hidden = []
for py in src_dir.rglob("*.py"):
    rel = py.relative_to(project_root)
    mod = str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
    if mod.endswith("__init__"):
        mod = mod[:-9]
    if mod:
        hidden.append(mod)

block_cipher = None

a = Analysis(
    [str(project_root / "src" / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(templates_dir), "templates")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MY-FrameQuickView",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
