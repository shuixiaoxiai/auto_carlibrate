import unittest
from dataclasses import replace

from ble_calibration.domain import (
    ActionOrigin,
    ActionPoint,
    CalibrationProject,
    CanFrame,
    ConditionPoint,
    Direction,
    DirectionRecord,
    DirectionStatus,
    EventType,
    Node,
    StrategyEventResult,
    StrategyKind,
    VehicleEvent,
)
from ble_calibration.domain.enums import DIRECTION_LABELS, NODE_LABELS


class DomainModelTests(unittest.TestCase):
    def test_node_and_direction_order_is_frozen(self) -> None:
        self.assertEqual(NODE_LABELS, ("主", "前", "后", "左", "右"))
        self.assertEqual(
            DIRECTION_LABELS,
            ("正前", "右前", "正右", "右后", "正后", "左后", "正左", "左前"),
        )
        self.assertEqual(Node.from_index(0), Node.MASTER)
        self.assertEqual(Direction.from_label("左前"), Direction.FRONT_LEFT)
        with self.assertRaises(ValueError):
            Node.from_index(-1)
        with self.assertRaises(ValueError):
            Direction.from_index(8)

    def test_can_frame_json_round_trip(self) -> None:
        frame = CanFrame(
            timestamp=12.5,
            arbitration_id=0x629,
            data=bytes(range(16)),
            channel=1,
            receive_monotonic=99.1,
        )
        self.assertEqual(CanFrame.from_json_record(frame.to_json_record()), frame)
        self.assertEqual(frame.source_timestamp, frame.timestamp)

    def test_actual_vehicle_event_request_must_match_type(self) -> None:
        event = VehicleEvent.from_request(2, 8.4, Direction.FRONT)
        self.assertEqual(event.event_type, EventType.LOCK)
        with self.assertRaises(ValueError):
            VehicleEvent(EventType.UNLOCK, 1.0, Direction.FRONT, 2)

    def test_strategy_result_preserves_solid_and_dashed_semantics(self) -> None:
        solid = ConditionPoint(
            event_type=EventType.UNLOCK,
            timestamp=10.0,
            trigger_node=Node.FRONT,
            strategy=StrategyKind.BASE,
            rssi=(-70, -65, -80, -77, -79),
        )
        dashed = ActionPoint(
            event_type=EventType.UNLOCK,
            timestamp=10.5,
            origin=ActionOrigin.CALCULATED,
        )
        result = StrategyEventResult(solid, dashed, distance_m=3.2)
        self.assertEqual(result.condition.label, "解(前)")
        with self.assertRaises(ValueError):
            StrategyEventResult(
                solid,
                ActionPoint(EventType.UNLOCK, 9.9, ActionOrigin.CALCULATED),
            )

    def test_project_and_direction_record_round_trip(self) -> None:
        lock_event = VehicleEvent.from_request(2, 12.0, Direction.FRONT)
        unlock_event = VehicleEvent.from_request(1, 20.0, Direction.FRONT)
        record = DirectionRecord(
            direction=Direction.FRONT,
            status=DirectionStatus.COMPLETE,
            start_timestamp=0.0,
            end_timestamp=22.0,
            walking_speed_mps=1.1,
            actual_lock_distance_m=10.0,
            actual_unlock_distance_m=3.0,
            vehicle_events=(lock_event, unlock_event),
            sample_count=221,
            raw_data_file="captures/front.jsonl",
        )
        project = CalibrationProject(
            name="八方向基准",
            original_cloud_hex="00AABB",
            directions=(record,),
        )
        self.assertEqual(
            CalibrationProject.from_dict(project.to_dict()).to_dict(),
            project.to_dict(),
        )
        self.assertEqual(record.event(EventType.UNLOCK), unlock_event)

    def test_project_rejects_duplicate_directions(self) -> None:
        record = DirectionRecord(direction=Direction.FRONT)
        with self.assertRaises(ValueError):
            CalibrationProject(name="duplicate", directions=(record, record))

        second_group = replace(record, group_index=2, recording_id="front-group-2")
        project = CalibrationProject(
            name="same direction groups",
            directions=(record, second_group),
        )
        self.assertEqual(len(project.directions), 2)


if __name__ == "__main__":
    unittest.main()
