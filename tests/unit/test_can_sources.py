import json
import tempfile
import unittest
from pathlib import Path

from ble_calibration.can.mock_source import MockCanSource
from ble_calibration.can.source import CanSourceError, SourceState
from ble_calibration.can.zlg_source import ZlgCanSource
from ble_calibration.config import CanSettings
from ble_calibration.domain.models import CanFrame


def write_frames(path: Path, frames) -> None:
    path.write_text(
        "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames),
        encoding="utf-8",
    )


class FakeMessage:
    def __init__(self, timestamp, arbitration_id, data, channel=0):
        self.timestamp = timestamp
        self.arbitration_id = arbitration_id
        self.data = data
        self.channel = channel
        self.is_fd = True
        self.bitrate_switch = True


class FakeBus:
    def __init__(self, messages):
        self.messages = list(messages)
        self.shutdown_called = False

    def recv(self, timeout):
        return self.messages.pop(0) if self.messages else None

    def shutdown(self):
        self.shutdown_called = True


class CanSourceTests(unittest.TestCase):
    def test_mock_replay_emits_frames_and_lifecycle(self) -> None:
        frames = [
            CanFrame(0.0, 0x100, b"\x01"),
            CanFrame(0.1, 0x101, b"\x02"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frames.jsonl"
            write_frames(path, frames)
            source = MockCanSource(path, speed=0)
            states = []
            source.subscribe_status(lambda status: states.append(status.state))
            source.connect()
            received = [source.recv(), source.recv()]
            self.assertIsNone(source.recv())

        self.assertEqual(received, frames)
        self.assertEqual(source.state, SourceState.STOPPED)
        self.assertIn(SourceState.CONNECTING, states)
        self.assertIn(SourceState.RUNNING, states)

    def test_mock_loop_uses_continuous_timestamps(self) -> None:
        frames = [
            CanFrame(0.0, 0x100, b"\x01"),
            CanFrame(1.0, 0x101, b"\x02"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frames.jsonl"
            write_frames(path, frames)
            source = MockCanSource(path, speed=0, loop=True)
            source.connect()
            timestamps = [source.recv().timestamp for _ in range(4)]
            source.stop()
        self.assertEqual(timestamps, [0.0, 1.0, 2.0, 3.0])

    def test_accelerated_loop_covers_30_minute_source_timeline(self) -> None:
        frames = [
            CanFrame(0.0, 0x100, b"\x01"),
            CanFrame(1.0, 0x101, b"\x02"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frames.jsonl"
            write_frames(path, frames)
            source = MockCanSource(path, speed=0, loop=True)
            source.connect()
            last = None
            for _ in range(1802):
                last = source.recv()
            source.stop()
        self.assertGreaterEqual(last.timestamp, 1800.0)

    def test_mock_invalid_line_reports_source_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.jsonl"
            path.write_text("{bad json}\n", encoding="utf-8")
            source = MockCanSource(path)
            with self.assertRaises(CanSourceError):
                source.connect()
        self.assertEqual(source.state, SourceState.ERROR)

    def test_zlg_source_is_lazy_and_converts_messages(self) -> None:
        bus = FakeBus([
            FakeMessage(1.0, 0x111, b"\x00", channel=1),
            FakeMessage(1.1, 0x629, b"\x01\x02", channel=0),
        ])
        captured_kwargs = {}

        def bus_factory(**kwargs):
            captured_kwargs.update(kwargs)
            return bus

        source = ZlgCanSource(
            CanSettings(channel=0, library_path=r"D:\zlg\library"),
            bus_factory=bus_factory,
            device_type="fake-device",
        )
        source.connect()
        frame = source.recv(timeout=1)
        source.stop()

        self.assertEqual(frame.arbitration_id, 0x629)
        self.assertEqual(frame.data, b"\x01\x02")
        self.assertEqual(captured_kwargs["interface"], "zlgcan")
        self.assertEqual(captured_kwargs["device_type"], "fake-device")
        self.assertEqual(captured_kwargs["libpath"], r"D:\zlg\library")
        self.assertTrue(bus.shutdown_called)


if __name__ == "__main__":
    unittest.main()
