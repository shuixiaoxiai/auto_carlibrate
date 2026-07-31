"""Compatibility CLI for :mod:`ble_calibration.mock.generator`."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from ble_calibration.mock.generator import *  # noqa: F401,F403
    from ble_calibration.mock.generator import main
except ModuleNotFoundError:
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from ble_calibration.mock.generator import *  # noqa: F401,F403
    from ble_calibration.mock.generator import main


if __name__ == "__main__":
    raise SystemExit(main())
