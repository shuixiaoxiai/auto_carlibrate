import json
import tempfile
import unittest
from pathlib import Path

from ble_calibration.config import (
    AppSettings,
    CanSettings,
    RuntimeSettings,
    load_settings,
    save_settings,
)


class SettingsTests(unittest.TestCase):
    def test_missing_settings_uses_valid_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_settings(Path(temp_dir) / "missing.json")
        self.assertEqual(settings.can.interface, "zlgcan")
        self.assertEqual(settings.runtime.recompute_budget_ms, 200)

    def test_settings_round_trip(self) -> None:
        settings = AppSettings(
            can=CanSettings(channel=1, library_path=r"D:\zlg\library"),
            runtime=RuntimeSettings(default_walking_speed_mps=1.2),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_settings(path, settings)
            loaded = load_settings(path)
            self.assertEqual(loaded, settings)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeSettings(recompute_budget_ms=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
