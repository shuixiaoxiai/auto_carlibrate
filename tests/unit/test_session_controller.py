import unittest
import time
from queue import Empty, Queue

from ble_calibration.can import MemoryCanSource
from ble_calibration.can.source import CanSource, SourceState
from ble_calibration.domain import (
    Direction,
    DirectionStatus,
    EventType,
    SessionPhase,
)
from ble_calibration.mock.generator import MockConfig, generate_mock_session
from ble_calibration.session import (
    DirectionSessionController,
    ManualCaptureCoordinator,
    SessionStateError,
)


class PushCanSource(CanSource):
    def __init__(self) -> None:
        super().__init__()
        self.frames = Queue()
        self.stop_count = 0

    def connect(self) -> None:
        self._set_state(SourceState.CONNECTED, "test live source connected")

    def push(self, frames) -> None:
        for frame in frames:
            self.frames.put(frame)

    def recv(self, timeout: float = 1.0):
        if self.state is SourceState.CONNECTED:
            self._set_state(SourceState.RUNNING, "test live source running")
        if self.state in (SourceState.STOPPED, SourceState.ERROR):
            return None
        try:
            return self.frames.get(timeout=timeout)
        except Empty:
            return None

    def stop(self) -> None:
        if self.state is SourceState.STOPPED:
            return
        self.stop_count += 1
        self._set_state(SourceState.STOPPED, "test live source stopped")


class FailingCanSource(CanSource):
    def connect(self) -> None:
        self._set_state(SourceState.ERROR, "device unavailable")
        raise RuntimeError("device unavailable")

    def recv(self, timeout: float = 1.0):
        return None

    def stop(self) -> None:
        self._set_state(SourceState.STOPPED, "failed source stopped")


class CountingRecorder:
    def __init__(self) -> None:
        self.frames = []
        self.stop_count = 0

    def write(self, frame) -> None:
        self.frames.append(frame)

    def stop(self) -> None:
        self.stop_count += 1


class DirectionSessionControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frames, cls.manifest = generate_mock_session(MockConfig(seed=20260730))

    def frames_for_manifest_direction(self, item):
        return [
            frame
            for frame in self.frames
            if item["start_time"] <= frame.timestamp <= item["end_time"] + 0.1
        ]

    @staticmethod
    def wait_until(predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("condition was not reached before timeout")
            time.sleep(0.002)

    def test_complete_one_direction_with_actual_distances(self) -> None:
        controller = DirectionSessionController()
        item = self.manifest["directions"][0]
        controller.select_direction(Direction.from_label(item["name"]))
        controller.start(walking_speed_mps=1.1, raw_data_file="front.jsonl")
        controller.set_distances(item["lock_distance_m"], item["unlock_distance_m"])
        for frame in self.frames_for_manifest_direction(item):
            controller.process_frame(frame)
        record = controller.manual_stop(item["end_time"])

        self.assertEqual(controller.phase, SessionPhase.COMPLETE)
        self.assertEqual(record.status, DirectionStatus.COMPLETE)
        self.assertEqual(
            [event.event_type for event in record.vehicle_events],
            [EventType.LOCK, EventType.UNLOCK],
        )
        self.assertGreater(record.sample_count, 0)
        self.assertEqual(record.actual_lock_distance_m, item["lock_distance_m"])
        self.assertGreater(record.end_timestamp, record.event(EventType.UNLOCK).timestamp)

    def test_capture_preserves_target_group_and_recording_identity(self) -> None:
        controller = DirectionSessionController()
        item = self.manifest["directions"][0]
        controller.select_direction(Direction.FRONT)
        controller.start(
            group_index=2,
            recording_id="front-second-capture",
        )
        for frame in self.frames_for_manifest_direction(item):
            controller.process_frame(frame)
        record = controller.manual_stop(item["end_time"])

        self.assertEqual(record.group_index, 2)
        self.assertEqual(record.recording_id, "front-second-capture")
        self.assertIsNotNone(record.recorded_at)

    def test_all_eight_mock_directions_complete(self) -> None:
        controller = DirectionSessionController()
        for item in self.manifest["directions"]:
            direction = Direction.from_label(item["name"])
            controller.select_direction(direction)
            controller.start()
            controller.set_distances(
                item["lock_distance_m"],
                item["unlock_distance_m"],
            )
            for frame in self.frames_for_manifest_direction(item):
                controller.process_frame(frame)
            controller.manual_stop(item["end_time"])
            self.assertEqual(controller.phase, SessionPhase.COMPLETE)

        self.assertEqual(len(controller.records), 8)
        self.assertTrue(
            all(record.status is DirectionStatus.COMPLETE for record in controller.records)
        )

    def test_missing_unlock_can_be_manually_stopped_as_incomplete(self) -> None:
        controller = DirectionSessionController()
        item = self.manifest["directions"][0]
        controller.select_direction(Direction.FRONT)
        controller.start()
        for frame in self.frames_for_manifest_direction(item):
            controller.process_frame(frame)
            if controller.phase is SessionPhase.WAITING_UNLOCK:
                break
        record = controller.manual_stop()

        self.assertEqual(controller.phase, SessionPhase.INCOMPLETE)
        self.assertEqual(record.status, DirectionStatus.INCOMPLETE)
        self.assertIsNotNone(record.event(EventType.LOCK))
        self.assertIsNone(record.event(EventType.UNLOCK))

    def test_active_snapshot_and_partial_distance_survive_manual_stop(self) -> None:
        controller = DirectionSessionController()
        item = self.manifest["directions"][0]
        controller.select_direction(Direction.FRONT)
        controller.start()
        for frame in self.frames_for_manifest_direction(item):
            controller.process_frame(frame)
            if controller.phase is SessionPhase.WAITING_UNLOCK:
                break
        controller.set_distance(EventType.LOCK, item["lock_distance_m"])
        snapshot = controller.active_record_snapshot()
        record = controller.manual_stop()

        self.assertEqual(snapshot.status, DirectionStatus.RECORDING)
        self.assertIsNotNone(snapshot.event(EventType.LOCK))
        self.assertEqual(record.actual_lock_distance_m, item["lock_distance_m"])
        self.assertIsNone(record.actual_unlock_distance_m)
        self.assertEqual(record.status, DirectionStatus.INCOMPLETE)

    def test_manual_capture_coordinator_is_thread_safe_and_keeps_post_unlock(self) -> None:
        coordinator = ManualCaptureCoordinator()
        item = self.manifest["directions"][0]
        frames = self.frames_for_manifest_direction(item)
        coordinator.begin(
            Direction.FRONT,
            MemoryCanSource(frames, speed=0),
            walking_speed_mps=1.0,
        )
        deadline = time.monotonic() + 2.0
        while not coordinator.snapshot().source_finished:
            self.assertLess(time.monotonic(), deadline)
        active = coordinator.snapshot()
        dataset = coordinator.finish(
            lock_distance_m=item["lock_distance_m"],
            unlock_distance_m=item["unlock_distance_m"],
        )

        self.assertEqual(active.dataset.record.status, DirectionStatus.RECORDING)
        self.assertIsNotNone(active.dataset.record.event(EventType.UNLOCK))
        self.assertGreater(
            active.dataset.record.end_timestamp,
            active.dataset.record.event(EventType.UNLOCK).timestamp,
        )
        self.assertEqual(dataset.record.status, DirectionStatus.COMPLETE)
        self.assertEqual(len(dataset.samples), dataset.record.sample_count)

    def test_persistent_live_source_stays_connected_across_directions(self) -> None:
        coordinator = ManualCaptureCoordinator()
        source = PushCanSource()
        coordinator.connect(source)
        self.wait_until(lambda: coordinator.is_connected)

        first = self.manifest["directions"][0]
        first_frames = self.frames_for_manifest_direction(first)
        first_recorder = CountingRecorder()
        coordinator.begin_connected(
            Direction.from_label(first["name"]),
            raw_data_file="front.blf",
            recorder=first_recorder,
        )
        source.push(first_frames)
        self.wait_until(
            lambda: (
                coordinator.snapshot().dataset is not None
                and coordinator.snapshot().dataset.record.event(EventType.UNLOCK)
                is not None
                and len(first_recorder.frames) >= len(first_frames)
            )
        )
        first_dataset = coordinator.finish(
            lock_distance_m=first["lock_distance_m"],
            unlock_distance_m=first["unlock_distance_m"],
        )

        self.assertTrue(coordinator.is_connected)
        self.assertEqual(first_dataset.record.status, DirectionStatus.COMPLETE)
        self.assertEqual(len(first_recorder.frames), len(first_frames))
        self.assertEqual(first_recorder.stop_count, 1)
        self.assertEqual(source.stop_count, 0)

        second = self.manifest["directions"][1]
        second_frames = self.frames_for_manifest_direction(second)
        second_recorder = CountingRecorder()
        coordinator.begin_connected(
            Direction.from_label(second["name"]),
            raw_data_file="front_right.blf",
            recorder=second_recorder,
        )
        source.push(second_frames)
        self.wait_until(
            lambda: (
                coordinator.snapshot().dataset is not None
                and coordinator.snapshot().dataset.record.event(EventType.UNLOCK)
                is not None
                and len(second_recorder.frames) >= len(second_frames)
            )
        )
        second_dataset = coordinator.finish(
            lock_distance_m=second["lock_distance_m"],
            unlock_distance_m=second["unlock_distance_m"],
        )
        coordinator.disconnect()

        self.assertEqual(second_dataset.record.status, DirectionStatus.COMPLETE)
        self.assertEqual(len(second_recorder.frames), len(second_frames))
        self.assertEqual(second_recorder.stop_count, 1)
        self.assertEqual(source.stop_count, 1)
        self.assertFalse(coordinator.is_connected)

    def test_failed_live_connection_can_be_replaced_without_restart(self) -> None:
        coordinator = ManualCaptureCoordinator()
        coordinator.connect(FailingCanSource())
        self.wait_until(
            lambda: (
                coordinator.snapshot().error is not None
                and coordinator.snapshot().source_status is not None
                and coordinator.snapshot().source_status.state
                is SourceState.STOPPED
            )
        )
        self.assertFalse(coordinator.is_connected)
        self.assertIn("device unavailable", coordinator.snapshot().error)

        replacement = PushCanSource()
        coordinator.connect(replacement)
        self.wait_until(lambda: coordinator.is_connected)
        self.assertTrue(coordinator.is_connected)
        coordinator.disconnect()
        self.assertEqual(replacement.stop_count, 1)

    def test_redo_clears_previous_record(self) -> None:
        controller = DirectionSessionController()
        controller.select_direction(Direction.FRONT)
        controller.start()
        controller.manual_stop(timestamp=1.0)
        self.assertIsNotNone(controller.record_for(Direction.FRONT))
        controller.redo(Direction.FRONT)
        self.assertIsNone(controller.record_for(Direction.FRONT))
        self.assertEqual(controller.phase, SessionPhase.READY)

    def test_invalid_operator_order_is_rejected(self) -> None:
        controller = DirectionSessionController()
        with self.assertRaises(SessionStateError):
            controller.start()
        controller.select_direction(Direction.FRONT)
        controller.start()
        with self.assertRaises(SessionStateError):
            controller.select_direction(Direction.REAR)


if __name__ == "__main__":
    unittest.main()
