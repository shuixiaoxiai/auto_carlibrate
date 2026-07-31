"""Stable identifiers shared by capture, analysis, storage, and UI layers."""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class Node(str, Enum):
    MASTER = "master"
    FRONT = "front"
    REAR = "rear"
    LEFT = "left"
    RIGHT = "right"

    @property
    def index(self) -> int:
        return NODE_ORDER.index(self)

    @property
    def label(self) -> str:
        return NODE_LABELS[self.index]

    @classmethod
    def from_index(cls, index: int) -> "Node":
        if index < 0:
            raise ValueError(f"invalid node index: {index}")
        try:
            return NODE_ORDER[index]
        except IndexError as error:
            raise ValueError(f"invalid node index: {index}") from error


NODE_ORDER: Tuple[Node, ...] = (
    Node.MASTER,
    Node.FRONT,
    Node.REAR,
    Node.LEFT,
    Node.RIGHT,
)
NODE_LABELS: Tuple[str, ...] = ("主", "前", "后", "左", "右")


class Direction(str, Enum):
    FRONT = "front"
    FRONT_RIGHT = "front_right"
    RIGHT = "right"
    REAR_RIGHT = "rear_right"
    REAR = "rear"
    REAR_LEFT = "rear_left"
    LEFT = "left"
    FRONT_LEFT = "front_left"

    @property
    def index(self) -> int:
        return DIRECTION_ORDER.index(self)

    @property
    def label(self) -> str:
        return DIRECTION_LABELS[self.index]

    @classmethod
    def from_index(cls, index: int) -> "Direction":
        if index < 0:
            raise ValueError(f"invalid direction index: {index}")
        try:
            return DIRECTION_ORDER[index]
        except IndexError as error:
            raise ValueError(f"invalid direction index: {index}") from error

    @classmethod
    def from_label(cls, label: str) -> "Direction":
        try:
            return DIRECTION_ORDER[DIRECTION_LABELS.index(label)]
        except ValueError as error:
            raise ValueError(f"invalid direction label: {label}") from error


DIRECTION_ORDER: Tuple[Direction, ...] = (
    Direction.FRONT,
    Direction.FRONT_RIGHT,
    Direction.RIGHT,
    Direction.REAR_RIGHT,
    Direction.REAR,
    Direction.REAR_LEFT,
    Direction.LEFT,
    Direction.FRONT_LEFT,
)
DIRECTION_LABELS: Tuple[str, ...] = (
    "正前",
    "右前",
    "正右",
    "右后",
    "正后",
    "左后",
    "正左",
    "左前",
)


class EventType(str, Enum):
    UNLOCK = "unlock"
    LOCK = "lock"

    @property
    def request_value(self) -> int:
        return 1 if self is EventType.UNLOCK else 2

    @property
    def label(self) -> str:
        return "解" if self is EventType.UNLOCK else "闭"

    @classmethod
    def from_request_value(cls, value: int) -> "EventType":
        if value == 1:
            return cls.UNLOCK
        if value == 2:
            return cls.LOCK
        raise ValueError(f"request value does not represent an action: {value}")


class DirectionStatus(str, Enum):
    NOT_STARTED = "not_started"
    RECORDING = "recording"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class SessionPhase(str, Enum):
    IDLE = "idle"
    READY = "ready"
    WAITING_LOCK = "waiting_lock"
    WAITING_UNLOCK = "waiting_unlock"
    AWAITING_DISTANCES = "awaiting_distances"
    READY_TO_FINISH = "ready_to_finish"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class StrategyKind(str, Enum):
    BASE = "base"
    MASTER_UNLOCK = "mstUnlock"
    QUICK_LOCK = "quickLock"
    QUICK_UNLOCK = "quickUnlock"
    MASTER_THAN_SLAVE = "mstThanSlave"
    BEVEL_ANGLE = "bevelAngle"


class ActionOrigin(str, Enum):
    VEHICLE = "vehicle"
    CALCULATED = "calculated"


class DistanceGrade(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    POOR = "poor"
    NOT_AVAILABLE = "not_available"

    @property
    def label(self) -> str:
        return {
            DistanceGrade.EXCELLENT: "优",
            DistanceGrade.GOOD: "良",
            DistanceGrade.POOR: "差",
            DistanceGrade.NOT_AVAILABLE: "--",
        }[self]
