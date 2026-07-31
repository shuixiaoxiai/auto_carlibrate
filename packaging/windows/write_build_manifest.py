"""Write checksums and environment evidence for one Windows build."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import struct
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _application_version(project_root: Path = PROJECT_ROOT) -> str:
    version_path = project_root / "src" / "ble_calibration" / "version.py"
    text = version_path.read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    if match is None:
        raise RuntimeError(f"cannot read application version from {version_path}")
    return match.group(1)


def _source_revision(project_root: Path = PROJECT_ROOT) -> Optional[str]:
    if (project_root / ".git").exists():
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        revision = completed.stdout.strip()
        if completed.returncode == 0 and revision:
            return revision
    revision_path = project_root / "SOURCE_REVISION.txt"
    if not revision_path.exists():
        return None
    revision = revision_path.read_text(encoding="utf-8").strip()
    if not revision or "$Format" in revision:
        return None
    return revision


def build_manifest(
    *,
    onedir_exe: Path,
    archive: Path,
    installer: Optional[Path],
    acceptance_dir: Path,
    include_zlgcan: bool,
    source_tests_run: bool,
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
    if source_tests_run:
        required_acceptance += (
            "source-ui.json",
            "manual-workflow.json",
            "live-workflow.json",
        )
    acceptance = {}
    for name in required_acceptance:
        path = acceptance_dir / name
        acceptance[name] = _artifact(path)
    result_names = (
        "analysis.json",
        "source-ui.json",
        "manual-workflow.json",
        "live-workflow.json",
        "zlg-bundle.json",
    )
    acceptance_results = {}
    for name in result_names:
        path = acceptance_dir / name
        if name in acceptance:
            acceptance_results[name] = json.loads(
                path.read_text(encoding="utf-8")
            )

    artifacts = {
        "onedir_exe": _artifact(onedir_exe),
        "archive": _artifact(archive),
    }
    if installer is not None:
        artifacts["installer"] = _artifact(installer)

    return {
        "schema": "ble-calibration-build-manifest/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "application_version": _application_version(),
        "source_revision": _source_revision(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_bits": struct.calcsize("P") * 8,
        "include_zlgcan": include_zlgcan,
        "source_tests_run": source_tests_run,
        "package_versions": _package_versions(
            ("PyInstaller", "PySide6", "pyqtgraph", "python-can", "zlgcan")
        ),
        "artifacts": artifacts,
        "native_drivers": [_artifact(path) for path in driver_paths],
        "acceptance": acceptance,
        "acceptance_results": acceptance_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onedir-exe", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--acceptance-dir", type=Path, required=True)
    parser.add_argument("--include-zlgcan", action="store_true")
    parser.add_argument("--source-tests-run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(
        onedir_exe=args.onedir_exe,
        archive=args.archive,
        installer=args.installer,
        acceptance_dir=args.acceptance_dir,
        include_zlgcan=args.include_zlgcan,
        source_tests_run=args.source_tests_run,
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
