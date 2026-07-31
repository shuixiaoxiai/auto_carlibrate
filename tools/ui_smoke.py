"""Offscreen Qt acceptance check for the eight-direction What-if workspace."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import QApplication

from ble_calibration.ui.demo import build_generated_demo_state
from ble_calibration.ui.main_window import CalibrationMainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--max-refresh-ms", type=float, default=200.0)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    state = build_generated_demo_state()
    window = CalibrationMainWindow(state, "Qt 自动验收")
    window.resize(args.width, args.height)
    window.show()
    outcome = {"ok": False}

    def verify() -> None:
        try:
            assert len(window.cards) == 8
            assert all(len(card._curves) == 5 for card in window.cards.values())
            baseline = state.result
            assert baseline.lock_summary.excellent == 5
            assert baseline.unlock_summary.excellent == 5

            blockers = [
                QSignalBlocker(spin)
                for spin in window.parameter_panel.threshold_spins["lock"]
            ]
            for spin in window.parameter_panel.threshold_spins["lock"]:
                spin.setValue(-100)
            visible_started = time.perf_counter()
            window._apply_parameter_edits()
            app.processEvents()
            visible_refresh_ms = (
                (time.perf_counter() - visible_started) * 1000.0
                + window.parameter_timer.interval()
            )
            del blockers

            changed = state.result
            assert changed.lock_summary.excellent == 0
            assert changed.lock_summary.good == 0
            assert changed.lock_summary.poor == 8
            assert len(changed.lock_summary.untriggered_directions) == 8
            assert changed.unlock_summary.excellent == 0
            assert changed.unlock_summary.good == 0
            assert changed.unlock_summary.poor == 8
            assert len(changed.unlock_summary.untriggered_directions) == 8
            summary_panel = window.parameter_panel.summary_panel
            assert summary_panel.lock_card.poor_label.text() == "差 8"
            assert summary_panel.unlock_card.poor_label.text() == "差 8"
            assert window.last_what_if_refresh_ms is not None
            assert visible_refresh_ms < args.max_refresh_ms

            window.set_parameters_visible(False)
            assert not window.parameter_panel.isVisible()
            assert window.show_parameters_bottom.isVisible()
            window.set_parameters_visible(True)
            assert window.parameter_panel.isVisible()

            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            outcome.update({
                "ok": True,
                "direction_count": len(window.cards),
                "curve_count": sum(
                    len(card._curves) for card in window.cards.values()
                ),
                "core_and_widgets_ms": round(
                    window.last_what_if_refresh_ms,
                    3,
                ),
                "debounce_to_painted_ms": round(visible_refresh_ms, 3),
                "lock_summary": {
                    "excellent": changed.lock_summary.excellent,
                    "good": changed.lock_summary.good,
                    "poor": changed.lock_summary.poor,
                    "untriggered": len(
                        changed.lock_summary.untriggered_directions
                    ),
                },
                "unlock_summary": {
                    "excellent": changed.unlock_summary.excellent,
                    "good": changed.unlock_summary.good,
                    "poor": changed.unlock_summary.poor,
                    "untriggered": len(
                        changed.unlock_summary.untriggered_directions
                    ),
                },
            })
        except BaseException as error:
            outcome["error"] = f"{type(error).__name__}: {error}"
        finally:
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            app.quit()

    QTimer.singleShot(100, verify)
    app.exec()
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
