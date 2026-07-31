"""Qt application bootstrap and deterministic Mock workspace construction."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..config import default_user_data_dir
from ..session import ManualCaptureCoordinator
from .demo import build_file_demo_state, build_generated_demo_state
from .main_window import CalibrationMainWindow
from .manual_demo import build_manual_demo
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
    replay_speed: float = 10.0,
    database_path: Optional[Path] = None,
    project_id: Optional[str] = None,
    project_name: str = "新建八方向标定",
    argv: Optional[Sequence[str]] = None,
) -> int:
    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    manual_capture = None
    source_factory = None
    workspace = None
    resolved_database = database_path or (
        default_user_data_dir() / "projects.sqlite3"
    )
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
        workspace=workspace,
    )
    window.show()
    if parameters_hidden:
        window.set_parameters_visible(False)

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
