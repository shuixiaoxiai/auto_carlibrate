"""Direction-test workflow independent from GUI and CAN hardware."""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from ..domain.enums import (
    Direction,
    DirectionStatus,
    EventType,
    SessionPhase,
)
from ..domain.models import CanFrame, DirectionRecord, RssiSample, VehicleEvent
from ..processing.pipeline import CanFrameProcessor, FrameProcessingResult


class SessionStateError(RuntimeError):
    """The requested operator action is invalid in the current phase."""


class DirectionSessionController:
    def __init__(
        self,
        sample_rate_hz: float = 10.0,
        stale_timeout_s: float = 0.3,
        default_walking_speed_mps: float = 1.0,
    ) -> None:
        if default_walking_speed_mps <= 0:
            raise ValueError("default_walking_speed_mps must be greater than zero")
        self.processor = CanFrameProcessor(sample_rate_hz, stale_timeout_s)
        self.default_walking_speed_mps = default_walking_speed_mps
        self.phase = SessionPhase.IDLE
        self.selected_direction: Optional[Direction] = None
        self._records: Dict[Direction, DirectionRecord] = {}
        self._samples: Dict[Direction, Tuple[RssiSample, ...]] = {}
        self._active_samples = []
        self._active_events = []
        self._start_timestamp: Optional[float] = None
        self._end_timestamp: Optional[float] = None
        self._last_timestamp: Optional[float] = None
        self._walking_speed_mps = default_walking_speed_mps
        self._lock_distance_m: Optional[float] = None
        self._unlock_distance_m: Optional[float] = None
        self._raw_data_file: Optional[str] = None
        self.unexpected_event_count = 0

    @property
    def records(self) -> Tuple[DirectionRecord, ...]:
        return tuple(
            self._records[direction]
            for direction in Direction
            if direction in self._records
        )

    @property
    def active_samples(self) -> Tuple[RssiSample, ...]:
        return tuple(self._active_samples)

    def samples_for(self, direction: Direction) -> Tuple[RssiSample, ...]:
        return self._samples.get(direction, ())

    def record_for(self, direction: Direction) -> Optional[DirectionRecord]:
        return self._records.get(direction)

    def active_record_snapshot(self) -> Optional[DirectionRecord]:
        if (
            self.selected_direction is None
            or self.phase
            not in (
                SessionPhase.WAITING_LOCK,
                SessionPhase.WAITING_UNLOCK,
                SessionPhase.AWAITING_DISTANCES,
                SessionPhase.READY_TO_FINISH,
            )
        ):
            return None
        return DirectionRecord(
            direction=self.selected_direction,
            status=DirectionStatus.RECORDING,
            start_timestamp=self._start_timestamp,
            end_timestamp=self._last_timestamp,
            walking_speed_mps=self._walking_speed_mps,
            actual_lock_distance_m=self._lock_distance_m,
            actual_unlock_distance_m=self._unlock_distance_m,
            vehicle_events=tuple(self._active_events),
            sample_count=len(self._active_samples),
            raw_data_file=self._raw_data_file,
        )

    def select_direction(self, direction: Direction) -> None:
        if self.phase in (
            SessionPhase.WAITING_LOCK,
            SessionPhase.WAITING_UNLOCK,
            SessionPhase.AWAITING_DISTANCES,
            SessionPhase.READY_TO_FINISH,
        ):
            raise SessionStateError("finish or stop the current direction first")
        self.selected_direction = direction
        self.phase = SessionPhase.READY

    def start(
        self,
        walking_speed_mps: Optional[float] = None,
        raw_data_file: Optional[str] = None,
    ) -> None:
        if self.phase is not SessionPhase.READY or self.selected_direction is None:
            raise SessionStateError("select a direction before starting")
        speed = (
            self.default_walking_speed_mps
            if walking_speed_mps is None
            else walking_speed_mps
        )
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("walking_speed_mps must be greater than zero")
        self.processor.reset()
        self._active_samples = []
        self._active_events = []
        self._start_timestamp = None
        self._end_timestamp = None
        self._last_timestamp = None
        self._walking_speed_mps = speed
        self._lock_distance_m = None
        self._unlock_distance_m = None
        self._raw_data_file = raw_data_file
        self.unexpected_event_count = 0
        self.phase = SessionPhase.WAITING_LOCK

    def process_frame(self, frame: CanFrame) -> FrameProcessingResult:
        if self.phase not in (
            SessionPhase.WAITING_LOCK,
            SessionPhase.WAITING_UNLOCK,
            SessionPhase.AWAITING_DISTANCES,
            SessionPhase.READY_TO_FINISH,
        ):
            return FrameProcessingResult({})
        assert self.selected_direction is not None
        if self._start_timestamp is None:
            self._start_timestamp = frame.timestamp
        self._last_timestamp = frame.timestamp
        result = self.processor.process(frame, self.selected_direction)
        self._active_samples.extend(result.samples)
        if result.event is not None:
            self._handle_event(result.event)
        return result

    def _handle_event(self, event: VehicleEvent) -> None:
        if event.event_type is EventType.LOCK and self.phase is SessionPhase.WAITING_LOCK:
            self._active_events.append(event)
            self.phase = SessionPhase.WAITING_UNLOCK
            return
        if event.event_type is EventType.UNLOCK and self.phase is SessionPhase.WAITING_UNLOCK:
            self._active_events.append(event)
            self._end_timestamp = event.timestamp
            self.phase = (
                SessionPhase.READY_TO_FINISH
                if self._lock_distance_m is not None
                and self._unlock_distance_m is not None
                else SessionPhase.AWAITING_DISTANCES
            )
            return
        self.unexpected_event_count += 1

    def set_distances(self, lock_distance_m: float, unlock_distance_m: float) -> None:
        self.set_distance(EventType.LOCK, lock_distance_m)
        self.set_distance(EventType.UNLOCK, unlock_distance_m)

    def set_distance(self, event_type: EventType, distance_m: float) -> None:
        name = f"{event_type.value}_distance_m"
        if not math.isfinite(distance_m) or distance_m < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
        if self.phase not in (
            SessionPhase.WAITING_LOCK,
            SessionPhase.WAITING_UNLOCK,
            SessionPhase.AWAITING_DISTANCES,
            SessionPhase.READY_TO_FINISH,
        ):
            raise SessionStateError("no active direction accepts distance input")
        if event_type is EventType.LOCK:
            self._lock_distance_m = distance_m
        else:
            self._unlock_distance_m = distance_m
        if (
            self.phase is SessionPhase.AWAITING_DISTANCES
            and self._lock_distance_m is not None
            and self._unlock_distance_m is not None
        ):
            self.phase = SessionPhase.READY_TO_FINISH

    def manual_stop(self, timestamp: Optional[float] = None) -> DirectionRecord:
        if self.phase not in (
            SessionPhase.WAITING_LOCK,
            SessionPhase.WAITING_UNLOCK,
            SessionPhase.AWAITING_DISTANCES,
            SessionPhase.READY_TO_FINISH,
        ):
            raise SessionStateError("there is no active direction to stop")
        self._end_timestamp = timestamp if timestamp is not None else self._last_timestamp
        event_types = {event.event_type for event in self._active_events}
        is_complete = (
            EventType.LOCK in event_types
            and EventType.UNLOCK in event_types
            and self._lock_distance_m is not None
            and self._unlock_distance_m is not None
        )
        return self._finalize(
            DirectionStatus.COMPLETE if is_complete else DirectionStatus.INCOMPLETE,
            SessionPhase.COMPLETE if is_complete else SessionPhase.INCOMPLETE,
        )

    def _finalize(
        self,
        status: DirectionStatus,
        phase: SessionPhase,
    ) -> DirectionRecord:
        assert self.selected_direction is not None
        record = DirectionRecord(
            direction=self.selected_direction,
            status=status,
            start_timestamp=self._start_timestamp,
            end_timestamp=self._end_timestamp,
            walking_speed_mps=self._walking_speed_mps,
            actual_lock_distance_m=self._lock_distance_m,
            actual_unlock_distance_m=self._unlock_distance_m,
            vehicle_events=tuple(self._active_events),
            sample_count=len(self._active_samples),
            raw_data_file=self._raw_data_file,
        )
        self._records[self.selected_direction] = record
        self._samples[self.selected_direction] = tuple(self._active_samples)
        self.phase = phase
        return record

    def redo(self, direction: Direction) -> None:
        if self.phase in (
            SessionPhase.WAITING_LOCK,
            SessionPhase.WAITING_UNLOCK,
            SessionPhase.AWAITING_DISTANCES,
            SessionPhase.READY_TO_FINISH,
        ):
            raise SessionStateError("stop the active direction before re-recording")
        self._records.pop(direction, None)
        self._samples.pop(direction, None)
        self.selected_direction = direction
        self.phase = SessionPhase.READY
