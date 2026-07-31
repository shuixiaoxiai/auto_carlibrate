"""JSONL-backed CAN source with real-time, accelerated, or fastest replay."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, List, Optional

from ..domain.models import CanFrame
from .source import CanSource, CanSourceError, SourceState


class MockCanSource(CanSource):
    def __init__(
        self,
        path: Path,
        speed: float = 1.0,
        loop: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        if not math.isfinite(speed) or speed < 0:
            raise ValueError("speed must be a finite non-negative number")
        self.path = Path(path)
        self.speed = speed
        self.loop = loop
        self._monotonic = monotonic
        self._frames: List[CanFrame] = []
        self._frame_index = 0
        self._cycle_index = 0
        self._cycle_duration = 0.0
        self._wall_start = 0.0
        self._source_start = 0.0
        self._stop_event = threading.Event()

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def connect(self) -> None:
        if self.state in (SourceState.CONNECTED, SourceState.RUNNING):
            return
        self._set_state(SourceState.CONNECTING, f"loading {self.path}")
        try:
            frames = self._load_frames()
        except (OSError, ValueError) as error:
            self._set_state(SourceState.ERROR, str(error))
            raise CanSourceError(f"cannot load mock CAN data: {error}") from error
        if not frames:
            self._set_state(SourceState.ERROR, "mock file has no frames")
            raise CanSourceError("mock CAN file has no frames")

        self._frames = frames
        self._frame_index = 0
        self._cycle_index = 0
        self._source_start = frames[0].timestamp
        self._cycle_duration = self._calculate_cycle_duration(frames)
        self._wall_start = self._monotonic()
        self._stop_event.clear()
        self._set_state(SourceState.CONNECTED, f"loaded {len(frames)} frames")

    def _load_frames(self) -> List[CanFrame]:
        frames: List[CanFrame] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    frames.append(CanFrame.from_json_record(record))
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(f"invalid frame at line {line_number}: {error}") from error
        frames.sort(key=lambda frame: (frame.timestamp, frame.arbitration_id))
        return frames

    @staticmethod
    def _calculate_cycle_duration(frames: List[CanFrame]) -> float:
        if len(frames) == 1:
            return 0.001
        positive_deltas = [
            frames[index].timestamp - frames[index - 1].timestamp
            for index in range(1, len(frames))
            if frames[index].timestamp > frames[index - 1].timestamp
        ]
        tail_step = min(positive_deltas) if positive_deltas else 0.001
        return max(frames[-1].timestamp - frames[0].timestamp + tail_step, 0.001)

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        if self.state is SourceState.DISCONNECTED:
            raise CanSourceError("mock source is not connected")
        if self.state in (SourceState.STOPPED, SourceState.ERROR):
            return None
        if self.state is SourceState.CONNECTED:
            self._set_state(SourceState.RUNNING, "replay started")

        if self._frame_index >= len(self._frames):
            if not self.loop:
                self._set_state(SourceState.STOPPED, "end of mock stream")
                return None
            self._frame_index = 0
            self._cycle_index += 1

        frame = self._frames[self._frame_index]
        timestamp_offset = self._cycle_index * self._cycle_duration
        output = replace(frame, timestamp=frame.timestamp + timestamp_offset)

        if self.speed > 0:
            relative_source = (
                frame.timestamp - self._source_start + timestamp_offset
            ) / self.speed
            wait_seconds = self._wall_start + relative_source - self._monotonic()
            if wait_seconds > 0:
                if wait_seconds > timeout:
                    self._stop_event.wait(timeout)
                    return None
                if self._stop_event.wait(wait_seconds):
                    return None

        if self._stop_event.is_set():
            return None
        self._frame_index += 1
        return output

    def stop(self) -> None:
        if self.state is SourceState.STOPPED:
            return
        self._set_state(SourceState.STOPPING, "stopping mock replay")
        self._stop_event.set()
        self._set_state(SourceState.STOPPED, "mock replay stopped")
