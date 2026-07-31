"""Windowed executable entry point used by PyInstaller."""

from __future__ import annotations

import sys

from ble_calibration.app.main import main


def run() -> int:
    arguments = sys.argv[1:]
    if not arguments:
        arguments = ["gui", "--live-zlg"]
    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(run())
