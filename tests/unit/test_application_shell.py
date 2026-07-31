import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ble_calibration.app.main import main
from ble_calibration.domain.models import CanFrame
from ble_calibration.mock.generator import MockConfig, generate_mock_session
from tools.can_protocol import decode_frame as compatibility_decode_frame
from ble_calibration.can.protocol import decode_frame as canonical_decode_frame


class ApplicationShellTests(unittest.TestCase):
    def test_info_command_reports_frozen_dimensions(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["info", "--json"])
        info = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(len(info["directions"]), 8)
        self.assertEqual(len(info["nodes"]), 5)

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


if __name__ == "__main__":
    unittest.main()
