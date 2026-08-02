"""Offscreen Qt acceptance check for the eight-direction What-if workspace."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import QApplication

from ble_calibration.analysis import DirectionDataset
from ble_calibration.cloud import decode_cloud
from ble_calibration.domain import Direction
from ble_calibration.ui.demo import build_generated_demo_state
from ble_calibration.ui.main_window import CalibrationMainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-core-ms", type=float, default=600.0)
    parser.add_argument("--max-refresh-ms", type=float, default=1000.0)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    state = build_generated_demo_state()
    first_groups = tuple(state.datasets)
    for group_index in (2, 3):
        for dataset in first_groups:
            direction = dataset.record.direction
            state.upsert_dataset(
                DirectionDataset(
                    replace(
                        dataset.record,
                        group_index=group_index,
                        recording_id=f"ui-{direction.value}-{group_index}",
                        recorded_at=(
                            "2099-08-01T00:"
                            f"{group_index:02d}:{direction.index:02d}+00:00"
                        ),
                    ),
                    dataset.samples,
                )
            )
    window = CalibrationMainWindow(state, "Qt 自动验收")
    window.resize(args.width, args.height)
    window.show()
    outcome = {"ok": False}

    def verify() -> None:
        try:
            assert len(window.cards) == 8
            assert all(len(card._curves) == 5 for card in window.cards.values())
            assert all(card.selected_view == 3 for card in window.cards.values())
            assert all(
                button.isEnabled()
                for card in window.cards.values()
                for button in card.group_buttons.values()
            )
            assert all(card.mean_button.isEnabled() for card in window.cards.values())
            front_card = window.cards[Direction.FRONT]
            front_card.mean_button.click()
            app.processEvents()
            assert front_card.selected_view == 0
            assert front_card._mean_context == (3, 3, 3)
            assert len(front_card._curves) == 5
            assert not front_card.walking_speed.isEnabled()
            front_card.group_buttons[1].click()
            app.processEvents()
            assert front_card.selected_view == 1
            right_front_card = window.cards[Direction.FRONT_RIGHT]
            right_front_card.group_buttons[2].click()
            app.processEvents()
            assert front_card.selected_view == 1
            assert right_front_card.selected_view == 2
            assert all(
                window.cards[direction].selected_view == 3
                for direction in Direction
                if direction not in (Direction.FRONT, Direction.FRONT_RIGHT)
            )
            assert front_card.walking_speed.isEnabled()
            baseline = state.result
            assert baseline.lock_summary.total == 24
            assert baseline.unlock_summary.total == 24
            original_hex = state.encoded_hex()

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
            threshold_core_ms = changed.elapsed_ms
            assert threshold_core_ms < args.max_core_ms
            assert changed.lock_summary.excellent == 0
            assert changed.lock_summary.good == 0
            assert changed.lock_summary.poor == 24
            assert len(changed.lock_summary.untriggered_directions) == 24
            assert changed.unlock_summary.excellent == 0
            assert changed.unlock_summary.good == 0
            assert changed.unlock_summary.poor == 24
            assert len(changed.unlock_summary.untriggered_directions) == 24
            summary_panel = window.parameter_panel.summary_panel
            assert summary_panel.lock_card.poor_label.text() == "差 24"
            assert summary_panel.unlock_card.poor_label.text() == "差 24"
            assert window.last_what_if_refresh_ms is not None
            assert visible_refresh_ms < args.max_refresh_ms
            threshold_refresh_ms = window.last_what_if_refresh_ms
            threshold_hex = state.encoded_hex()
            assert threshold_hex != original_hex
            decoded_threshold = decode_cloud(threshold_hex)
            assert decoded_threshold.parameters.lock_thresholds == (
                -100,
                -100,
                -100,
                -100,
                -100,
            )

            window.set_parameters_visible(False)
            assert not window.parameter_panel.isVisible()
            assert window.show_parameters_bottom.isVisible()
            window.set_parameters_visible(True)
            assert window.parameter_panel.isVisible()

            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))

            window._restore_parameters()
            quick_lock_spin = window.parameter_panel.strategy_spins[
                "quickLock"
            ]["weakFront"]
            strategy_value = 2 if quick_lock_spin.value() != 2 else 3
            strategy_blocker = QSignalBlocker(quick_lock_spin)
            quick_lock_spin.setValue(strategy_value)
            strategy_started = time.perf_counter()
            window._apply_parameter_edits()
            app.processEvents()
            strategy_core_ms = state.result.elapsed_ms
            strategy_visible_ms = (
                (time.perf_counter() - strategy_started) * 1000.0
                + window.parameter_timer.interval()
            )
            del strategy_blocker
            assert strategy_core_ms < args.max_core_ms
            assert strategy_visible_ms < args.max_refresh_ms
            assert len(state.result.directions) == 8
            strategy_hex = state.encoded_hex()
            assert strategy_hex != original_hex
            decoded_strategy = decode_cloud(strategy_hex)
            assert (
                decoded_strategy.parameters.quick_lock["weakFront"]
                == strategy_value
            )
            window._restore_parameters()
            assert state.encoded_hex() == original_hex
            assert state.result.lock_summary.total == 24
            assert state.result.unlock_summary.total == 24
            outcome.update({
                "ok": True,
                "direction_count": len(window.cards),
                "record_count": state.record_count,
                "curve_count": sum(
                    len(card._curves) for card in window.cards.values()
                ),
                "core_and_widgets_ms": round(
                    threshold_refresh_ms,
                    3,
                ),
                "threshold_core_recompute_ms": round(
                    threshold_core_ms,
                    3,
                ),
                "strategy_core_recompute_ms": round(
                    strategy_core_ms,
                    3,
                ),
                "max_core_recompute_ms": round(
                    max(threshold_core_ms, strategy_core_ms),
                    3,
                ),
                "debounce_to_painted_ms": round(visible_refresh_ms, 3),
                "threshold_debounce_to_painted_ms": round(
                    visible_refresh_ms,
                    3,
                ),
                "strategy_debounce_to_painted_ms": round(
                    strategy_visible_ms,
                    3,
                ),
                "cloud_codec_round_trip": True,
                "one_click_restore": True,
                "group_switch_and_mean": True,
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
            outcome["traceback"] = traceback.format_exc()
        finally:
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            app.quit()

    QTimer.singleShot(100, verify)
    app.exec()
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
