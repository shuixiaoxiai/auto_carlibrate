"""Qt application bootstrap and deterministic Mock workspace construction."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .demo import build_file_demo_state, build_generated_demo_state
from .main_window import CalibrationMainWindow


def run_ui(
    *,
    frame_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    seed: int = 20260730,
    screenshot_path: Optional[Path] = None,
    quit_after_ms: Optional[int] = None,
    parameters_hidden: bool = False,
    argv: Optional[Sequence[str]] = None,
) -> int:
    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    if frame_path is None:
        state = build_generated_demo_state(seed)
        project_name = f"Mock 八方向 · seed {seed}"
    else:
        if manifest_path is None:
            raise ValueError("manifest_path is required with frame_path")
        state = build_file_demo_state(frame_path, manifest_path)
        project_name = frame_path.stem

    window = CalibrationMainWindow(state, project_name)
    window.show()
    if parameters_hidden:
        window.set_parameters_visible(False)

    if screenshot_path is not None:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)

        def save_screenshot() -> None:
            window.grab().save(str(screenshot_path))
            if quit_after_ms is None:
                app.quit()

        QTimer.singleShot(700, save_screenshot)
    if quit_after_ms is not None:
        QTimer.singleShot(quit_after_ms, app.quit)
    return app.exec()
