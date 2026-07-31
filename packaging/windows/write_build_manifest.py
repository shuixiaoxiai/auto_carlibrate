"""Write checksums and environment evidence for one Windows build."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import struct
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, Optional

from ble_calibration.version import __version__


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path) -> Dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _package_versions(names: Iterable[str]) -> Dict[str, Optional[str]]:
    versions: Dict[str, Optional[str]] = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def build_manifest(
    *,
    onedir_exe: Path,
    archive: Path,
    installer: Optional[Path],
    acceptance_dir: Path,
    include_zlgcan: bool,
) -> Dict[str, object]:
    onedir_exe = onedir_exe.resolve()
    onedir_root = onedir_exe.parent
    driver_paths = sorted(
        path
        for path in onedir_root.rglob("*clgcan_driver*.pyd")
        if path.is_file()
    )
    if include_zlgcan and not driver_paths:
        raise RuntimeError(
            "include_zlgcan was requested but clgcan_driver.pyd is absent"
        )

    required_acceptance = (
        "bundle-can.blf",
        "bundle-can.manifest.json",
        "analysis.json",
        "analysis.png",
        "manual.png",
        "live-zlg.png",
    )
    if include_zlgcan:
        required_acceptance += ("zlg-bundle.json",)
    acceptance = {}
    for name in required_acceptance:
        path = acceptance_dir / name
        acceptance[name] = _artifact(path)

    artifacts = {
        "onedir_exe": _artifact(onedir_exe),
        "archive": _artifact(archive),
    }
    if installer is not None:
        artifacts["installer"] = _artifact(installer)

    return {
        "schema": "ble-calibration-build-manifest/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "application_version": __version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_bits": struct.calcsize("P") * 8,
        "include_zlgcan": include_zlgcan,
        "package_versions": _package_versions(
            ("PyInstaller", "PySide6", "pyqtgraph", "python-can", "zlgcan")
        ),
        "artifacts": artifacts,
        "native_drivers": [_artifact(path) for path in driver_paths],
        "acceptance": acceptance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onedir-exe", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--acceptance-dir", type=Path, required=True)
    parser.add_argument("--include-zlgcan", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(
        onedir_exe=args.onedir_exe,
        archive=args.archive,
        installer=args.installer,
        acceptance_dir=args.acceptance_dir,
        include_zlgcan=args.include_zlgcan,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Build manifest written: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
