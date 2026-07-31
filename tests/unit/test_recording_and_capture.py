import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ble_calibration.can.mock_source import MockCanSource
from ble_calibration.can.recording import JsonlFrameRecorder, RotatingBlfRecorder
from ble_calibration.capture import CaptureWorker
from ble_calibration.domain.models import CanFrame


class FakeBlfWriter:
    instances = []

    def __init__(self, path, channel):
        self.path = path
        self.channel = channel
        self.messages = []
        self.stopped = False
        self.__class__.instances.append(self)

    def write(self, message):
        self.messages.append(message)

    def stop(self):
        self.stopped = True


def fake_message_factory(**kwargs):
    return kwargs


class RecordingAndCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeBlfWriter.instances = []

    def test_jsonl_recorder_round_trip(self) -> None:
        frame = CanFrame(1.25, 0x629, b"\x01\x02")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.jsonl"
            recorder = JsonlFrameRecorder(path)
            recorder.write(frame)
            recorder.stop()
            saved = CanFrame.from_json_record(
                json.loads(path.read_text(encoding="utf-8"))
            )
        self.assertEqual(saved, frame)

    def test_blf_recorder_rolls_without_empty_trailing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = RotatingBlfRecorder(
                Path(temp_dir) / "capture.blf",
                channel=0,
                max_frames=2,
                writer_factory=FakeBlfWriter,
                message_factory=fake_message_factory,
                today=lambda: date(2026, 7, 30),
            )
            for index in range(5):
                recorder.write(CanFrame(float(index), 0x100 + index, bytes([index])))
            recorder.stop()

        self.assertEqual([len(item.messages) for item in FakeBlfWriter.instances], [2, 2, 1])
        self.assertEqual(
            [Path(item.path).name for item in FakeBlfWriter.instances],
            ["capture_20260730.blf", "capture_20260730_2.blf", "capture_20260730_3.blf"],
        )
        self.assertTrue(all(item.stopped for item in FakeBlfWriter.instances))

    def test_capture_worker_closes_source_and_recorder(self) -> None:
        frames = [CanFrame(index * 0.1, 0x100 + index, bytes([index])) for index in range(5)]
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            output_path = Path(temp_dir) / "output.jsonl"
            input_path.write_text(
                "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames),
                encoding="utf-8",
            )
            source = MockCanSource(input_path, speed=0)
            recorder = JsonlFrameRecorder(output_path)
            seen = []
            worker = CaptureWorker(
                source,
                recorder=recorder,
                on_frame=seen.append,
                max_frames=3,
            )
            worker.start()
            self.assertTrue(worker.join(timeout=2))
            saved_lines = output_path.read_text(encoding="utf-8").splitlines()

        self.assertIsNone(worker.last_error)
        self.assertEqual(worker.frame_count, 3)
        self.assertEqual(seen, frames[:3])
        self.assertEqual(len(saved_lines), 3)
        self.assertFalse(worker.is_alive)


if __name__ == "__main__":
    unittest.main()
