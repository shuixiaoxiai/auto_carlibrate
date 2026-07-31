"""Launch the built EXE in offscreen Mock modes and verify artifacts."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import subprocess
import tempfile
from pathlib import Path


def run_and_require_screenshot(command, screenshot: Path, timeout: float) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        env=environment,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"bundle smoke command failed with exit code {completed.returncode}"
        )
    if not screenshot.exists() or screenshot.stat().st_size < 10_000:
        raise RuntimeError(f"bundle did not create a valid screenshot: {screenshot}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    executable = args.exe.resolve()
    if not executable.exists():
        raise FileNotFoundError(executable)

    output_context = (
        tempfile.TemporaryDirectory()
        if args.output_dir is None
        else nullcontext(str(args.output_dir.resolve()))
    )
    with output_context as temp_dir:
        root = Path(temp_dir)
        root.mkdir(parents=True, exist_ok=True)
        analysis_screenshot = root / "analysis.png"
        analysis_report = root / "analysis.json"
        for stale_artifact in (analysis_screenshot, analysis_report):
            if stale_artifact.exists():
                stale_artifact.unlink()
        run_and_require_screenshot(
            [
                str(executable),
                "gui",
                "--automation-report",
                str(analysis_report),
                "--screenshot",
                str(analysis_screenshot),
            ],
            analysis_screenshot,
            args.timeout,
        )
        report = json.loads(analysis_report.read_text(encoding="utf-8"))
        if report["direction_count"] != 8:
            raise RuntimeError("packaged UI did not recompute all eight directions")
        if report["refresh_ms"] is None or report["refresh_ms"] >= 200.0:
            raise RuntimeError(
                f"packaged UI What-if refresh exceeded 200 ms: {report['refresh_ms']}"
            )
        untriggered_summary = {
            "total": 8,
            "excellent": 0,
            "good": 0,
            "poor": 8,
            "untriggered": 8,
        }
        if report["lock_summary"] != untriggered_summary:
            raise RuntimeError(
                "packaged UI quality summary did not follow the What-if result: "
                f"{report['lock_summary']}"
            )
        if report["unlock_summary"] != untriggered_summary:
            raise RuntimeError(
                "packaged UI unlock summary did not follow the What-if result: "
                f"{report['unlock_summary']}"
            )
        if (
            report["summary_widgets"]["lock_poor"] != "差 8"
            or report["summary_widgets"]["unlock_poor"] != "差 8"
        ):
            raise RuntimeError(
                "packaged summary widgets did not display the recomputed poor counts"
            )
        print(f"Packaged What-if report: {analysis_report}")

        manual_screenshot = root / "manual.png"
        if manual_screenshot.exists():
            manual_screenshot.unlink()
        run_and_require_screenshot(
            [
                str(executable),
                "gui",
                "--manual-mock",
                "--database",
                str(root / "projects.sqlite3"),
                "--parameters-hidden",
                "--screenshot",
                str(manual_screenshot),
            ],
            manual_screenshot,
            args.timeout,
        )
        print(f"Packaged manual workspace screenshot: {manual_screenshot}")

    print(
        "Packaged EXE smoke test passed: "
        f"{executable} (8 directions and quality summaries recomputed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
