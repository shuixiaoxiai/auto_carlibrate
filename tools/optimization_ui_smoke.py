"""Offscreen acceptance check for the automatic optimization user workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

from ble_calibration.ui.demo import build_generated_demo_state
from ble_calibration.ui.main_window import CalibrationMainWindow


class GuiCallbackBridge(QObject):
    """Deliver worker notifications to the Qt GUI thread in every PySide build."""

    def __init__(
        self,
        result_callback,
        failure_callback,
        cancelled_callback,
        thread_finished_callback,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._result_callback = result_callback
        self._failure_callback = failure_callback
        self._cancelled_callback = cancelled_callback
        self._thread_finished_callback = thread_finished_callback

    def _dispatch(self, callback, *arguments) -> None:
        try:
            app = QApplication.instance()
            if app is None or QThread.currentThread() is not app.thread():
                raise RuntimeError("optimization smoke callback escaped the GUI thread")
            callback(*arguments)
        except BaseException as error:
            self._failure_callback(error)

    @Slot(object)
    def result(self, value) -> None:
        self._dispatch(self._result_callback, value)

    @Slot(str)
    def failed(self, message: str) -> None:
        self._dispatch(self._failure_callback, RuntimeError(message))

    @Slot()
    def cancelled(self) -> None:
        self._dispatch(self._cancelled_callback)

    @Slot()
    def thread_finished(self) -> None:
        self._dispatch(self._thread_finished_callback)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    state = build_generated_demo_state()
    original_hex = state.encoded_hex()
    window = CalibrationMainWindow(state, "自动调参验收")
    window.resize(1500, 960)
    window.show()
    outcome = {"ok": False}
    finished_result = {"value": None}

    def fail(error) -> None:
        if "error" not in outcome:
            outcome["error"] = f"{type(error).__name__}: {error}"
            outcome["traceback"] = traceback.format_exc()
        app.quit()

    def on_result(result) -> None:
        try:
            finished_result["value"] = result
            dialog = window._optimization_dialog
            assert dialog is not None
            assert result.can_apply
            assert dialog.apply_button.isEnabled()
            assert result.recommendation.metrics.lock_excellent_rate_percent >= 75.0
            assert result.recommendation.metrics.unlock_excellent_rate_percent >= 75.0
            assert result.recommendation.metrics.lock_poor == 0
            assert result.recommendation.metrics.unlock_poor == 0
            assert result.recommendation.metrics.ordering_violations == 0
            assert result.recommendation.metrics.near_unlock_violations == 0
            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                dialog.grab().save(str(args.screenshot))
        except BaseException as error:
            fail(error)

    def verify_after_thread() -> None:
        try:
            result = finished_result["value"]
            assert result is not None
            assert window._optimization_thread is None
            window._apply_automatic_recommendation(result)
            assert state.encoded_hex() != original_hex
            assert (
                state.current_document.parameters.lock_thresholds
                == result.recommendation.parameters.lock_thresholds
            )
            assert (
                state.current_document.parameters.unlock_thresholds
                == result.recommendation.parameters.unlock_thresholds
            )
            assert window.parameter_panel.optimize_button.isEnabled()
            outcome.update({
                "ok": True,
                "can_apply": result.can_apply,
                "evaluated_candidates": result.evaluated_candidates,
                "elapsed_ms": round(result.elapsed_ms, 3),
                "lock_rate_percent": (
                    result.recommendation.metrics.lock_excellent_rate_percent
                ),
                "unlock_rate_percent": (
                    result.recommendation.metrics.unlock_excellent_rate_percent
                ),
                "lock_poor": result.recommendation.metrics.lock_poor,
                "unlock_poor": result.recommendation.metrics.unlock_poor,
                "ordering_violations": (
                    result.recommendation.metrics.ordering_violations
                ),
                "near_unlock_violations": (
                    result.recommendation.metrics.near_unlock_violations
                ),
                "minimum_gap_db": min(
                    unlock - lock
                    for lock, unlock in zip(
                        result.recommendation.parameters.lock_thresholds,
                        result.recommendation.parameters.unlock_thresholds,
                    )
                    if lock != 0 and unlock != 0
                ),
                "robustness": (
                    f"{result.robustness_passed}/{result.robustness_total}"
                ),
                "applied_to_what_if": True,
                "vehicle_write": False,
            })
        except BaseException as error:
            fail(error)
            return
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(outcome, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        window.close_for_automation()
        app.quit()

    bridge = GuiCallbackBridge(
        on_result,
        fail,
        lambda: fail(RuntimeError("optimizer unexpectedly cancelled")),
        verify_after_thread,
        window,
    )

    def start() -> None:
        try:
            assert window.parameter_panel.optimize_button.isEnabled()
            window._show_automatic_optimization()
            dialog = window._optimization_dialog
            assert dialog is not None
            assert dialog.start_button.isEnabled()
            window._start_automatic_optimization(False)
            worker = window._optimization_worker
            thread = window._optimization_thread
            assert worker is not None and thread is not None
            worker.finished.connect(
                bridge.result,
                Qt.ConnectionType.QueuedConnection,
            )
            worker.failed.connect(
                bridge.failed,
                Qt.ConnectionType.QueuedConnection,
            )
            worker.cancelled.connect(
                bridge.cancelled,
                Qt.ConnectionType.QueuedConnection,
            )
            thread.finished.connect(
                bridge.thread_finished,
                Qt.ConnectionType.QueuedConnection,
            )
        except BaseException as error:
            fail(error)

    def timeout() -> None:
        if outcome["ok"]:
            return
        if window._optimization_worker is not None:
            window._optimization_worker.request_cancel()
        fail(TimeoutError("automatic optimization smoke test timed out"))

    QTimer.singleShot(100, start)
    QTimer.singleShot(int(args.timeout_seconds * 1000), timeout)
    app.exec()
    if not outcome["ok"]:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(outcome, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
