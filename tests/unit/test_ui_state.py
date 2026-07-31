import unittest

from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
)
from ble_calibration.ui import build_generated_demo_state


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


if __name__ == "__main__":
    unittest.main()
