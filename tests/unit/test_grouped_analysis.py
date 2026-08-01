import unittest
from dataclasses import replace

from ble_calibration.analysis import DirectionDataset
from ble_calibration.domain import Direction
from ble_calibration.ui import build_generated_demo_state
from ble_calibration.ui.state import CalibrationUiState


class GroupedAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = build_generated_demo_state(seed=20260730)

    def grouped_state(self) -> CalibrationUiState:
        datasets = []
        for group_index in (1, 2, 3):
            for dataset in self.base.datasets:
                datasets.append(
                    DirectionDataset(
                        record=replace(
                            dataset.record,
                            group_index=group_index,
                            recording_id=(
                                f"{dataset.record.direction.value}-{group_index}"
                            ),
                            recorded_at=f"2026-08-01T00:00:0{group_index}+00:00",
                            walking_speed_mps=float(group_index),
                        ),
                        samples=dataset.samples,
                    )
                )
        return CalibrationUiState(self.base.original_document, datasets)

    def test_three_groups_recompute_all_24_records_and_build_means(self) -> None:
        state = self.grouped_state()

        self.assertEqual(set(state.result.group_results), {1, 2, 3})
        self.assertEqual(state.result.lock_summary.total, 24)
        self.assertEqual(state.result.unlock_summary.total, 24)
        self.assertEqual(len(state.result.mean_directions), 8)
        front_mean = state.mean_for(Direction.FRONT)
        self.assertEqual(front_mean.group_count, 3)
        self.assertEqual(front_mean.dataset.record.walking_speed_mps, 2.0)
        self.assertTrue(front_mean.dataset.samples)
        self.assertIsNotNone(front_mean.analysis.lock)
        self.assertIsNotNone(front_mean.analysis.unlock)
        self.assertLess(state.result.elapsed_ms, 600.0)

    def test_parameter_change_updates_every_group_and_total_summary(self) -> None:
        state = self.grouped_state()
        parameters = state.current_document.parameters
        changed = state.apply_updates(
            unlock_thresholds=parameters.unlock_thresholds,
            lock_thresholds=(-100, -100, -100, -100, -100),
        )

        self.assertEqual(changed.lock_summary.poor, 24)
        self.assertEqual(len(changed.lock_summary.untriggered_directions), 24)
        self.assertTrue(
            all("组" in item.label for item in changed.lock_summary.poor_directions)
        )

    def test_slot_fill_latest_time_and_full_overwrite_target(self) -> None:
        front = next(
            dataset
            for dataset in self.base.datasets
            if dataset.record.direction is Direction.FRONT
        )
        group_one = DirectionDataset(
            replace(
                front.record,
                group_index=1,
                recording_id="front-1",
                recorded_at="2026-08-01T00:00:01+00:00",
            ),
            front.samples,
        )
        group_three = DirectionDataset(
            replace(
                front.record,
                group_index=3,
                recording_id="front-3",
                recorded_at="2026-08-01T00:00:02+00:00",
            ),
            front.samples,
        )
        state = CalibrationUiState(
            self.base.original_document,
            (group_one, group_three),
        )

        self.assertEqual(state.next_capture_group(Direction.FRONT), 2)
        self.assertEqual(state.latest_group_for(Direction.FRONT), 3)

        group_two = DirectionDataset(
            replace(
                front.record,
                group_index=2,
                recording_id="front-2",
                recorded_at="2026-08-01T00:00:03+00:00",
            ),
            front.samples,
        )
        state.upsert_dataset(group_two)
        self.assertEqual(state.latest_group_for(Direction.FRONT), 2)
        self.assertEqual(state.next_capture_group(Direction.FRONT), 3)

        state.remove_dataset(Direction.FRONT, 2)
        self.assertEqual(state.next_capture_group(Direction.FRONT), 2)

    def test_latest_group_uses_capture_instant_across_timezones(self) -> None:
        front = next(
            dataset
            for dataset in self.base.datasets
            if dataset.record.direction is Direction.FRONT
        )
        state = CalibrationUiState(
            self.base.original_document,
            (
                DirectionDataset(
                    replace(
                        front.record,
                        group_index=1,
                        recording_id="front-timezone-1",
                        recorded_at="2026-08-01T01:00:00+01:00",
                    ),
                    front.samples,
                ),
                DirectionDataset(
                    replace(
                        front.record,
                        group_index=2,
                        recording_id="front-timezone-2",
                        recorded_at="2026-08-01T00:30:00Z",
                    ),
                    front.samples,
                ),
            ),
        )

        self.assertEqual(state.latest_group_for(Direction.FRONT), 2)

    def test_full_capture_replaces_third_group_only_after_upsert(self) -> None:
        state = self.grouped_state()
        old_third = state.dataset_for(Direction.FRONT, 3)
        self.assertEqual(state.next_capture_group(Direction.FRONT), 3)

        replacement = DirectionDataset(
            replace(
                old_third.record,
                recording_id="front-third-replacement",
                recorded_at="2026-08-01T00:01:00+00:00",
            ),
            old_third.samples,
        )
        self.assertEqual(
            state.dataset_for(Direction.FRONT, 3).record.recording_id,
            old_third.record.recording_id,
        )

        state.upsert_dataset(replacement)

        self.assertEqual(state.record_count, 24)
        self.assertEqual(
            state.dataset_for(Direction.FRONT, 3).record.recording_id,
            "front-third-replacement",
        )
        self.assertEqual(state.latest_group_for(Direction.FRONT), 3)

    def test_default_speed_only_changes_future_capture_default(self) -> None:
        state = self.grouped_state()
        original_speed = state.dataset_for(Direction.FRONT, 1).record.walking_speed_mps

        state.update_default_walking_speed(1.6)

        self.assertEqual(state.default_walking_speed_mps, 1.6)
        self.assertEqual(
            state.dataset_for(Direction.FRONT, 1).record.walking_speed_mps,
            original_speed,
        )


if __name__ == "__main__":
    unittest.main()
