import tempfile
import unittest
from pathlib import Path

from ble_calibration.domain import Direction
from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
)
from ble_calibration.session import ManualCaptureCoordinator
from ble_calibration.ui import build_generated_demo_state
from ble_calibration.ui.manual_demo import build_manual_demo
from ble_calibration.ui.project_workspace import ProjectWorkspace


class CalibrationUiStateTests(unittest.TestCase):
    def test_parameter_update_recomputes_directions_and_summaries_together(self) -> None:
        state = build_generated_demo_state(seed=20260730)
        original_hex = state.encoded_hex()
        changed = state.apply_updates(
            unlock_thresholds=REFERENCE_UNLOCK_THRESHOLDS,
            lock_thresholds=(-100, -100, -100, -100, -100),
            mst_unlock=state.current_document.parameters.mst_unlock,
            strategy_updates={
                "quickLock": {
                    field: value
                    for field, value in state.current_document.parameters.quick_lock.items()
                },
                "quickUnlock": {
                    field: value
                    for field, value in state.current_document.parameters.quick_unlock.items()
                },
                "mstThanSlave": {
                    field: value
                    for field, value in state.current_document.parameters.mst_than_slave.items()
                },
                "bevelAngle": {
                    field: value
                    for field, value in state.current_document.parameters.bevel_angle.items()
                },
            },
        )
        self.assertEqual(len(changed.directions), 8)
        self.assertEqual(changed.lock_summary.poor, 8)
        self.assertEqual(len(changed.lock_summary.untriggered_directions), 8)
        self.assertNotEqual(state.encoded_hex(), original_hex)

        restored = state.restore()
        self.assertEqual(state.encoded_hex(), original_hex)
        self.assertTrue(state.using_original)
        self.assertEqual(restored.lock_summary.excellent, 5)
        self.assertEqual(restored.unlock_summary.excellent, 5)

    def test_manual_capture_project_save_and_reopen_rebuilds_same_result(self) -> None:
        state, provider = build_manual_demo(replay_speed=0)
        coordinator = ManualCaptureCoordinator()
        hint = provider.distance_hint(Direction.FRONT)
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = ProjectWorkspace.create(
                Path(temp_dir) / "projects.sqlite3",
                "手动项目",
            )
            recorder, raw_path = workspace.capture_target(Direction.FRONT)
            coordinator.begin(
                Direction.FRONT,
                provider.source_for(Direction.FRONT),
                raw_data_file=raw_path,
                recorder=recorder,
            )
            self.assertTrue(coordinator.wait_source_finished(5.0))
            dataset = coordinator.finish(
                lock_distance_m=hint.lock_distance_m,
                unlock_distance_m=hint.unlock_distance_m,
            )
            state.upsert_dataset(dataset)
            parameters = state.current_document.parameters
            state.apply_updates(
                unlock_thresholds=parameters.unlock_thresholds,
                lock_thresholds=tuple(
                    value - 1 for value in parameters.lock_thresholds
                ),
            )
            saved_hex = state.encoded_hex()
            workspace.save(state)

            reopened_workspace, reopened = ProjectWorkspace.load(
                workspace.database_path,
                workspace.project_id,
            )

        self.assertEqual(reopened_workspace.name, "手动项目")
        self.assertEqual(len(reopened.datasets), 1)
        self.assertFalse(reopened.using_original)
        self.assertEqual(reopened.encoded_hex(), saved_hex)
        self.assertEqual(reopened.datasets[0].samples, dataset.samples)
        self.assertEqual(reopened.result.directions, state.result.directions)
        self.assertEqual(reopened.result.lock_summary, state.result.lock_summary)
        self.assertEqual(reopened.result.unlock_summary, state.result.unlock_summary)


if __name__ == "__main__":
    unittest.main()
