# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

project_root = Path(SPECPATH).parents[1]
source_root = project_root / "src"
asset_root = project_root / "build" / "windows-assets"

binaries = []
datas = []
hiddenimports = [
    "can",
    "can.io",
    "can.io.blf",
]
try:
    datas += copy_metadata("python-can")
except Exception:
    pass

# Fully collect numpy because PyInstaller 6.11's hook does not include every
# numpy 2.5 pure-Python module, including numpy._core._exceptions.
try:
    np_datas, np_binaries, np_hidden = collect_all("numpy")
    # Exclude test modules to avoid roughly 4.6 MB and 495 unnecessary files.
    np_datas = [
        data
        for data in np_datas
        if "tests" not in os.path.normpath(data[0]).split(os.sep)
    ]
    np_hidden = [
        module
        for module in np_hidden
        if not (".tests." in module or module.endswith(".tests"))
    ]
    datas += np_datas
    binaries += np_binaries
    hiddenimports += np_hidden
except Exception as error:
    raise RuntimeError("numpy could not be collected") from error

if os.environ.get("BLE_CALIBRATION_INCLUDE_ZLGCAN") == "1":
    try:
        package_datas, package_binaries, package_hidden = collect_all("zlgcan")
        package_datas += copy_metadata("zlgcan")
    except Exception as error:
        raise RuntimeError(
            "ZLG packaging requested but zlgcan could not be collected"
        ) from error
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
