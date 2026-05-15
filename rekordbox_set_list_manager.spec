# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Rekordbox Set List Manager
# Build: uv run pyinstaller rekordbox_set_list_manager.spec   (or: make dist)
#
# Prerequisites (install once):
#   uv sync --extra dist
#
# Output (all platforms produce a one-dir bundle):
#   dist/RekordboxSetListManager/          (Windows / Linux one-dir)
#   dist/RekordboxSetListManager.app       (macOS .app bundle)

import re
import sys
from pathlib import Path

src = Path("src/rekordbox_set_list_manager")

# ── Single-source version: read directly from __init__.py ──────────────────
_ver_text = (src / "__init__.py").read_text(encoding="utf-8")
_ver_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', _ver_text, re.M)
if _ver_match is None:
    raise RuntimeError(
        "Could not parse __version__ from src/rekordbox_set_list_manager/__init__.py"
    )
VERSION = _ver_match.group(1)

# ── Collect resource directories (icons/, styles/) ─────────────────────────
def _resource_datas() -> list[tuple[str, str]]:
    """Return (src, dest) pairs for every file under gui/resources/."""
    resources_dir = src / "gui" / "resources"
    pairs = []
    for f in resources_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src.parent)   # relative to src/
            dest = str(rel.parent)
            pairs.append((str(f), dest))
    return pairs

a = Analysis(
    [str(src / "__main__.py")],
    pathex=["src"],
    binaries=[],
    datas=_resource_datas(),
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "spotipy",
        "tidalapi",
        "pyrekordbox",
        "thefuzz",
        "platformdirs",
        "pydantic",
        "pydantic.v1",
        "sqlcipher3",
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
    name="RekordboxSetListManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,
    uac_admin=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RekordboxSetListManager",
)

# ── macOS .app bundle (skipped automatically on Windows / Linux) ───────────
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="RekordboxSetListManager.app",
        icon=None,
        bundle_identifier="com.rekordboxsetlistmanager.RekordboxSetListManager",
        info_plist={
            "CFBundleName": "Rekordbox Set List Manager",
            "CFBundleDisplayName": "Rekordbox Set List Manager",
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "RekordboxSetListManager",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "LSMinimumSystemVersion": "12.0",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Rekordbox Set List Manager Project",
                    "CFBundleTypeExtensions": ["setmgr"],
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "LSItemContentTypes": ["com.rekordboxsetlistmanager.setmgr"],
                }
            ],
            "UTExportedTypeDeclarations": [
                {
                    "UTTypeIdentifier": "com.rekordboxsetlistmanager.setmgr",
                    "UTTypeDescription": "Rekordbox Set List Manager Project",
                    "UTTypeConformsTo": ["public.data", "public.content"],
                    "UTTypeTagSpecification": {"public.filename-extension": ["setmgr"]},
                }
            ],
        },
    )
