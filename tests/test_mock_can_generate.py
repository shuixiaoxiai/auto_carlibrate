import json
import tempfile
import unittest
from pathlib import Path

from tools.can_protocol import CANID_LOCKREQ, decode_frame
from tools.mock_can_generate import (
    DIRECTIONS,
    MockConfig,
    generate_mock_session,
    write_jsonl,
    write_manifest,
)


class MockCanGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = MockConfig(seed=1234, sample_rate_hz=10.0)

    def test_generation_is_deterministic(self) -> None:
        first_frames, first_manifest = generate_mock_session(self.config)
        second_frames, second_manifest = generate_mock_session(self.config)
        self.assertEqual(first_frames, second_frames)
        self.assertEqual(first_manifest, second_manifest)

    def test_all_directions_have_lock_then_unlock_edges(self) -> None:
        frames, manifest = generate_mock_session(self.config)
        self.assertEqual(len(manifest["directions"]), len(DIRECTIONS))
        self.assertTrue(all(item["lock_event_time"] is not None for item in manifest["directions"]))
        self.assertTrue(all(item["unlock_event_time"] is not None for item in manifest["directions"]))

        request_edges = []
        previous_request = 0
        for frame in frames:
            if frame.arbitration_id != CANID_LOCKREQ:
                continue
            request = decode_frame(frame.arbitration_id, frame.data)["lock_req"]
            if request in (1, 2) and request != previous_request:
                request_edges.append(request)
            previous_request = request

        self.assertEqual(request_edges, [2, 1] * len(DIRECTIONS))

    def test_jsonl_and_manifest_are_writable(self) -> None:
        frames, manifest = generate_mock_session(self.config)
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "session.jsonl"
            manifest_path = Path(temp_dir) / "session.manifest.json"
            write_jsonl(output, frames)
            write_manifest(manifest_path, manifest)

            first_record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
            saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("arbitration_id", first_record)
            self.assertEqual(saved_manifest["schema"], "digital-key-mock-can/v1")


if __name__ == "__main__":
    unittest.main()
