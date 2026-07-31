"""Compatibility wrapper for the canonical CAN protocol module.

Existing scripts may continue importing ``tools.can_protocol`` or running from
the repository without installing the package first.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from ble_calibration.can.protocol import *  # noqa: F401,F403
except ModuleNotFoundError:
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from ble_calibration.can.protocol import *  # noqa: F401,F403
