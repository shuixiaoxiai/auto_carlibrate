"""Independent capture thread that isolates CAN receive from UI work."""

from __future__ import annotations

import threading
from typing import Callable, Optional

from ..can.recording import FrameRecorder
from ..can.source import CanSource, SourceState, SourceStatus
from ..domain.models import CanFrame

FrameCallback = Callable[[CanFrame], None]
StatusCallback = Callable[[SourceStatus], None]


class CaptureWorker:
    def __init__(
        self,
        source: CanSource,
        recorder: Optional[FrameRecorder] = None,
        on_frame: Optional[FrameCallback] = None,
        on_status: Optional[StatusCallback] = None,
        poll_timeout: float = 0.1,
        max_frames: Optional[int] = None,
    ) -> None:
        if poll_timeout <= 0:
            raise ValueError("poll_timeout must be greater than zero")
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be greater than zero")
        self.source = source
        self.recorder = recorder
        self.on_frame = on_frame
        self.poll_timeout = poll_timeout
        self.max_frames = max_frames
        self.frame_count = 0
        self.last_error: Optional[BaseException] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unsubscribe = (
            source.subscribe_status(on_status) if on_status is not None else None
        )

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_alive:
            raise RuntimeError("capture worker is already running")
        self.frame_count = 0
        self.last_error = None
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ble-can-capture",
            daemon=False,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            self.source.connect()
            while not self._stop_event.is_set():
                frame = self.source.recv(timeout=self.poll_timeout)
                if frame is None:
                    if self.source.state in (SourceState.STOPPED, SourceState.ERROR):
                        break
                    continue
                if self.recorder is not None:
                    self.recorder.write(frame)
                if self.on_frame is not None:
                    self.on_frame(frame)
                self.frame_count += 1
                if self.max_frames is not None and self.frame_count >= self.max_frames:
                    break
        except BaseException as error:
            self.last_error = error
        finally:
            try:
                if self.recorder is not None:
                    self.recorder.stop()
            except BaseException as error:
                if self.last_error is None:
                    self.last_error = error
            try:
                self.source.stop()
            except BaseException as error:
                if self.last_error is None:
                    self.last_error = error

    def stop(self) -> None:
        self._stop_event.set()
        self.source.stop()

    def join(self, timeout: Optional[float] = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def close(self) -> None:
        self.stop()
        self.join()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
