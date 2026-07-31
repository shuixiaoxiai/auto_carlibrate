import unittest
import time

from ble_calibration.can import MemoryCanSource
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
