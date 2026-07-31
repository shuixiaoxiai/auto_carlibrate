"""Audit a Windows build manifest against the release requirements."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _source_revision(project_root: Path) -> Optional[str]:
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
    if revision_path.exists():
        revision = revision_path.read_text(encoding="utf-8").strip()
        if revision and "$Format" not in revision:
            return revision
    return None


def audit_manifest(
    manifest: Mapping[str, object],
    *,
    require_windows: bool,
    require_zlgcan: bool,
    require_source_tests: bool,
    expected_revision: Optional[str] = None,
) -> Dict[str, object]:
    failures: List[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(
        manifest.get("schema") == "ble-calibration-build-manifest/v1",
        "unexpected build manifest schema",
    )
    require(manifest.get("python_bits") == 64, "build Python is not 64-bit")
    platform_text = str(manifest.get("platform", ""))
    machine_text = str(manifest.get("machine", "")).lower()
    if require_windows:
        require("windows" in platform_text.lower(), "build platform is not Windows")
        require(
            machine_text in ("amd64", "x86_64"),
            f"build machine is not x64: {machine_text}",
        )

    revision = manifest.get("source_revision")
    require(isinstance(revision, str) and len(revision) == 40, "source revision missing")
    if expected_revision is not None:
        require(revision == expected_revision, "source revision does not match archive")

    versions = manifest.get("package_versions", {})
    require(
        isinstance(versions, Mapping)
        and versions.get("python-can") == "4.6.1",
        "python-can version is not 4.6.1",
    )
    if require_zlgcan:
        require(manifest.get("include_zlgcan") is True, "ZLG bundle not requested")
        require(
            isinstance(versions, Mapping) and versions.get("zlgcan") == "0.3.0",
            "zlgcan version is not 0.3.0",
        )
        require(bool(manifest.get("native_drivers")), "native ZLG driver missing")
    if require_source_tests:
        require(manifest.get("source_tests_run") is True, "source tests were skipped")

    artifacts = manifest.get("artifacts", {})
    require(isinstance(artifacts, Mapping), "artifact table missing")
    if isinstance(artifacts, Mapping):
        for name in ("onedir_exe", "archive"):
            item = artifacts.get(name, {})
            require(isinstance(item, Mapping), f"{name} artifact missing")
            if isinstance(item, Mapping):
                require(
                    bool(SHA256_PATTERN.fullmatch(str(item.get("sha256", "")))),
                    f"{name} SHA-256 invalid",
                )
                require(int(item.get("size_bytes", 0)) > 0, f"{name} is empty")

    results = manifest.get("acceptance_results", {})
    require(isinstance(results, Mapping), "embedded acceptance results missing")
    if not isinstance(results, Mapping):
        results = {}

    analysis = results.get("analysis.json", {})
    require(analysis.get("direction_count") == 8, "packaged UI does not have 8 directions")
    require(
        isinstance(analysis.get("refresh_ms"), (int, float))
        and analysis["refresh_ms"] < 200,
        "packaged What-if refresh is not below 200 ms",
    )
    for event_name in ("lock_summary", "unlock_summary"):
        summary = analysis.get(event_name, {})
        require(summary.get("total") == 8, f"{event_name} total is not 8")
        require(summary.get("poor") == 8, f"{event_name} did not recompute")

    if require_source_tests:
        source_ui = results.get("source-ui.json", {})
        require(source_ui.get("ok") is True, "source UI acceptance failed")
        require(source_ui.get("direction_count") == 8, "source UI direction count is not 8")
        require(source_ui.get("curve_count") == 40, "source UI curve count is not 40")
        for key in (
            "threshold_debounce_to_painted_ms",
            "strategy_debounce_to_painted_ms",
        ):
            value = source_ui.get(key)
            require(
                isinstance(value, (int, float)) and value < 200,
                f"{key} is not below 200 ms",
            )
        require(source_ui.get("cloud_codec_round_trip") is True, "cloud codec failed")
        require(source_ui.get("one_click_restore") is True, "one-click restore failed")

        manual = results.get("manual-workflow.json", {})
        require(manual.get("ok") is True, "manual workflow acceptance failed")
        require(
            manual.get("operator_start_finish_directions") == 8,
            "manual start/finish did not cover 8 directions",
        )
        require(manual.get("lock_distance_inputs") == 8, "lock distances incomplete")
        require(manual.get("unlock_distance_inputs") == 8, "unlock distances incomplete")
        require(manual.get("project_reopened") is True, "saved project did not reopen")

        live = results.get("live-workflow.json", {})
        require(live.get("ok") is True, "live workflow acceptance failed")
        require(live.get("direction_count") == 8, "live workflow direction count is not 8")
        require(live.get("single_device_connection") is True, "live source reconnected per direction")
        require(live.get("raw_direction_files") == 8, "live raw files are incomplete")

    if require_zlgcan:
        zlg = results.get("zlg-bundle.json", {})
        require(zlg.get("ok") is True, "frozen ZLG backend check failed")
        require(zlg.get("zlgcan_version") == "0.3.0", "frozen zlgcan version mismatch")
        require(bool(zlg.get("native_drivers")), "frozen ZLG driver not discoverable")

    return {
        "schema": "ble-calibration-release-audit/v1",
        "ok": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "source_revision": revision,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-windows", action="store_true")
    parser.add_argument("--require-zlgcan", action="store_true")
    parser.add_argument("--require-source-tests", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[2]
    audit = audit_manifest(
        manifest,
        require_windows=args.require_windows,
        require_zlgcan=args.require_zlgcan,
        require_source_tests=args.require_source_tests,
        expected_revision=_source_revision(project_root),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not audit["ok"]:
        for failure in audit["failures"]:
            print(f"FAILED: {failure}")
        return 1
    print(f"Release manifest audit passed: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
