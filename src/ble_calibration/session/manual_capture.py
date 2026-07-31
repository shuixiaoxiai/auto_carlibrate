"""Thread-safe manual direction capture coordinated with a CanSource worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from ..analysis import DirectionDataset
from ..can.recording import FrameRecorder
from ..can.source import CanSource, SourceStatus
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

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.controller.active_record_snapshot() is not None

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
            self._source_status = None
            worker = CaptureWorker(
                source,
                recorder=recorder,
                on_frame=self._on_frame,
                on_status=self._on_status,
            )
            self._worker = worker
        worker.start()

    def _on_frame(self, frame) -> None:
        with self._lock:
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
        worker = self._worker
        if worker is not None:
            worker.stop()
            if not worker.join(2.0):
                raise RuntimeError("CAN capture thread did not stop within 2 seconds")
        with self._lock:
            if lock_distance_m is not None:
                self.controller.set_distance(EventType.LOCK, lock_distance_m)
            if unlock_distance_m is not None:
                self.controller.set_distance(EventType.UNLOCK, unlock_distance_m)
            record = self.controller.manual_stop()
            dataset = DirectionDataset(
                record,
                self.controller.samples_for(record.direction),
            )
            self._worker = None
            return dataset

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
                source_finished=worker is not None and not worker.is_alive,
                error=error,
            )

    def wait_source_finished(self, timeout: Optional[float] = None) -> bool:
        worker = self._worker
        return True if worker is None else worker.join(timeout)

    def close(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.close()
            self._worker = None
