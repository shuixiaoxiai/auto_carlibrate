"""Offscreen eight-direction acceptance for the persistent live-CAN UI path."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from queue import Empty, Queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ble_calibration.can.source import CanSource, SourceState
from ble_calibration.config import AppSettings, CanSettings, load_settings, save_settings
from ble_calibration.domain import Direction, DirectionStatus, EventType
from ble_calibration.mock.generator import MockConfig, generate_mock_session
from ble_calibration.session import ManualCaptureCoordinator
from ble_calibration.ui.main_window import CalibrationMainWindow
from ble_calibration.ui.manual_demo import build_empty_state
from ble_calibration.ui.project_workspace import ProjectWorkspace


class PushLiveSource(CanSource):
    def __init__(self) -> None:
        super().__init__()
        self._frames = Queue()
        self.stop_count = 0

    def connect(self) -> None:
        self._set_state(SourceState.CONNECTED, "模拟 ZLG 已连接")

    def push(self, frames) -> None:
        for frame in frames:
            self._frames.put(frame)

    def recv(self, timeout: float = 1.0):
        if self.state is SourceState.CONNECTED:
            self._set_state(SourceState.RUNNING, "模拟 ZLG 正在接收")
        if self.state in (SourceState.STOPPED, SourceState.ERROR):
            return None
        try:
            return self._frames.get(timeout=timeout)
        except Empty:
            return None

    def stop(self) -> None:
        if self.state is SourceState.STOPPED:
            return
        self.stop_count += 1
        self._set_state(SourceState.STOPPED, "模拟 ZLG 已断开")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    state = build_empty_state()
    source = PushLiveSource()
    coordinator = ManualCaptureCoordinator()
    frames, manifest = generate_mock_session(MockConfig(seed=20260730))
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    settings_path = root / "settings.json"
    workspace = ProjectWorkspace.create(
        root / "projects.sqlite3",
        "ZLG 持久连接自动验收",
        capture_format="jsonl",
    )

    def persist(settings: CanSettings) -> None:
        save_settings(settings_path, AppSettings(can=settings))

    window = CalibrationMainWindow(
        state,
        workspace.name,
        manual_capture=coordinator,
        live_source_factory=lambda settings: source,
        can_settings=CanSettings(library_path=r"D:\zlg\library"),
        settings_saver=persist,
        workspace=workspace,
    )
    window.resize(args.width, args.height)
    window.show()
    window.set_parameters_visible(False)
    outcome = {"ok": False}
    direction_items = list(manifest["directions"])
    current_index = 0

    def fail(error: BaseException) -> None:
        outcome["error"] = f"{type(error).__name__}: {error}"
        outcome["traceback"] = traceback.format_exc()
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        window._dirty = False
        window.close_for_automation()
        app.quit()

    def connect_device() -> None:
        try:
            window._connect_live_device(window.device_panel.settings())
            wait_connected()
        except BaseException as error:
            fail(error)

    def wait_connected() -> None:
        try:
            window._poll_manual_capture()
            if not coordinator.is_connected:
                snapshot = coordinator.snapshot()
                if snapshot.error:
                    raise RuntimeError(snapshot.error)
                QTimer.singleShot(5, wait_connected)
                return
            assert window.recording_bar.start_button.isEnabled()
            begin_next()
        except BaseException as error:
            fail(error)

    def begin_next() -> None:
        nonlocal current_index
        try:
            if current_index >= len(direction_items):
                verify_complete()
                return
            item = direction_items[current_index]
            direction = Direction.from_label(item["name"])
            direction_frames = tuple(
                frame
                for frame in frames
                if item["start_time"]
                <= frame.timestamp
                <= item["end_time"] + 1e-9
            )
            window._start_manual_recording(direction, 1.0)
            assert coordinator.is_active
            source.push(direction_frames)
            wait_direction(item, direction_frames[-1].timestamp)
        except BaseException as error:
            fail(error)

    def wait_direction(item, final_timestamp: float) -> None:
        nonlocal current_index
        try:
            window._poll_manual_capture()
            snapshot = coordinator.snapshot()
            record = None if snapshot.dataset is None else snapshot.dataset.record
            if (
                record is None
                or record.end_timestamp is None
                or record.end_timestamp + 1e-9 < final_timestamp
            ):
                QTimer.singleShot(
                    5,
                    lambda: wait_direction(item, final_timestamp),
                )
                return
            window.recording_bar.lock_distance.setValue(
                item["lock_distance_m"]
            )
            window.recording_bar.unlock_distance.setValue(
                item["unlock_distance_m"]
            )
            window._finish_manual_recording(
                item["lock_distance_m"],
                item["unlock_distance_m"],
            )
            assert coordinator.is_connected
            current_index += 1
            QTimer.singleShot(0, begin_next)
        except BaseException as error:
            fail(error)

    def verify_complete() -> None:
        try:
            assert len(state.datasets) == 8
            assert all(
                dataset.record.status is DirectionStatus.COMPLETE
                and dataset.record.event(EventType.LOCK) is not None
                and dataset.record.event(EventType.UNLOCK) is not None
                for dataset in state.datasets
            )
            assert coordinator.is_connected
            assert source.stop_count == 0
            window._complete_test()
            assert not window._dirty
            window._disconnect_live_device()
            assert source.stop_count == 1
            assert not coordinator.is_connected
            saved_settings = load_settings(settings_path)
            assert saved_settings.can.library_path == r"D:\zlg\library"
            assert all(
                Path(dataset.record.raw_data_file).exists()
                for dataset in state.datasets
            )
            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            outcome.update({
                "ok": True,
                "direction_count": len(state.datasets),
                "complete_count": sum(
                    dataset.record.status is DirectionStatus.COMPLETE
                    for dataset in state.datasets
                ),
                "single_device_connection": source.stop_count == 1,
                "settings_persisted": True,
                "operator_start_finish_directions": len(state.datasets),
                "lock_distance_inputs": sum(
                    dataset.record.actual_lock_distance_m is not None
                    for dataset in state.datasets
                ),
                "unlock_distance_inputs": sum(
                    dataset.record.actual_unlock_distance_m is not None
                    for dataset in state.datasets
                ),
                "raw_direction_files": sum(
                    Path(dataset.record.raw_data_file).exists()
                    for dataset in state.datasets
                ),
                "lock_summary_total": state.result.lock_summary.total,
                "unlock_summary_total": state.result.unlock_summary.total,
            })
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            window.close_for_automation()
            app.quit()
        except BaseException as error:
            fail(error)

    QTimer.singleShot(100, connect_device)
    app.exec()
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    temporary.cleanup()
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
