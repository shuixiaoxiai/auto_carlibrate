"""0x55A request edge detection."""

from __future__ import annotations

from typing import Optional

from ..domain.enums import Direction
from ..domain.models import VehicleEvent


class RequestEdgeDetector:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._previous_value = 0

    @property
    def previous_value(self) -> int:
        return self._previous_value

    def observe(
        self,
        request_value: int,
        timestamp: float,
        direction: Optional[Direction],
    ) -> Optional[VehicleEvent]:
        if not 0 <= request_value <= 0x0F:
            raise ValueError("request_value must fit in a nibble")
        previous = self._previous_value
        self._previous_value = request_value
        if request_value not in (1, 2) or request_value == previous or direction is None:
            return None
        return VehicleEvent.from_request(request_value, timestamp, direction)
