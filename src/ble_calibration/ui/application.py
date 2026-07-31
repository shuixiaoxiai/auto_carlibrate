"""Qt application bootstrap and deterministic Mock workspace construction."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..can import ZlgCanSource
from ..config import (
    CanSettings,
    default_user_data_dir,
    load_settings,
    save_settings,
)
from ..session import ManualCaptureCoordinator
from .demo import build_file_demo_state, build_generated_demo_state
from .main_window import CalibrationMainWindow
from .manual_demo import build_empty_state, build_manual_demo
from .project_workspace import ProjectWorkspace


def run_ui(
    *,
    frame_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    seed: int = 20260730,
    screenshot_path: Optional[Path] = None,
    quit_after_ms: Optional[int] = None,
    parameters_hidden: bool = False,
    manual_mock: bool = False,
    live_zlg: bool = False,
    replay_speed: float = 10.0,
    database_path: Optional[Path] = None,
    settings_path: Optional[Path] = None,
    project_id: Optional[str] = None,
    project_name: str = "新建八方向标定",
    automation_report_path: Optional[Path] = None,
    argv: Optional[Sequence[str]] = None,
) -> int:
    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    manual_capture = None
    source_factory = None
    live_source_factory = None
    workspace = None
    resolved_database = database_path or (
        default_user_data_dir() / "projects.sqlite3"
    )
    resolved_settings = settings_path or (
        default_user_data_dir() / "settings.json"
    )
    app_settings = load_settings(resolved_settings)

    def persist_can_settings(can_settings: CanSettings) -> None:
        nonlocal app_settings
        app_settings = replace(app_settings, can=can_settings)
        save_settings(resolved_settings, app_settings)

    if project_id is not None:
        if frame_path is not None:
            raise ValueError("project_id cannot be combined with frame_path")
        workspace, state = ProjectWorkspace.load(
            resolved_database,
            project_id,
        )
        project_name = workspace.name
        if manual_mock:
            _, provider = build_manual_demo(seed, replay_speed)
            manual_capture = ManualCaptureCoordinator()
            source_factory = provider.source_for
        elif live_zlg:
            manual_capture = ManualCaptureCoordinator()
            live_source_factory = ZlgCanSource
            workspace.capture_channel = app_settings.can.channel
    elif manual_mock:
        if frame_path is not None:
            raise ValueError("manual Mock mode cannot be combined with frame_path")
        state, provider = build_manual_demo(seed, replay_speed)
        manual_capture = ManualCaptureCoordinator()
        source_factory = provider.source_for
        workspace = ProjectWorkspace.create(
            resolved_database,
            project_name,
        )
    elif live_zlg:
        if frame_path is not None:
            raise ValueError("live ZLG mode cannot be combined with frame_path")
        state = build_empty_state()
        manual_capture = ManualCaptureCoordinator()
        live_source_factory = ZlgCanSource
        workspace = ProjectWorkspace.create(
            resolved_database,
            project_name,
            capture_format="blf",
            capture_channel=app_settings.can.channel,
        )
    elif frame_path is None:
        state = build_generated_demo_state(seed)
        project_name = f"Mock 八方向 · seed {seed}"
    else:
        if manifest_path is None:
            raise ValueError("manifest_path is required with frame_path")
        state = build_file_demo_state(frame_path, manifest_path)
        project_name = frame_path.stem

    window = CalibrationMainWindow(
        state,
        project_name,
        manual_capture=manual_capture,
        source_factory=source_factory,
        live_source_factory=live_source_factory,
        can_settings=app_settings.can,
        settings_saver=persist_can_settings,
        workspace=workspace,
    )
    window.show()
    if parameters_hidden:
        window.set_parameters_visible(False)

    if automation_report_path is not None:
        def exercise_what_if() -> None:
            for spin in window.parameter_panel.threshold_spins["lock"]:
                spin.setValue(-100)
            window._apply_parameter_edits()
            result = state.result
            report = {
                "direction_count": len(result.directions),
                "refresh_ms": window.last_what_if_refresh_ms,
                "lock_summary": {
                    "total": result.lock_summary.total,
                    "excellent": result.lock_summary.excellent,
                    "good": result.lock_summary.good,
                    "poor": result.lock_summary.poor,
                    "untriggered": len(
                        result.lock_summary.untriggered_directions
                    ),
                },
                "unlock_summary": {
                    "total": result.unlock_summary.total,
                    "excellent": result.unlock_summary.excellent,
                    "good": result.unlock_summary.good,
                    "poor": result.unlock_summary.poor,
                    "untriggered": len(
                        result.unlock_summary.untriggered_directions
                    ),
                },
                "summary_widgets": {
                    "lock_excellent": (
                        window.parameter_panel.summary_panel.lock_card
                        .excellent_label.text()
                    ),
                    "lock_good": (
                        window.parameter_panel.summary_panel.lock_card
                        .good_label.text()
                    ),
                    "lock_poor": (
                        window.parameter_panel.summary_panel.lock_card
                        .poor_label.text()
                    ),
                    "unlock_excellent": (
                        window.parameter_panel.summary_panel.unlock_card
                        .excellent_label.text()
                    ),
                    "unlock_good": (
                        window.parameter_panel.summary_panel.unlock_card
                        .good_label.text()
                    ),
                    "unlock_poor": (
                        window.parameter_panel.summary_panel.unlock_card
                        .poor_label.text()
                    ),
                },
            }
            automation_report_path.parent.mkdir(parents=True, exist_ok=True)
            automation_report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        QTimer.singleShot(250, exercise_what_if)

    def automated_exit() -> None:
        window.close_for_automation()
        app.quit()

    if screenshot_path is not None:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)

        def save_screenshot() -> None:
            window.grab().save(str(screenshot_path))
            if quit_after_ms is None:
                automated_exit()

        QTimer.singleShot(700, save_screenshot)
    if quit_after_ms is not None:
        QTimer.singleShot(quit_after_ms, automated_exit)
    return app.exec()
