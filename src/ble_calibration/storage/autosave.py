"""Periodic recovery snapshots for dirty in-memory projects."""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .models import StoredProject
from .repository import ProjectRepository

SnapshotProvider = Callable[[], Optional[StoredProject]]


class AutosaveWorker:
    """Save a recovery snapshot on a timer without blocking the UI thread."""

    def __init__(
        self,
        repository: ProjectRepository,
        snapshot_provider: SnapshotProvider,
        interval_s: float = 30.0,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.repository = repository
        self.snapshot_provider = snapshot_provider
        self.interval_s = interval_s
        self.last_error: Optional[BaseException] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self.last_error = None
        self._thread = threading.Thread(
            target=self._run,
            name="ble-calibration-autosave",
            daemon=True,
        )
        self._thread.start()

    def save_now(self) -> bool:
        stored = self.snapshot_provider()
        if stored is None:
            return False
        self.repository.save_recovery(stored)
        return True

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            try:
                self.save_now()
            except Exception as error:
                self.last_error = error

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_s)
        self._thread = None
