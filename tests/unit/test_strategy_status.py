import unittest

from ble_calibration.cloud.models import CloudParameters
from ble_calibration.ui.strategy_status import StrategyActivation, strategy_statuses


class StrategyStatusTests(unittest.TestCase):
    def parameters(self, **updates: object) -> CloudParameters:
        values = {
            "unlock_thresholds": (-67, -66, -68, -67, -67),
            "lock_thresholds": (-78, -77, -79, -78, -78),
        }
        values.update(updates)
        return CloudParameters(**values)  # type: ignore[arg-type]

    def test_reports_strategy_specific_enablement_gates(self) -> None:
        statuses = strategy_statuses(
            self.parameters(
                mst_unlock=(0, -70, 0, 0, 0),
                quick_lock={"weakFront": 2, "strongMst": 1},
                quick_unlock={"unlockTime": 1},
                mst_than_slave={"diff": 1},
                bevel_angle={"offsetRFR": 2},
            )
        )

        self.assertTrue(all(status.activation is StrategyActivation.ENABLED for status in statuses.values()))

    def test_reports_disabled_when_strategy_gate_is_zero(self) -> None:
        statuses = strategy_statuses(
            self.parameters(
                mst_unlock=(0, 0, 0, 0, 0),
                quick_lock={"weakFront": 0, "strongMst": 2},
                quick_unlock={"unlockTime": 0, "frontToFr": 4},
                mst_than_slave={"diff": 0},
                bevel_angle={"offsetRFR": 0},
            )
        )

        self.assertTrue(all(status.activation is StrategyActivation.DISABLED for status in statuses.values()))

    def test_reports_invalid_bevel_configuration(self) -> None:
        statuses = strategy_statuses(
            self.parameters(bevel_angle={"offsetRFR": 12})
        )

        self.assertEqual(statuses["bevelAngle"].activation, StrategyActivation.INVALID)
        self.assertIn("offsetRFR", statuses["bevelAngle"].detail)
