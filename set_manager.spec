# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Set Manager
# Build: pyinstaller set_manager.spec
#
# Prerequisites:
#   pip install ".[dist]"   (installs pyinstaller)
#
# Output:
#   dist/SetManager          (macOS/Linux one-dir bundle)
#   dist/SetManager.app      (macOS .app via --windowed)

from pathlib import Path

src = Path("src/set_manager")

a = Analysis(
    [str(src / "__main__.py")],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # PySide6 modules pulled in dynamically
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # Service dependencies
        "spotipy",
        "tidalapi",
        "pyrekordbox",
        "thefuzz",
        "platformdirs",
        "pydantic",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SetManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # no terminal window on Windows/macOS
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SetManager",
)

# macOS .app bundle (comment out if not on macOS)
app = BUNDLE(
    coll,
    name="SetManager.app",
    icon=None,
    bundle_identifier="com.setmanager.SetManager",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": "0.1.0",
    },
)
