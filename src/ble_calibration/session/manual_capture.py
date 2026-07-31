"""Thread-safe manual direction capture coordinated with a CanSource worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from ..analysis import DirectionDataset
from ..can.recording import FrameRecorder
from ..can.source import CanSource, SourceState, SourceStatus
from ..capture import CaptureWorker
from ..domain import Direction, EventType
from ..domain.enums import SessionPhase
from .controller import DirectionSessionController, SessionStateError


@dataclass(frozen=True)
class ManualCaptureSnapshot:
    phase: SessionPhase
    direction: Optional[Direction]
    dataset: Optional[DirectionDataset]
    source_status: Optional[SourceStatus]
    frame_count: int
    source_finished: bool
    error: Optional[str]


class ManualCaptureCoordinator:
    """Serialize operator actions and worker callbacks around one controller."""

    def __init__(
        self,
        controller: Optional[DirectionSessionController] = None,
    ) -> None:
        self.controller = controller or DirectionSessionController()
        self._lock = threading.RLock()
        self._worker: Optional[CaptureWorker] = None
        self._source_status: Optional[SourceStatus] = None
        self._persistent_source = False
        self._direction_recorder: Optional[FrameRecorder] = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.controller.active_record_snapshot() is not None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            worker = self._worker
            return (
                self._persistent_source
                and worker is not None
                and worker.is_alive
                and worker.last_error is None
                and self._source_status is not None
                and self._source_status.state
                in (SourceState.CONNECTED, SourceState.RUNNING)
            )

    @property
    def uses_persistent_source(self) -> bool:
        with self._lock:
            return self._persistent_source

    def connect(self, source: CanSource) -> None:
        """Connect one live source and keep it open across direction records."""
        stale_worker = None
        with self._lock:
            if self.controller.active_record_snapshot() is not None:
                raise SessionStateError("finish the active direction before connecting")
            if self._worker is not None:
                if self._worker.is_alive:
                    raise SessionStateError("a CAN source is already connected")
                stale_worker, self._worker = self._worker, None
                self._persistent_source = False
        if stale_worker is not None:
            stale_worker.close()
        with self._lock:
            if self._worker is not None:
                raise SessionStateError("a CAN source is already connected")
            self._source_status = SourceStatus(
                SourceState.CONNECTING,
                "opening live CAN source",
            )
            self._persistent_source = True
            worker = CaptureWorker(
                source,
                on_frame=self._on_frame,
                on_status=self._on_status,
            )
            self._worker = worker
        worker.start()

    def disconnect(self) -> None:
        with self._lock:
            if self.controller.active_record_snapshot() is not None:
                raise SessionStateError(
                    "finish the active direction before disconnecting"
                )
            worker = self._worker
        if worker is not None:
            worker.close()
        with self._lock:
            if self._worker is worker:
                self._worker = None
            self._persistent_source = False
            self._stop_direction_recorder()

    def begin(
        self,
        direction: Direction,
        source: CanSource,
        *,
        walking_speed_mps: float = 1.0,
        raw_data_file: Optional[str] = None,
        recorder: Optional[FrameRecorder] = None,
    ) -> None:
        with self._lock:
            if self._worker is not None:
                raise SessionStateError("a CAN source is already active")
            self._prepare_direction(
                direction,
                walking_speed_mps=walking_speed_mps,
                raw_data_file=raw_data_file,
            )
            self._source_status = None
            self._persistent_source = False
            worker = CaptureWorker(
                source,
                recorder=recorder,
                on_frame=self._on_frame,
                on_status=self._on_status,
            )
            self._worker = worker
        worker.start()

    def begin_connected(
        self,
        direction: Direction,
        *,
        walking_speed_mps: float = 1.0,
        raw_data_file: Optional[str] = None,
        recorder: Optional[FrameRecorder] = None,
    ) -> None:
        """Start one direction while retaining the connected live CAN worker."""
        with self._lock:
            worker = self._worker
            if (
                not self._persistent_source
                or worker is None
                or not worker.is_alive
                or worker.last_error is not None
                or self._source_status is None
                or self._source_status.state
                not in (SourceState.CONNECTED, SourceState.RUNNING)
            ):
                raise SessionStateError("connect the live CAN device first")
            self._prepare_direction(
                direction,
                walking_speed_mps=walking_speed_mps,
                raw_data_file=raw_data_file,
            )
            self._direction_recorder = recorder

    def _prepare_direction(
        self,
        direction: Direction,
        *,
        walking_speed_mps: float,
        raw_data_file: Optional[str],
    ) -> None:
        if self.controller.active_record_snapshot() is not None:
            raise SessionStateError("finish the active direction first")
        if self.controller.record_for(direction) is not None:
            self.controller.redo(direction)
        else:
            self.controller.select_direction(direction)
        self.controller.start(
            walking_speed_mps=walking_speed_mps,
            raw_data_file=raw_data_file,
        )

    def _on_frame(self, frame) -> None:
        with self._lock:
            if self.controller.active_record_snapshot() is None:
                return
            if self._direction_recorder is not None:
                self._direction_recorder.write(frame)
            self.controller.process_frame(frame)

    def _on_status(self, status: SourceStatus) -> None:
        with self._lock:
            self._source_status = status

    def finish(
        self,
        *,
        lock_distance_m: Optional[float],
        unlock_distance_m: Optional[float],
    ) -> DirectionDataset:
        with self._lock:
            worker = self._worker
            persistent_source = self._persistent_source
        if worker is not None and not persistent_source:
            worker.stop()
            if not worker.join(2.0):
                raise RuntimeError("CAN capture thread did not stop within 2 seconds")
        with self._lock:
            try:
                if lock_distance_m is not None:
                    self.controller.set_distance(EventType.LOCK, lock_distance_m)
                if unlock_distance_m is not None:
                    self.controller.set_distance(EventType.UNLOCK, unlock_distance_m)
                record = self.controller.manual_stop()
                return DirectionDataset(
                    record,
                    self.controller.samples_for(record.direction),
                )
            finally:
                if persistent_source:
                    self._stop_direction_recorder()
                else:
                    self._worker = None

    def snapshot(self) -> ManualCaptureSnapshot:
        with self._lock:
            record = self.controller.active_record_snapshot()
            dataset = (
                None
                if record is None
                else DirectionDataset(record, self.controller.active_samples)
            )
            worker = self._worker
            error = (
                None
                if worker is None or worker.last_error is None
                else str(worker.last_error)
            )
            return ManualCaptureSnapshot(
                phase=self.controller.phase,
                direction=self.controller.selected_direction,
                dataset=dataset,
                source_status=self._source_status,
                frame_count=0 if worker is None else worker.frame_count,
                source_finished=(
                    record is not None
                    and worker is not None
                    and not worker.is_alive
                ),
                error=error,
            )

    def wait_source_finished(self, timeout: Optional[float] = None) -> bool:
        worker = self._worker
        return True if worker is None else worker.join(timeout)

    def close(self) -> None:
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.close()
        with self._lock:
            self._stop_direction_recorder()
            self._worker = None
            self._persistent_source = False

    def _stop_direction_recorder(self) -> None:
        recorder, self._direction_recorder = self._direction_recorder, None
        if recorder is not None:
            recorder.stop()
