"""In-memory CAN source for interactive hardware-free recording tests."""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Optional, Sequence

from ..domain.models import CanFrame
from .source import CanSource, CanSourceError, SourceState


class MemoryCanSource(CanSource):
    def __init__(
        self,
        frames: Sequence[CanFrame],
        speed: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        if not math.isfinite(speed) or speed < 0:
            raise ValueError("speed must be a finite non-negative number")
        self._input_frames = tuple(frames)
        self.speed = speed
        self._monotonic = monotonic
        self._frames = ()
        self._index = 0
        self._wall_start = 0.0
        self._source_start = 0.0
        self._stop_event = threading.Event()

    def connect(self) -> None:
        if self.state in (SourceState.CONNECTED, SourceState.RUNNING):
            return
        if not self._input_frames:
            self._set_state(SourceState.ERROR, "memory source has no frames")
            raise CanSourceError("memory source has no frames")
        self._frames = tuple(
            sorted(
                self._input_frames,
                key=lambda frame: (frame.timestamp, frame.arbitration_id),
            )
        )
        self._index = 0
        self._source_start = self._frames[0].timestamp
        self._wall_start = self._monotonic()
        self._stop_event.clear()
        self._set_state(SourceState.CONNECTED, f"loaded {len(self._frames)} frames")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        if self.state is SourceState.DISCONNECTED:
            raise CanSourceError("memory source is not connected")
        if self.state in (SourceState.STOPPED, SourceState.ERROR):
            return None
        if self.state is SourceState.CONNECTED:
            self._set_state(SourceState.RUNNING, "memory replay started")
        if self._index >= len(self._frames):
            self._set_state(SourceState.STOPPED, "end of memory stream")
            return None

        frame = self._frames[self._index]
        if self.speed > 0:
            target = self._wall_start + (
                frame.timestamp - self._source_start
            ) / self.speed
            wait_seconds = target - self._monotonic()
            if wait_seconds > 0:
                if wait_seconds > timeout:
                    self._stop_event.wait(timeout)
                    return None
                if self._stop_event.wait(wait_seconds):
                    return None
        if self._stop_event.is_set():
            return None
        self._index += 1
        return frame

    def stop(self) -> None:
        if self.state is SourceState.STOPPED:
            return
        self._set_state(SourceState.STOPPING, "stopping memory replay")
        self._stop_event.set()
        self._set_state(SourceState.STOPPED, "memory replay stopped")
