# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

project_root = Path(SPECPATH).parents[1]
source_root = project_root / "src"
asset_root = project_root / "build" / "windows-assets"

binaries = []
datas = []
hiddenimports = []
try:
    datas += copy_metadata("python-can")
except Exception:
    pass

for package_name in ("zlgcan",):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package_name)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(project_root / "packaging" / "windows" / "entry.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pyqtgraph.examples",
        "pyqtgraph.opengl",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="BLECalibration",
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
    icon=str(asset_root / "BLECalibration.ico"),
    version=(
        str(asset_root / "version_info.txt")
        if sys.platform == "win32"
        else None
    ),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BLECalibration",
)
