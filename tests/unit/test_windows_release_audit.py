import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "packaging"
    / "windows"
    / "audit_build_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("audit_build_manifest", SCRIPT_PATH)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit_module)


def passing_manifest():
    artifact = {"size_bytes": 1, "sha256": "a" * 64}
    return {
        "schema": "ble-calibration-build-manifest/v1",
        "source_revision": "f" * 40,
        "platform": "Windows-11-10.0.26100-SP0",
        "machine": "AMD64",
        "python_bits": 64,
        "include_zlgcan": True,
        "source_tests_run": True,
        "package_versions": {"python-can": "4.6.1", "zlgcan": "0.3.0"},
        "artifacts": {"onedir_exe": artifact, "archive": artifact},
        "native_drivers": [artifact],
        "acceptance_results": {
            "analysis.json": {
                "direction_count": 8,
                "refresh_ms": 20.0,
                "lock_summary": {"total": 8, "poor": 8},
                "unlock_summary": {"total": 8, "poor": 8},
            },
            "source-ui.json": {
                "ok": True,
                "direction_count": 8,
                "record_count": 24,
                "curve_count": 40,
                "threshold_core_recompute_ms": 100.27,
                "strategy_core_recompute_ms": 120.0,
                "threshold_debounce_to_painted_ms": 160.502,
                "strategy_debounce_to_painted_ms": 206.035,
                "cloud_codec_round_trip": True,
                "one_click_restore": True,
                "group_switch_and_mean": True,
            },
            "manual-workflow.json": {
                "ok": True,
                "operator_start_finish_directions": 8,
                "lock_distance_inputs": 8,
                "unlock_distance_inputs": 8,
                "project_reopened": True,
            },
            "live-workflow.json": {
                "ok": True,
                "direction_count": 8,
                "single_device_connection": True,
                "raw_direction_files": 8,
            },
            "optimization-workflow.json": {
                "ok": True,
                "can_apply": True,
                "lock_rate_percent": 75.0,
                "unlock_rate_percent": 87.5,
                "lock_poor": 0,
                "unlock_poor": 0,
                "ordering_violations": 0,
                "near_unlock_violations": 0,
                "minimum_gap_db": 5,
                "applied_to_what_if": True,
                "vehicle_write": False,
            },
            "zlg-bundle.json": {
                "ok": True,
                "zlgcan_version": "0.3.0",
                "native_drivers": ["zlgcan_driver.pyd"],
            },
        },
    }


class WindowsReleaseAuditTests(unittest.TestCase):
    def test_complete_windows_manifest_passes(self) -> None:
        manifest = passing_manifest()
        audit = audit_module.audit_manifest(
            manifest,
            require_windows=True,
            require_zlgcan=True,
            require_source_tests=True,
            expected_revision="f" * 40,
        )
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["failures"], [])

    def test_slow_grouped_refresh_and_missing_distance_fail(self) -> None:
        manifest = passing_manifest()
        manifest["acceptance_results"]["source-ui.json"][
            "strategy_debounce_to_painted_ms"
        ] = 1000.0
        manifest["acceptance_results"]["source-ui.json"][
            "threshold_core_recompute_ms"
        ] = 600.0
        manifest["acceptance_results"]["manual-workflow.json"][
            "unlock_distance_inputs"
        ] = 7
        audit = audit_module.audit_manifest(
            manifest,
            require_windows=True,
            require_zlgcan=True,
            require_source_tests=True,
        )
        self.assertFalse(audit["ok"])
        self.assertIn(
            "strategy_debounce_to_painted_ms is not below 1000 ms",
            audit["failures"],
        )
        self.assertIn(
            "threshold_core_recompute_ms is not below 600 ms",
            audit["failures"],
        )
        self.assertIn("unlock distances incomplete", audit["failures"])

    def test_unsafe_automatic_recommendation_fails_release(self) -> None:
        manifest = passing_manifest()
        optimization = manifest["acceptance_results"][
            "optimization-workflow.json"
        ]
        optimization["near_unlock_violations"] = 1
        optimization["minimum_gap_db"] = 2

        audit = audit_module.audit_manifest(
            manifest,
            require_windows=True,
            require_zlgcan=True,
            require_source_tests=True,
        )

        self.assertFalse(audit["ok"])
        self.assertIn(
            "automatic recommendation introduces unlock below 1m",
            audit["failures"],
        )
        self.assertIn(
            "automatic recommendation threshold gap is below 3 dB",
            audit["failures"],
        )


if __name__ == "__main__":
    unittest.main()
