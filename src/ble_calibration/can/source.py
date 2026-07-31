"""Common CAN source lifecycle used by mock replay and live hardware."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

from ..domain.models import CanFrame


class SourceState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class SourceStatus:
    state: SourceState
    message: str = ""


class CanSourceError(RuntimeError):
    """A user-visible data-source failure."""


StatusCallback = Callable[[SourceStatus], None]


class CanSource(ABC):
    """Thread-safe lifecycle contract for a stream of :class:`CanFrame` values."""

    def __init__(self) -> None:
        self._state = SourceState.DISCONNECTED
        self._state_lock = threading.Lock()
        self._status_callbacks: List[StatusCallback] = []

    @property
    def state(self) -> SourceState:
        with self._state_lock:
            return self._state

    def subscribe_status(self, callback: StatusCallback) -> Callable[[], None]:
        with self._state_lock:
            self._status_callbacks.append(callback)

        def unsubscribe() -> None:
            with self._state_lock:
                if callback in self._status_callbacks:
                    self._status_callbacks.remove(callback)

        return unsubscribe

    def _set_state(self, state: SourceState, message: str = "") -> None:
        with self._state_lock:
            self._state = state
            callbacks = tuple(self._status_callbacks)
        status = SourceStatus(state, message)
        for callback in callbacks:
            try:
                callback(status)
            except Exception:
                # A UI/diagnostic observer must never break the receive lifecycle.
                continue

    @abstractmethod
    def connect(self) -> None:
        """Open the source and prepare it for ``recv`` calls."""

    @abstractmethod
    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        """Return the next frame, or ``None`` on timeout/end of stream."""

    @abstractmethod
    def stop(self) -> None:
        """Interrupt pending receives and release all source resources."""

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "CanSource":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
