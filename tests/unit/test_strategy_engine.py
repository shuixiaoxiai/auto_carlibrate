import unittest
from dataclasses import replace

from ble_calibration.cloud.models import CloudParameters
from ble_calibration.domain import Direction, RssiSample, StrategyKind
from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
)
from ble_calibration.session import DirectionSessionController
from ble_calibration.strategy import StrategyEngine


def sample(timestamp, values):
    return RssiSample(
        relative_time=timestamp,
        source_timestamp=timestamp,
        values=tuple(values),
        node_age_ms=(0.0, 0.0, 0.0, 0.0, 0.0),
        stale=(False, False, False, False, False),
    )


def parameters(**updates):
    base = CloudParameters(
        unlock_thresholds=(-60, -60, -60, -60, -60),
        lock_thresholds=(-70, -70, -70, -70, -70),
    )
    return replace(base, **updates)


def base_lock_prefix():
    return [
        sample(0.0, (-70, -70, -70, -70, -70)),
        sample(1.0, (-70, -70, -70, -70, -70)),
        sample(2.0, (-70, -70, -70, -70, -70)),
    ]


class StrategyEngineTests(unittest.TestCase):
    def test_base_lock_and_unlock_use_timestamp_durations(self) -> None:
        samples = base_lock_prefix() + [
            sample(3.0, (-75, -60, -75, -75, -75)),
            sample(3.2, (-75, -60, -75, -75, -75)),
            sample(3.5, (-75, -60, -75, -75, -75)),
        ]
        result = StrategyEngine(parameters()).analyze(Direction.FRONT, samples)
        self.assertEqual(result.lock.condition.timestamp, 0.0)
        self.assertEqual(result.lock.action.timestamp, 2.0)
        self.assertEqual(result.lock.condition.strategy, StrategyKind.BASE)
        self.assertEqual(result.unlock.condition.timestamp, 3.0)
        self.assertEqual(result.unlock.action.timestamp, 3.5)
        self.assertEqual(result.unlock.condition.label, "解(前)")

    def test_quick_lock_reclassifies_buffer_zone(self) -> None:
        quick_lock = {
            "weakFront": 3,
            "weakRear": 0,
            "weakFl": 0,
            "weakFr": 0,
            "strongMst": 2,
            "strongFront": 0,
            "strongRear": 2,
            "strongFl": 2,
            "strongFr": 2,
            "reserve": 0,
        }
        weak_front = (-72, -67, -72, -72, -72)
        samples = [
            sample(0.0, weak_front),
            sample(1.0, weak_front),
            sample(2.0, weak_front),
            sample(3.0, (-75, -60, -75, -75, -75)),
            sample(3.5, (-75, -60, -75, -75, -75)),
        ]
        result = StrategyEngine(
            parameters(quick_lock=quick_lock)
        ).analyze(Direction.FRONT, samples)
        self.assertIsNotNone(result.lock)
        self.assertEqual(result.lock.condition.strategy, StrategyKind.QUICK_LOCK)
        self.assertEqual(result.lock.condition.trigger_node.value, "front")

    def test_master_unlock_strategy(self) -> None:
        samples = base_lock_prefix() + [
            sample(3.0, (-66, -75, -75, -75, -75)),
            sample(3.5, (-66, -75, -75, -75, -75)),
        ]
        result = StrategyEngine(
            parameters(mst_unlock=(-66, -66, -66, -66, -66))
        ).analyze(Direction.FRONT, samples)
        self.assertEqual(
            result.unlock.condition.strategy,
            StrategyKind.MASTER_UNLOCK,
        )
        self.assertEqual(result.unlock.condition.trigger_node.value, "master")

    def test_quick_unlock_transition_strategy(self) -> None:
        quick_unlock = {
            "unlockTime": 4,
            "frontToFr": 5,
            "frontToFl": 0,
            "rearToFl": 0,
            "rearToFr": 0,
            "reserve": 0,
        }
        samples = base_lock_prefix() + [
            sample(2.5, (-75, -64, -80, -80, -70)),
            sample(3.0, (-75, -70, -80, -80, -65)),
            sample(3.25, (-75, -70, -80, -80, -65)),
            sample(3.5, (-75, -70, -80, -80, -65)),
        ]
        result = StrategyEngine(
            parameters(quick_unlock=quick_unlock)
        ).analyze(Direction.FRONT_RIGHT, samples)
        self.assertEqual(
            result.unlock.condition.strategy,
            StrategyKind.QUICK_UNLOCK,
        )
        self.assertEqual(result.unlock.condition.trigger_node.value, "right")

    def test_master_than_slave_strategy(self) -> None:
        samples = base_lock_prefix() + [
            sample(3.0, (-65, -71, -76, -77, -78)),
            sample(3.5, (-65, -71, -76, -77, -78)),
        ]
        result = StrategyEngine(
            parameters(mst_than_slave={"diff": 3, "reserve": 0})
        ).analyze(Direction.FRONT, samples)
        self.assertEqual(
            result.unlock.condition.strategy,
            StrategyKind.MASTER_THAN_SLAVE,
        )

    def test_bevel_angle_strategy(self) -> None:
        bevel = {
            "offsetRFR": 5,
            "offsetRFF": 5,
            "offsetLFL": 0,
            "offsetLFF": 0,
            "offsetLBL": 0,
            "offsetLBB": 0,
            "offsetRBR": 0,
            "offsetRBB": 0,
        }
        samples = base_lock_prefix() + [
            sample(3.0, (-75, -65, -75, -75, -65)),
            sample(3.5, (-75, -65, -75, -75, -65)),
        ]
        result = StrategyEngine(
            parameters(bevel_angle=bevel)
        ).analyze(Direction.FRONT_RIGHT, samples)
        self.assertEqual(
            result.unlock.condition.strategy,
            StrategyKind.BEVEL_ANGLE,
        )

    def test_disabled_strategies_do_not_trigger(self) -> None:
        samples = base_lock_prefix() + [
            sample(3.0, (-65, -75, -75, -75, -75)),
            sample(3.5, (-65, -75, -75, -75, -75)),
        ]
        result = StrategyEngine(
            parameters(mst_than_slave={"diff": 0, "reserve": 0})
        ).analyze(Direction.FRONT, samples)
        self.assertIsNone(result.unlock)

    def test_zero_optional_configurations_are_disabled(self) -> None:
        self.assertIsNone(
            StrategyEngine(
                parameters(lock_thresholds=(0, 0, 0, 0, 0))
            ).analyze(Direction.FRONT, base_lock_prefix()).lock
        )
        zero_quick_lock = {
            "weakFront": 0,
            "weakRear": 0,
            "weakFl": 0,
            "weakFr": 0,
            "strongMst": 0,
            "strongFront": 0,
            "strongRear": 0,
            "strongFl": 0,
            "strongFr": 0,
            "reserve": 0,
        }
        buffer_samples = [
            sample(0.0, (-72, -67, -72, -72, -72)),
            sample(1.0, (-72, -67, -72, -72, -72)),
            sample(2.0, (-72, -67, -72, -72, -72)),
        ]
        self.assertIsNone(
            StrategyEngine(
                parameters(quick_lock=zero_quick_lock)
            ).analyze(Direction.FRONT, buffer_samples).lock
        )

        post_lock = base_lock_prefix() + [
            sample(3.0, (-65, -75, -75, -75, -75)),
            sample(3.5, (-65, -75, -75, -75, -75)),
        ]
        self.assertIsNone(
            StrategyEngine(
                parameters(mst_unlock=(0, 0, 0, 0, 0))
            ).analyze(Direction.FRONT, post_lock).unlock
        )
        self.assertIsNone(
            StrategyEngine(
                parameters(
                    quick_unlock={
                        "unlockTime": 0,
                        "frontToFr": 5,
                        "frontToFl": 0,
                        "rearToFl": 0,
                        "rearToFr": 0,
                        "reserve": 0,
                    }
                )
            ).analyze(Direction.FRONT, post_lock).unlock
        )
        self.assertIsNone(
            StrategyEngine(
                parameters(
                    bevel_angle={
                        "offsetRFR": 0,
                        "offsetRFF": 0,
                        "offsetLFL": 0,
                        "offsetLFF": 0,
                        "offsetLBL": 0,
                        "offsetLBB": 0,
                        "offsetRBR": 0,
                        "offsetRBB": 0,
                    }
                )
            ).analyze(Direction.FRONT, post_lock).unlock
        )

    def test_same_input_is_deterministic(self) -> None:
        samples = base_lock_prefix() + [
            sample(3.0, (-75, -60, -75, -75, -75)),
            sample(3.5, (-75, -60, -75, -75, -75)),
        ]
        engine = StrategyEngine(parameters())
        self.assertEqual(
            engine.analyze(Direction.FRONT, samples),
            engine.analyze(Direction.FRONT, samples),
        )

    def test_reference_parameters_reproduce_all_mock_action_times(self) -> None:
        frames, manifest = generate_mock_session(MockConfig(seed=20260730))
        controller = DirectionSessionController()
        engine = StrategyEngine(
            CloudParameters(
                unlock_thresholds=tuple(REFERENCE_UNLOCK_THRESHOLDS),
                lock_thresholds=tuple(REFERENCE_LOCK_THRESHOLDS),
            )
        )
        for item in manifest["directions"]:
            direction = Direction.from_label(item["name"])
            controller.select_direction(direction)
            controller.start()
            controller.set_distances(
                item["lock_distance_m"],
                item["unlock_distance_m"],
            )
            for frame in frames:
                if item["start_time"] <= frame.timestamp <= item["end_time"]:
                    controller.process_frame(frame)
            controller.manual_stop(item["end_time"])
            result = engine.analyze(direction, controller.samples_for(direction))
            self.assertIsNotNone(result.lock)
            self.assertIsNotNone(result.unlock)
            self.assertAlmostEqual(
                result.lock.action.timestamp,
                item["lock_event_time"],
                delta=0.05,
            )
            self.assertAlmostEqual(
                result.unlock.action.timestamp,
                item["unlock_event_time"],
                delta=0.05,
            )


if __name__ == "__main__":
    unittest.main()
