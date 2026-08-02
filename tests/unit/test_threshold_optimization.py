import unittest
from dataclasses import replace

from ble_calibration.analysis import DirectionDataset
from ble_calibration.cloud.models import CloudParameters
from ble_calibration.domain import (
    Direction,
    DirectionRecord,
    DirectionStatus,
    DistanceGrade,
    EventType,
    RssiSample,
    StrategyKind,
    VehicleEvent,
)
from ble_calibration.optimization import (
    AutomaticThresholdOptimizer,
    DIRECTION_ACTIVE_NODES,
    NODE_AFFECTED_DIRECTIONS,
    OptimizationConfig,
    SampleOutcome,
    optimization_readiness,
    thresholds_are_legal,
)
from ble_calibration.optimization.constraints import eligible_datasets
from ble_calibration.optimization.evaluator import CandidateEvaluator, strategy_kind
from ble_calibration.optimization.strategies import (
    disable_optional_strategies,
    strategy_presets,
)
from ble_calibration.ui.demo import build_generated_demo_state


def sample(timestamp, values):
    return RssiSample(
        relative_time=timestamp,
        source_timestamp=timestamp,
        values=tuple(values),
        node_age_ms=(0.0, 0.0, 0.0, 0.0, 0.0),
        stale=(False, False, False, False, False),
    )


def complete_dataset(unlock_distance=3.0):
    direction = Direction.FRONT
    record = DirectionRecord(
        direction=direction,
        status=DirectionStatus.COMPLETE,
        start_timestamp=0.0,
        end_timestamp=4.5,
        walking_speed_mps=1.0,
        actual_lock_distance_m=10.0,
        actual_unlock_distance_m=unlock_distance,
        vehicle_events=(
            VehicleEvent.from_request(2, 2.0, direction),
            VehicleEvent.from_request(1, 3.5, direction),
        ),
        sample_count=7,
        recording_id="front-complete",
    )
    samples = (
        sample(0.0, (-70, -70, -70, -70, -70)),
        sample(1.0, (-70, -70, -70, -70, -70)),
        sample(2.0, (-70, -70, -70, -70, -70)),
        sample(3.0, (-80, -65, -80, -80, -80)),
        sample(3.5, (-80, -65, -80, -80, -80)),
        sample(4.0, (-55, -55, -55, -55, -55)),
        sample(4.5, (-55, -55, -55, -55, -55)),
    )
    return DirectionDataset(record, samples)


class ThresholdOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.parameters = CloudParameters(
            unlock_thresholds=(-65, -65, -65, -65, -65),
            lock_thresholds=(-70, -70, -70, -70, -70),
        )

    def test_readiness_requires_at_least_one_complete_paired_group(self):
        dataset = complete_dataset()
        incomplete = DirectionDataset(
            replace(
                dataset.record,
                actual_unlock_distance_m=None,
                recording_id="front-incomplete",
            ),
            dataset.samples,
        )

        blocked = optimization_readiness(
            self.parameters,
            (incomplete,),
            OptimizationConfig(),
        )
        ready = optimization_readiness(
            self.parameters,
            (dataset, incomplete),
            OptimizationConfig(),
        )

        self.assertFalse(blocked.can_start)
        self.assertTrue(ready.can_start)
        self.assertEqual(ready.eligible_datasets, 1)
        self.assertEqual(len(ready.skipped_labels), 1)
        self.assertTrue(ready.low_confidence)

    def test_direction_matrix_keeps_remote_nodes_out_of_front_search(self):
        self.assertEqual(
            tuple(node.value for node in DIRECTION_ACTIVE_NODES[Direction.FRONT]),
            ("master", "front"),
        )
        self.assertNotIn(Direction.REAR, NODE_AFFECTED_DIRECTIONS[next(
            node for node in DIRECTION_ACTIVE_NODES[Direction.FRONT]
            if node.value == "front"
        )])

    def test_threshold_constraints_allow_disabled_pair_but_not_partial_disable(self):
        config = OptimizationConfig()
        disabled = replace(
            self.parameters,
            unlock_thresholds=(0, -65, -65, -65, -65),
            lock_thresholds=(0, -70, -70, -70, -70),
        )
        partial = replace(disabled, unlock_thresholds=(-65, -65, -65, -65, -65))
        narrow = replace(self.parameters, unlock_thresholds=(-68, -65, -65, -65, -65))

        self.assertTrue(thresholds_are_legal(disabled, config))
        self.assertFalse(thresholds_are_legal(partial, config))
        self.assertFalse(thresholds_are_legal(narrow, config))

    def test_new_unlock_below_one_meter_is_a_hard_violation(self):
        dataset = complete_dataset(unlock_distance=1.2)
        evaluator = CandidateEvaluator(
            self.parameters,
            eligible_datasets((dataset,)),
            OptimizationConfig(),
        )
        baseline = evaluator.evaluate(self.parameters)
        evaluator.set_baseline(baseline)
        delayed = replace(
            self.parameters,
            unlock_thresholds=(-55, -55, -55, -55, -55),
        )
        candidate = evaluator.evaluate(delayed)

        self.assertAlmostEqual(baseline.samples[0].unlock_distance_m, 1.2)
        self.assertLess(candidate.samples[0].unlock_distance_m, 1.0)
        self.assertEqual(candidate.metrics.near_unlock_violations, 1)
        self.assertFalse(candidate.feasible)

    def test_same_quality_prefers_safer_unlock_then_farther_lock(self):
        evaluator = CandidateEvaluator(
            self.parameters,
            (),
            OptimizationConfig(),
        )

        def outcome(lock_distance, unlock_distance):
            return SampleOutcome(
                direction=Direction.FRONT,
                group_index=1,
                recording_id="score-order",
                lock_distance_m=lock_distance,
                unlock_distance_m=unlock_distance,
                lock_grade=DistanceGrade.EXCELLENT,
                unlock_grade=DistanceGrade.EXCELLENT,
                lock_trigger_node=None,
                unlock_trigger_node=None,
                lock_strategy=None,
                unlock_strategy=None,
                lock_untriggered=False,
                unlock_untriggered=False,
            )

        nearer_unlock = evaluator._score(
            self.parameters,
            (outcome(12.0, 2.0),),
        )
        safer_unlock = evaluator._score(
            self.parameters,
            (outcome(10.0, 2.5),),
        )
        farther_lock = evaluator._score(
            self.parameters,
            (outcome(11.0, 2.5),),
        )

        self.assertGreater(safer_unlock.score, nearer_unlock.score)
        self.assertGreater(farther_lock.score, safer_unlock.score)

    def test_strategy_presets_enable_only_one_registered_strategy(self):
        source = replace(
            self.parameters,
            quick_unlock={"unlockTime": 4, "frontToFr": 3},
            mst_than_slave={"diff": 2},
        )
        disabled = disable_optional_strategies(source)
        presets = strategy_presets(disabled, source)

        self.assertIsNone(strategy_kind(disabled))
        self.assertIn(StrategyKind.QUICK_UNLOCK, presets)
        self.assertIn(StrategyKind.MASTER_THAN_SLAVE, presets)
        self.assertTrue(
            all(
                strategy_kind(parameters) is kind
                for kind, candidates in presets.items()
                for parameters in candidates
            )
        )

    def test_optimizer_is_deterministic_and_replays_generated_data(self):
        state = build_generated_demo_state(seed=20260730)
        config = OptimizationConfig(
            threshold_radius_db=1,
            maximum_sweeps=1,
            maximum_evaluations=260,
            allow_strategy_fallback=False,
        )

        first = AutomaticThresholdOptimizer(config).optimize(
            state.current_document.parameters,
            state.datasets,
        )
        second = AutomaticThresholdOptimizer(config).optimize(
            state.current_document.parameters,
            state.datasets,
        )

        self.assertEqual(first.recommendation.parameters, second.recommendation.parameters)
        self.assertEqual(first.recommendation.score, second.recommendation.score)
        self.assertEqual(first.recommendation.metrics.lock_total, 8)
        self.assertEqual(first.recommendation.metrics.unlock_total, 8)
        self.assertGreater(first.recommendation.metrics.lock_excellent_rate_percent, 62.5)
        self.assertLessEqual(first.evaluated_candidates, config.maximum_evaluations)


if __name__ == "__main__":
    unittest.main()
