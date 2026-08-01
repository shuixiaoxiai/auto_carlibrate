"""Create a revision-stamped source-only ZIP for Windows handoff."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_archive(output_dir: Path, revision: str = "HEAD") -> Path:
    full_revision = _git("rev-parse", revision)
    short_revision = full_revision[:7]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / (
        f"BLECalibration-windows-source-{short_revision}.zip"
    )

    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--output={output}",
            full_revision,
            "--",
            ".",
            ":(exclude)dist-local",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    with zipfile.ZipFile(output) as archive:
        revision_entries = [
            name for name in archive.namelist() if name == "SOURCE_REVISION.txt"
        ]
        if len(revision_entries) != 1:
            raise RuntimeError(
                "source archive must contain exactly one SOURCE_REVISION.txt"
            )
        archived_revision = archive.read("SOURCE_REVISION.txt").decode().strip()
        if archived_revision != full_revision:
            raise RuntimeError(
                f"archive revision {archived_revision!r} does not match "
                f"Git revision {full_revision!r}"
            )
        if any(name == "dist-local" or name.startswith("dist-local/") for name in archive.namelist()):
            raise RuntimeError("source archive unexpectedly contains dist-local")

    print(f"Archive: {output}")
    print(f"Revision: {full_revision}")
    print(f"SHA-256: {_sha256(output)}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist-local",
    )
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    create_archive(args.output_dir.resolve(), args.revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
