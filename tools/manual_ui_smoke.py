"""Automated operator workflow over the manual Mock Qt interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ble_calibration.domain import Direction, DirectionStatus, EventType
from ble_calibration.session import ManualCaptureCoordinator
from ble_calibration.ui.main_window import CalibrationMainWindow
from ble_calibration.ui.manual_demo import build_manual_demo
from ble_calibration.ui.project_workspace import ProjectWorkspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    state, provider = build_manual_demo(replay_speed=0)
    coordinator = ManualCaptureCoordinator()
    temporary = tempfile.TemporaryDirectory()
    workspace = ProjectWorkspace.create(
        Path(temporary.name) / "projects.sqlite3",
        "手动工作流自动验收",
    )
    window = CalibrationMainWindow(
        state,
        "手动工作流自动验收",
        manual_capture=coordinator,
        source_factory=provider.source_for,
        workspace=workspace,
    )
    window.resize(args.width, args.height)
    window.show()
    window.set_parameters_visible(False)
    outcome = {"ok": False}
    directions = list(Direction)
    current_index = 0

    def fail(error: BaseException) -> None:
        outcome["error"] = f"{type(error).__name__}: {error}"
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        window._dirty = False
        window.close()
        app.quit()

    def begin_next() -> None:
        nonlocal current_index
        try:
            if current_index >= len(directions):
                verify_all_directions()
                return
            direction = directions[current_index]
            window._start_manual_recording(direction, 1.0)
            wait_for_source()
        except BaseException as error:
            fail(error)

    def wait_for_source() -> None:
        nonlocal current_index
        try:
            snapshot = coordinator.snapshot()
            window._poll_manual_capture()
            if not snapshot.source_finished:
                QTimer.singleShot(10, wait_for_source)
                return
            direction = directions[current_index]
            hint = provider.distance_hint(direction)
            window.recording_bar.lock_distance.setValue(hint.lock_distance_m)
            window.recording_bar.unlock_distance.setValue(hint.unlock_distance_m)
            window._finish_manual_recording(
                hint.lock_distance_m,
                hint.unlock_distance_m,
            )
            current_index += 1
            QTimer.singleShot(0, begin_next)
        except BaseException as error:
            fail(error)

    def verify_all_directions() -> None:
        try:
            assert len(state.datasets) == 8
            assert all(
                dataset.record.status is DirectionStatus.COMPLETE
                for dataset in state.datasets
            )
            assert all(
                dataset.record.event(EventType.UNLOCK) is not None
                and dataset.record.end_timestamp
                > dataset.record.event(EventType.UNLOCK).timestamp
                for dataset in state.datasets
            )
            assert all(
                len(window.cards[direction]._curves) == 5
                for direction in directions
            )
            assert state.result.lock_summary.total == 8
            assert state.result.unlock_summary.total == 8
            assert state.result.lock_summary.excellent == 5
            assert state.result.lock_summary.good == 3
            assert state.result.unlock_summary.excellent == 5
            assert state.result.unlock_summary.good == 3
            window._complete_test()
            assert not window._dirty
            reopened_workspace, reopened_state = ProjectWorkspace.load(
                workspace.database_path,
                workspace.project_id,
            )
            window._replace_workspace(reopened_workspace, reopened_state)
            assert len(window.state.datasets) == 8
            assert all(
                reopened.samples == original.samples
                for reopened, original in zip(window.state.datasets, state.datasets)
            )

            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            outcome.update({
                "ok": True,
                "saved_directions": len(state.datasets),
                "complete_directions": sum(
                    dataset.record.status is DirectionStatus.COMPLETE
                    for dataset in state.datasets
                ),
                "total_samples": sum(
                    dataset.record.sample_count for dataset in state.datasets
                ),
                "post_unlock_samples_preserved": True,
                "project_reopened": True,
                "lock_excellent_rate": (
                    window.state.result.lock_summary.excellent_rate_percent
                ),
                "unlock_excellent_rate": (
                    window.state.result.unlock_summary.excellent_rate_percent
                ),
            })
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            window.close()
            app.quit()
        except BaseException as error:
            fail(error)

    QTimer.singleShot(100, begin_next)
    app.exec()
    temporary.cleanup()
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
