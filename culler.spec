# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Culler.

Build per-platform:
    pyinstaller culler.spec

Output:
    macOS    → dist/Culler.app      (drag-to-Applications bundle)
    Windows  → dist/Culler.exe      (single file)
    Linux    → dist/culler          (single file)
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)
ENTRY = str(ROOT / "run_culler.py")

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

a = Analysis(
    [ENTRY],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="culler",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
        target_arch=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="culler",
    )
    app = BUNDLE(
        coll,
        name="Culler.app",
        icon=None,
        bundle_identifier="dev.culler.app",
        info_plist={
            "CFBundleName": "Culler",
            "CFBundleDisplayName": "Culler",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Culler" if IS_WIN else "culler",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        runtime_tmpdir=None,
    )
