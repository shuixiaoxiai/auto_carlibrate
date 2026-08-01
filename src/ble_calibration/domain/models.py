"""Validated domain models shared across all application layers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from .enums import (
    ActionOrigin,
    Direction,
    DirectionStatus,
    DistanceGrade,
    EventType,
    NODE_ORDER,
    Node,
    StrategyKind,
)
from .schema import (
    ANALYSIS_VERSION,
    CAN_JSONL_SCHEMA_VERSION,
    LEGACY_PROJECT_SCHEMA_VERSIONS,
    PROJECT_SCHEMA_VERSION,
)

NODE_COUNT = len(NODE_ORDER)
MAX_DIRECTION_GROUPS = 3
RssiValues = Tuple[Optional[int], Optional[int], Optional[int], Optional[int], Optional[int]]
NodeTimes = Tuple[
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
]
NodeFlags = Tuple[bool, bool, bool, bool, bool]


def _require_finite_non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")


def _require_node_tuple(name: str, values: Sequence[Any]) -> None:
    if len(values) != NODE_COUNT:
        raise ValueError(f"{name} must contain exactly {NODE_COUNT} node values")


@dataclass(frozen=True)
class CanFrame:
    """One raw CAN or CAN-FD frame using the source-provided timestamp."""

    timestamp: float
    arbitration_id: int
    data: bytes
    channel: int = 0
    is_fd: bool = True
    bitrate_switch: bool = True
    receive_monotonic: Optional[float] = None

    def __post_init__(self) -> None:
        _require_finite_non_negative("timestamp", self.timestamp)
        if not 0 <= self.arbitration_id <= 0x1FFFFFFF:
            raise ValueError("arbitration_id is outside the CAN identifier range")
        if self.channel < 0:
            raise ValueError("channel cannot be negative")
        if not isinstance(self.data, bytes):
            object.__setattr__(self, "data", bytes(self.data))
        if len(self.data) > 64:
            raise ValueError("CAN-FD payload cannot exceed 64 bytes")
        if self.receive_monotonic is not None:
            _require_finite_non_negative("receive_monotonic", self.receive_monotonic)

    @property
    def source_timestamp(self) -> float:
        return self.timestamp

    def to_json_record(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "schema": CAN_JSONL_SCHEMA_VERSION,
            "timestamp": self.timestamp,
            "arbitration_id": self.arbitration_id,
            "channel": self.channel,
            "is_fd": self.is_fd,
            "bitrate_switch": self.bitrate_switch,
            "data": self.data.hex().upper(),
        }
        if self.receive_monotonic is not None:
            record["receive_monotonic"] = self.receive_monotonic
        return record

    @classmethod
    def from_json_record(cls, record: Mapping[str, Any]) -> "CanFrame":
        try:
            data = bytes.fromhex(str(record["data"]))
            return cls(
                timestamp=float(record["timestamp"]),
                arbitration_id=int(record["arbitration_id"]),
                data=data,
                channel=int(record.get("channel", 0)),
                is_fd=bool(record.get("is_fd", True)),
                bitrate_switch=bool(record.get("bitrate_switch", True)),
                receive_monotonic=(
                    None
                    if record.get("receive_monotonic") is None
                    else float(record["receive_monotonic"])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid CAN JSON record: {error}") from error


@dataclass(frozen=True)
class RssiSample:
    """One time-aligned five-node RSSI sample."""

    relative_time: float
    source_timestamp: float
    values: RssiValues
    node_age_ms: NodeTimes = (None, None, None, None, None)
    stale: NodeFlags = (True, True, True, True, True)

    def __post_init__(self) -> None:
        _require_finite_non_negative("relative_time", self.relative_time)
        _require_finite_non_negative("source_timestamp", self.source_timestamp)
        _require_node_tuple("values", self.values)
        _require_node_tuple("node_age_ms", self.node_age_ms)
        _require_node_tuple("stale", self.stale)
        for value in self.values:
            if value is not None and not -256 <= value <= -1:
                raise ValueError(f"RSSI value out of range: {value}")
        for age in self.node_age_ms:
            if age is not None:
                _require_finite_non_negative("node age", age)

    def value(self, node: Node) -> Optional[int]:
        return self.values[node.index]

    def is_valid(self, node: Node) -> bool:
        return self.value(node) is not None and not self.stale[node.index]

    def values_by_node(self) -> Dict[str, Optional[int]]:
        return {node.value: self.value(node) for node in NODE_ORDER}


@dataclass(frozen=True)
class VehicleEvent:
    """An actual vehicle action edge decoded from 0x55A."""

    event_type: EventType
    timestamp: float
    direction: Direction
    request_value: int

    def __post_init__(self) -> None:
        _require_finite_non_negative("timestamp", self.timestamp)
        if self.request_value != self.event_type.request_value:
            raise ValueError("request_value does not match event_type")

    @classmethod
    def from_request(
        cls,
        request_value: int,
        timestamp: float,
        direction: Direction,
    ) -> "VehicleEvent":
        event_type = EventType.from_request_value(request_value)
        return cls(event_type, timestamp, direction, request_value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "direction": self.direction.value,
            "request_value": self.request_value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VehicleEvent":
        return cls(
            event_type=EventType(str(data["event_type"])),
            timestamp=float(data["timestamp"]),
            direction=Direction(str(data["direction"])),
            request_value=int(data["request_value"]),
        )


@dataclass(frozen=True)
class ConditionPoint:
    """Solid-line point where a strategy condition starts remaining true."""

    event_type: EventType
    timestamp: float
    trigger_node: Node
    strategy: StrategyKind
    rssi: RssiValues

    def __post_init__(self) -> None:
        _require_finite_non_negative("timestamp", self.timestamp)
        _require_node_tuple("rssi", self.rssi)
        for value in self.rssi:
            if value is not None and not -256 <= value <= -1:
                raise ValueError(f"RSSI value out of range: {value}")

    @property
    def label(self) -> str:
        return f"{self.event_type.label}({self.trigger_node.label})"


@dataclass(frozen=True)
class ActionPoint:
    """Dashed-line action point for actual data or a What-if result."""

    event_type: EventType
    timestamp: float
    origin: ActionOrigin

    def __post_init__(self) -> None:
        _require_finite_non_negative("timestamp", self.timestamp)


@dataclass(frozen=True)
class StrategyEventResult:
    """One lock or unlock result containing both line semantics."""

    condition: ConditionPoint
    action: ActionPoint
    distance_m: Optional[float] = None
    grade: DistanceGrade = DistanceGrade.NOT_AVAILABLE

    def __post_init__(self) -> None:
        if self.condition.event_type is not self.action.event_type:
            raise ValueError("condition and action event types must match")
        if self.action.timestamp < self.condition.timestamp:
            raise ValueError("action cannot occur before its condition point")
        if self.distance_m is not None:
            _require_finite_non_negative("distance_m", self.distance_m)


@dataclass(frozen=True)
class DirectionAnalysisResult:
    direction: Direction
    lock: Optional[StrategyEventResult] = None
    unlock: Optional[StrategyEventResult] = None
    analysis_version: str = ANALYSIS_VERSION


@dataclass(frozen=True)
class DirectionRecord:
    """Persisted metadata for one manually selected direction recording."""

    direction: Direction
    status: DirectionStatus = DirectionStatus.NOT_STARTED
    start_timestamp: Optional[float] = None
    end_timestamp: Optional[float] = None
    walking_speed_mps: float = 1.0
    actual_lock_distance_m: Optional[float] = None
    actual_unlock_distance_m: Optional[float] = None
    vehicle_events: Tuple[VehicleEvent, ...] = ()
    sample_count: int = 0
    raw_data_file: Optional[str] = None
    group_index: int = 1
    recording_id: str = field(default_factory=lambda: str(uuid4()))
    recorded_at: Optional[str] = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        )
    )

    def __post_init__(self) -> None:
        if self.start_timestamp is not None:
            _require_finite_non_negative("start_timestamp", self.start_timestamp)
        if self.end_timestamp is not None:
            _require_finite_non_negative("end_timestamp", self.end_timestamp)
        if (
            self.start_timestamp is not None
            and self.end_timestamp is not None
            and self.end_timestamp < self.start_timestamp
        ):
            raise ValueError("end_timestamp cannot be before start_timestamp")
        if not math.isfinite(self.walking_speed_mps) or self.walking_speed_mps <= 0:
            raise ValueError("walking_speed_mps must be greater than zero")
        for name, value in (
            ("actual_lock_distance_m", self.actual_lock_distance_m),
            ("actual_unlock_distance_m", self.actual_unlock_distance_m),
        ):
            if value is not None:
                _require_finite_non_negative(name, value)
        if self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
        if not 1 <= self.group_index <= MAX_DIRECTION_GROUPS:
            raise ValueError(
                f"group_index must be between 1 and {MAX_DIRECTION_GROUPS}"
            )
        if not self.recording_id.strip():
            raise ValueError("recording_id cannot be empty")
        if self.recorded_at is not None and not self.recorded_at.strip():
            raise ValueError("recorded_at cannot be empty")
        if any(event.direction is not self.direction for event in self.vehicle_events):
            raise ValueError("all vehicle events must belong to this direction")

    def event(self, event_type: EventType) -> Optional[VehicleEvent]:
        return next(
            (event for event in self.vehicle_events if event.event_type is event_type),
            None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction.value,
            "status": self.status.value,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "walking_speed_mps": self.walking_speed_mps,
            "actual_lock_distance_m": self.actual_lock_distance_m,
            "actual_unlock_distance_m": self.actual_unlock_distance_m,
            "vehicle_events": [event.to_dict() for event in self.vehicle_events],
            "sample_count": self.sample_count,
            "raw_data_file": self.raw_data_file,
            "group_index": self.group_index,
            "recording_id": self.recording_id,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DirectionRecord":
        direction = Direction(str(data["direction"]))
        group_index = int(data.get("group_index", 1))
        return cls(
            direction=direction,
            status=DirectionStatus(str(data.get("status", DirectionStatus.NOT_STARTED.value))),
            start_timestamp=(
                None if data.get("start_timestamp") is None else float(data["start_timestamp"])
            ),
            end_timestamp=(
                None if data.get("end_timestamp") is None else float(data["end_timestamp"])
            ),
            walking_speed_mps=float(data.get("walking_speed_mps", 1.0)),
            actual_lock_distance_m=(
                None
                if data.get("actual_lock_distance_m") is None
                else float(data["actual_lock_distance_m"])
            ),
            actual_unlock_distance_m=(
                None
                if data.get("actual_unlock_distance_m") is None
                else float(data["actual_unlock_distance_m"])
            ),
            vehicle_events=tuple(
                VehicleEvent.from_dict(item) for item in data.get("vehicle_events", [])
            ),
            sample_count=int(data.get("sample_count", 0)),
            raw_data_file=(
                None if data.get("raw_data_file") is None else str(data["raw_data_file"])
            ),
            group_index=group_index,
            recording_id=str(
                data.get("recording_id")
                or f"legacy-{direction.value}-group-{group_index}"
            ),
            recorded_at=(
                None if data.get("recorded_at") is None else str(data["recorded_at"])
            ),
        )


@dataclass(frozen=True)
class CalibrationProject:
    """Top-level project metadata; raw samples remain in referenced capture files."""

    name: str
    project_id: str = field(default_factory=lambda: str(uuid4()))
    schema: str = PROJECT_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    original_cloud_hex: Optional[str] = None
    directions: Tuple[DirectionRecord, ...] = ()
    default_walking_speed_mps: float = 1.0
    analysis_version: str = ANALYSIS_VERSION

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("project name cannot be empty")
        if self.schema != PROJECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported project schema: {self.schema}")
        if (
            not math.isfinite(self.default_walking_speed_mps)
            or not 0.1 <= self.default_walking_speed_mps <= 5.0
        ):
            raise ValueError("default_walking_speed_mps must be between 0.1 and 5.0")
        seen = set()
        for record in self.directions:
            key = (record.direction, record.group_index)
            if key in seen:
                raise ValueError(
                    "duplicate direction group: "
                    f"{record.direction.value}/{record.group_index}"
                )
            seen.add(key)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "project_id": self.project_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "original_cloud_hex": self.original_cloud_hex,
            "directions": [record.to_dict() for record in self.directions],
            "default_walking_speed_mps": self.default_walking_speed_mps,
            "analysis_version": self.analysis_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CalibrationProject":
        schema = str(data["schema"])
        if schema not in (PROJECT_SCHEMA_VERSION, *LEGACY_PROJECT_SCHEMA_VERSIONS):
            raise ValueError(f"unsupported project schema: {schema}")
        return cls(
            schema=PROJECT_SCHEMA_VERSION,
            project_id=str(data["project_id"]),
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            original_cloud_hex=(
                None
                if data.get("original_cloud_hex") is None
                else str(data["original_cloud_hex"])
            ),
            directions=tuple(
                DirectionRecord.from_dict(item) for item in data.get("directions", [])
            ),
            default_walking_speed_mps=float(
                data.get("default_walking_speed_mps", 1.0)
            ),
            analysis_version=str(data.get("analysis_version", ANALYSIS_VERSION)),
        )
