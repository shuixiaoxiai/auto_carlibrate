import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "packaging"
    / "windows"
    / "write_build_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("write_build_manifest", SCRIPT_PATH)
build_manifest_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_manifest_module)


class WindowsBuildManifestTests(unittest.TestCase):
    def test_source_revision_uses_export_substitution_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision = "f" * 40
            (root / "SOURCE_REVISION.txt").write_text(
                revision + "\n",
                encoding="utf-8",
            )
            resolved = build_manifest_module._source_revision(root)

        self.assertEqual(resolved, revision)

    def test_manifest_hashes_artifacts_acceptance_and_native_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            onedir = root / "BLECalibration"
            onedir.mkdir()
            executable = onedir / "BLECalibration.exe"
            executable.write_bytes(b"fake-windows-executable")
            driver = onedir / "_internal" / "clgcan_driver.pyd"
            driver.parent.mkdir()
            driver.write_bytes(b"fake-native-driver")
            archive = root / "BLECalibration-win64.zip"
            archive.write_bytes(b"fake-archive")
            installer = root / "BLECalibration-Setup.exe"
            installer.write_bytes(b"fake-installer")
            acceptance = root / "acceptance"
            acceptance.mkdir()
            for name in (
                "bundle-can.blf",
                "bundle-can.manifest.json",
                "zlg-bundle.json",
                "source-ui.json",
                "manual-workflow.json",
                "live-workflow.json",
                "analysis.json",
                "analysis.png",
                "manual.png",
                "live-zlg.png",
            ):
                (acceptance / name).write_bytes(name.encode("utf-8"))

            manifest = build_manifest_module.build_manifest(
                onedir_exe=executable,
                archive=archive,
                installer=installer,
                acceptance_dir=acceptance,
                include_zlgcan=True,
                source_tests_run=True,
            )

        self.assertEqual(
            manifest["schema"],
            "ble-calibration-build-manifest/v1",
        )
        self.assertEqual(manifest["python_bits"], 64)
        self.assertTrue(manifest["include_zlgcan"])
        self.assertTrue(manifest["source_tests_run"])
        self.assertIsNotNone(manifest["source_revision"])
        self.assertEqual(len(manifest["native_drivers"]), 1)
        self.assertEqual(
            len(manifest["artifacts"]["onedir_exe"]["sha256"]),
            64,
        )
        self.assertEqual(
            set(manifest["acceptance"]),
            {
                "bundle-can.blf",
                "bundle-can.manifest.json",
                "zlg-bundle.json",
                "source-ui.json",
                "manual-workflow.json",
                "live-workflow.json",
                "analysis.json",
                "analysis.png",
                "manual.png",
                "live-zlg.png",
            },
        )

    def test_requested_zlg_driver_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            onedir = root / "BLECalibration"
            onedir.mkdir()
            executable = onedir / "BLECalibration.exe"
            executable.write_bytes(b"fake-windows-executable")
            archive = root / "BLECalibration-win64.zip"
            archive.write_bytes(b"fake-archive")
            acceptance = root / "acceptance"
            acceptance.mkdir()
            for name in (
                "bundle-can.blf",
                "bundle-can.manifest.json",
                "analysis.json",
                "analysis.png",
                "manual.png",
                "live-zlg.png",
            ):
                (acceptance / name).write_bytes(name.encode("utf-8"))

            with self.assertRaises(RuntimeError):
                build_manifest_module.build_manifest(
                    onedir_exe=executable,
                    archive=archive,
                    installer=None,
                    acceptance_dir=acceptance,
                    include_zlgcan=True,
                    source_tests_run=False,
                )


if __name__ == "__main__":
    unittest.main()
