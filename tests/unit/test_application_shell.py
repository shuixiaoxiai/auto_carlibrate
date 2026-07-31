import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ble_calibration.app.main import main, zlg_runtime_report
from ble_calibration.domain.models import CanFrame
from ble_calibration.mock.generator import MockConfig, generate_mock_session
from ble_calibration.storage import ProjectRepository
from tools.can_protocol import decode_frame as compatibility_decode_frame
from ble_calibration.can.protocol import decode_frame as canonical_decode_frame
from tools.parse_cloud import parse_cloud

SAMPLE_CLOUD_HEX = (
    "00C2C7CABEC1BEC4C6BBBE0332143C1E37282828285F50505050000000002D14141B1B0100"
    "30B029FFFF0000001A4644442B03000300001C4400000D059908535914143211000000000000"
    "002333000000221D9C9C00"
)


class ApplicationShellTests(unittest.TestCase):
    def test_info_command_reports_frozen_dimensions(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["info", "--json"])
        info = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(len(info["directions"]), 8)
        self.assertEqual(len(info["nodes"]), 5)

    def test_zlg_runtime_report_handles_missing_dependency(self) -> None:
        def missing_import(name):
            raise ImportError(f"{name} is unavailable")

        report = zlg_runtime_report(missing_import)

        self.assertFalse(report["ok"])
        self.assertIn("ImportError", report["error"])
        self.assertEqual(report["native_drivers"], [])

    def test_zlg_runtime_report_finds_registered_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "zlgcan"
            package_dir.mkdir()
            package_file = package_dir / "__init__.py"
            package_file.write_text("", encoding="utf-8")
            modules = {
                "can": SimpleNamespace(
                    interfaces=SimpleNamespace(
                        BACKENDS={
                            "zlgcan": (
                                "zlgcan.can.interfaces.zlgcan",
                                "ZCanBus",
                            )
                        }
                    )
                ),
                "zlgcan": SimpleNamespace(__file__=str(package_file)),
                "zlgcan.zlgcan": SimpleNamespace(
                    ZCANDeviceType=SimpleNamespace(
                        ZCAN_USBCANFD_200U=41,
                    )
                ),
            }

            report = zlg_runtime_report(modules.__getitem__)

        self.assertTrue(report["ok"])
        self.assertEqual(
            report["backend"],
            ["zlgcan.can.interfaces.zlgcan", "ZCanBus"],
        )
        self.assertEqual(report["device_type"], "41")

    def test_generate_mock_command_writes_eight_directions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "session.jsonl"
            manifest = Path(temp_dir) / "session.manifest.json"
            with contextlib.redirect_stdout(io.StringIO()):
                result = main([
                    "generate-mock",
                    "--output",
                    str(output),
                    "--manifest",
                    str(manifest),
                    "--seed",
                    "123",
                ])
            saved_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(len(saved_manifest["directions"]), 8)

    def test_mock_uses_unified_can_frame_and_protocol(self) -> None:
        frames, _ = generate_mock_session(MockConfig(seed=7))
        self.assertIsInstance(frames[0], CanFrame)
        self.assertIs(compatibility_decode_frame, canonical_decode_frame)

    def test_capture_mock_command_uses_worker_and_writes_limit(self) -> None:
        frames, _ = generate_mock_session(MockConfig(seed=17))
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            output_path = Path(temp_dir) / "captured.jsonl"
            input_path.write_text(
                "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames[:20]),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main([
                    "capture-mock",
                    "--input",
                    str(input_path),
                    "--speed",
                    "0",
                    "--max-frames",
                    "12",
                    "--output",
                    str(output_path),
                ])
            saved_count = len(output_path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(result, 0)
        self.assertEqual(saved_count, 12)

    def test_session_demo_completes_all_directions(self) -> None:
        frames, manifest = generate_mock_session(MockConfig(seed=20260730))
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            manifest_path = Path(temp_dir) / "manifest.json"
            input_path.write_text(
                "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main([
                    "session-demo",
                    "--input",
                    str(input_path),
                    "--manifest",
                    str(manifest_path),
                    "--json",
                ])
            summary = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(summary["complete_count"], 8)
        self.assertEqual(summary["incomplete_count"], 0)

    def test_cloud_decode_and_encode_commands(self) -> None:
        decoded_output = io.StringIO()
        with contextlib.redirect_stdout(decoded_output):
            decode_result = main(["cloud-decode", SAMPLE_CLOUD_HEX])
        decoded = json.loads(decoded_output.getvalue())
        self.assertEqual(decode_result, 0)
        self.assertEqual(decoded["bleUnlockThred"][0], -62)

        encoded_output = io.StringIO()
        with contextlib.redirect_stdout(encoded_output):
            encode_result = main([
                "cloud-encode",
                SAMPLE_CLOUD_HEX,
                "--unlock",
                "-63",
                "-58",
                "-55",
                "-67",
                "-64",
                "--set",
                "quickLock.weakFront=2",
            ])
        encoded = encoded_output.getvalue().strip()
        self.assertEqual(encode_result, 0)
        self.assertEqual(parse_cloud(encoded)["bleUnlockThred"][0], -63)
        self.assertEqual(parse_cloud(encoded)["quickLock"]["weakFront"], 2)

    def test_project_demo_persists_reopens_replays_and_recomputes(self) -> None:
        frames, manifest = generate_mock_session(MockConfig(seed=20260730))
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            manifest_path = Path(temp_dir) / "manifest.json"
            database_path = Path(temp_dir) / "projects.sqlite3"
            input_path.write_text(
                "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                result = main([
                    "project-demo",
                    "--input",
                    str(input_path),
                    "--manifest",
                    str(manifest_path),
                    "--database",
                    str(database_path),
                ])

            with ProjectRepository(database_path) as repository:
                projects = repository.list_projects()
                analysis = repository.latest_analysis(projects[0].project_id)

        self.assertEqual(result, 0)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].direction_count, 8)
        self.assertEqual(analysis.payload["lock_summary"]["total"], 8)
        self.assertEqual(analysis.payload["unlock_summary"]["total"], 8)


if __name__ == "__main__":
    unittest.main()
