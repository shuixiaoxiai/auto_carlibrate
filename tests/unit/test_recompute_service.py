import unittest

from ble_calibration.analysis import (
    DirectionDataset,
    EightDirectionRecomputeService,
    WhatIfSession,
    lock_grade,
    project_action_distance,
    unlock_grade,
)
from ble_calibration.cloud import decode_cloud
from ble_calibration.cloud.models import CloudParameters
from ble_calibration.domain import (
    ActionOrigin,
    Direction,
    DistanceGrade,
    EventType,
)
from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
)
from ble_calibration.session import DirectionSessionController

SAMPLE_CLOUD_HEX = (
    "00C2C7CABEC1BEC4C6BBBE0332143C1E37282828285F50505050000000002D14141B1B0100"
    "30B029FFFF0000001A4644442B03000300001C4400000D059908535914143211000000000000"
    "002333000000221D9C9C00"
)


class DistanceTests(unittest.TestCase):
    def test_lock_boundaries(self) -> None:
        for value in (8.0, 12.0):
            self.assertEqual(lock_grade(value), DistanceGrade.EXCELLENT)
        for value in (5.0, 7.999, 12.001, 16.0):
            self.assertEqual(lock_grade(value), DistanceGrade.GOOD)
        for value in (4.999, 16.001):
            self.assertEqual(lock_grade(value), DistanceGrade.POOR)

    def test_unlock_boundaries(self) -> None:
        for value in (2.0, 5.0):
            self.assertEqual(unlock_grade(value), DistanceGrade.EXCELLENT)
        for value in (0.5, 1.999, 5.001, 8.0):
            self.assertEqual(unlock_grade(value), DistanceGrade.GOOD)
        for value in (0.499, 8.001):
            self.assertEqual(unlock_grade(value), DistanceGrade.POOR)

    def test_action_distance_projection(self) -> None:
        self.assertAlmostEqual(
            project_action_distance(EventType.LOCK, 10.0, 5.0, 6.0, 1.2),
            11.2,
        )
        self.assertAlmostEqual(
            project_action_distance(EventType.UNLOCK, 4.0, 5.0, 6.0, 1.2),
            2.8,
        )
        self.assertEqual(
            project_action_distance(EventType.UNLOCK, 0.2, 5.0, 7.0, 1.0),
            0.0,
        )


class RecomputeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        frames, manifest = generate_mock_session(MockConfig(seed=20260730))
        controller = DirectionSessionController()
        datasets = []
        for item in manifest["directions"]:
            direction = Direction.from_label(item["name"])
            controller.select_direction(direction)
            controller.start(walking_speed_mps=1.0)
            controller.set_distances(
                item["lock_distance_m"],
                item["unlock_distance_m"],
            )
            for frame in frames:
                if item["start_time"] <= frame.timestamp <= item["end_time"]:
                    controller.process_frame(frame)
            record = controller.manual_stop(item["end_time"])
            datasets.append(
                DirectionDataset(record, controller.samples_for(direction))
            )
        cls.datasets = tuple(datasets)
        cls.parameters = CloudParameters(
            unlock_thresholds=tuple(REFERENCE_UNLOCK_THRESHOLDS),
            lock_thresholds=tuple(REFERENCE_LOCK_THRESHOLDS),
        )

    def test_baseline_uses_actual_actions_and_expected_statistics(self) -> None:
        result = EightDirectionRecomputeService().recompute(
            self.parameters,
            self.datasets,
            use_actual_action_times=True,
        )
        self.assertEqual(len(result.directions), 8)
        self.assertEqual(result.lock_summary.total, 8)
        self.assertEqual(result.lock_summary.excellent, 5)
        self.assertEqual(result.lock_summary.good, 3)
        self.assertEqual(result.lock_summary.poor, 0)
        self.assertEqual(result.lock_summary.excellent_rate_percent, 62.5)
        self.assertEqual(result.unlock_summary.excellent, 5)
        self.assertEqual(result.unlock_summary.good, 3)
        self.assertEqual(result.unlock_summary.excellent_rate_percent, 62.5)
        self.assertTrue(
            all(
                analysis.lock.action.origin is ActionOrigin.VEHICLE
                and analysis.unlock.action.origin is ActionOrigin.VEHICLE
                for analysis in result.directions.values()
            )
        )

    def test_untriggered_what_if_recomputes_poor_lists(self) -> None:
        result = EightDirectionRecomputeService().recompute(
            CloudParameters(
                unlock_thresholds=self.parameters.unlock_thresholds,
                lock_thresholds=(-100, -100, -100, -100, -100),
            ),
            self.datasets,
        )
        self.assertEqual(result.lock_summary.total, 8)
        self.assertEqual(result.lock_summary.excellent, 0)
        self.assertEqual(result.lock_summary.poor, 8)
        self.assertEqual(len(result.lock_summary.untriggered_directions), 8)
        self.assertEqual(len(result.unlock_summary.untriggered_directions), 8)

    def test_what_if_update_recomputes_grade_summaries_together(self) -> None:
        document = decode_cloud(SAMPLE_CLOUD_HEX).with_updates(
            unlock_thresholds=REFERENCE_UNLOCK_THRESHOLDS,
            lock_thresholds=REFERENCE_LOCK_THRESHOLDS,
        )
        session = WhatIfSession(document, self.datasets)
        baseline = session.recompute()
        changed = session.apply_updates(
            lock_thresholds=(-100, -100, -100, -100, -100),
        )

        self.assertEqual(baseline.lock_summary.excellent, 5)
        self.assertEqual(changed.lock_summary.excellent, 0)
        self.assertEqual(changed.lock_summary.good, 0)
        self.assertEqual(changed.lock_summary.poor, 8)
        self.assertEqual(len(changed.lock_summary.untriggered_directions), 8)
        self.assertEqual(changed.unlock_summary.total, 8)

    def test_threshold_and_strategy_updates_finish_under_200_ms(self) -> None:
        document = decode_cloud(SAMPLE_CLOUD_HEX).with_updates(
            unlock_thresholds=REFERENCE_UNLOCK_THRESHOLDS,
            lock_thresholds=REFERENCE_LOCK_THRESHOLDS,
        )
        session = WhatIfSession(document, self.datasets)
        baseline = session.recompute()
        threshold_result = session.apply_updates(
            lock_thresholds=[value - 1 for value in REFERENCE_LOCK_THRESHOLDS]
        )
        strategy_result = session.apply_updates(
            strategy_updates={"quickLock": {"weakFront": 2}}
        )

        self.assertLess(baseline.elapsed_ms, 200.0)
        self.assertLess(threshold_result.elapsed_ms, 200.0)
        self.assertLess(strategy_result.elapsed_ms, 200.0)
        self.assertEqual(len(strategy_result.directions), 8)
        self.assertTrue(
            any(
                threshold_result.directions[direction].lock.action.timestamp
                != baseline.directions[direction].lock.action.timestamp
                for direction in baseline.directions
                if threshold_result.directions[direction].lock is not None
            )
        )

    def test_one_click_restore_recovers_original_hex_and_actual_lines(self) -> None:
        document = decode_cloud(SAMPLE_CLOUD_HEX).with_updates(
            unlock_thresholds=REFERENCE_UNLOCK_THRESHOLDS,
            lock_thresholds=REFERENCE_LOCK_THRESHOLDS,
        )
        session = WhatIfSession(document, self.datasets)
        original_hex = session.encoded_hex()
        session.apply_updates(
            unlock_thresholds=[value + 1 for value in REFERENCE_UNLOCK_THRESHOLDS]
        )
        self.assertNotEqual(session.encoded_hex(), original_hex)
        restored = session.restore()
        self.assertEqual(session.encoded_hex(), original_hex)
        self.assertTrue(
            all(
                analysis.lock.action.origin is ActionOrigin.VEHICLE
                for analysis in restored.directions.values()
                if analysis.lock is not None
            )
        )


if __name__ == "__main__":
    unittest.main()
