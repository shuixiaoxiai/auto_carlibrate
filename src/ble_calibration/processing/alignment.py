"""Five-node latest-value cache and fixed-rate aligned RSSI samples."""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

from ..can.protocol import decode_frame
from ..domain.enums import NODE_ORDER, Node
from ..domain.models import CanFrame, RssiSample


class RssiTimeAligner:
    def __init__(
        self,
        sample_rate_hz: float = 10.0,
        stale_timeout_s: float = 0.3,
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be greater than zero")
        if stale_timeout_s <= 0:
            raise ValueError("stale_timeout_s must be greater than zero")
        self.sample_rate_hz = sample_rate_hz
        self.sample_period_s = 1.0 / sample_rate_hz
        self.stale_timeout_s = stale_timeout_s
        self.out_of_order_count = 0
        self.reset()

    def reset(self) -> None:
        self._origin_timestamp: Optional[float] = None
        self._next_sample_timestamp: Optional[float] = None
        self._last_frame_timestamp: Optional[float] = None
        self._values: Dict[Node, Optional[int]] = {node: None for node in NODE_ORDER}
        self._value_timestamps: Dict[Node, Optional[float]] = {
            node: None for node in NODE_ORDER
        }
        self.out_of_order_count = 0

    @property
    def origin_timestamp(self) -> Optional[float]:
        return self._origin_timestamp

    def ingest(
        self,
        frame: CanFrame,
        decoded: Optional[Mapping[str, int]] = None,
    ) -> Tuple[RssiSample, ...]:
        if (
            self._last_frame_timestamp is not None
            and frame.timestamp < self._last_frame_timestamp - 1e-9
        ):
            self.out_of_order_count += 1
            return ()

        if self._origin_timestamp is None:
            self._origin_timestamp = frame.timestamp
            self._next_sample_timestamp = frame.timestamp

        samples: List[RssiSample] = []
        while (
            self._next_sample_timestamp is not None
            and self._next_sample_timestamp < frame.timestamp - 1e-9
        ):
            samples.append(self._snapshot(self._next_sample_timestamp))
            self._advance_sample_time()

        values = decode_frame(frame.arbitration_id, frame.data) if decoded is None else decoded
        for node in NODE_ORDER:
            if node.value in values:
                self._values[node] = int(values[node.value])
                self._value_timestamps[node] = frame.timestamp

        while (
            self._next_sample_timestamp is not None
            and self._next_sample_timestamp <= frame.timestamp + 1e-9
        ):
            samples.append(self._snapshot(self._next_sample_timestamp))
            self._advance_sample_time()

        self._last_frame_timestamp = frame.timestamp
        return tuple(samples)

    def flush(self, timestamp: float) -> Tuple[RssiSample, ...]:
        if self._origin_timestamp is None:
            return ()
        if self._last_frame_timestamp is not None and timestamp < self._last_frame_timestamp:
            raise ValueError("flush timestamp cannot move backwards")
        samples: List[RssiSample] = []
        while (
            self._next_sample_timestamp is not None
            and self._next_sample_timestamp <= timestamp + 1e-9
        ):
            samples.append(self._snapshot(self._next_sample_timestamp))
            self._advance_sample_time()
        return tuple(samples)

    def _advance_sample_time(self) -> None:
        assert self._next_sample_timestamp is not None
        self._next_sample_timestamp = round(
            self._next_sample_timestamp + self.sample_period_s,
            9,
        )

    def _snapshot(self, timestamp: float) -> RssiSample:
        assert self._origin_timestamp is not None
        values = tuple(self._values[node] for node in NODE_ORDER)
        ages = []
        stale = []
        for node in NODE_ORDER:
            value_timestamp = self._value_timestamps[node]
            if value_timestamp is None:
                ages.append(None)
                stale.append(True)
                continue
            age_s = max(0.0, timestamp - value_timestamp)
            ages.append(round(age_s * 1000.0, 6))
            stale.append(age_s > self.stale_timeout_s + 1e-9)
        return RssiSample(
            relative_time=round(timestamp - self._origin_timestamp, 9),
            source_timestamp=timestamp,
            values=values,  # type: ignore[arg-type]
            node_age_ms=tuple(ages),  # type: ignore[arg-type]
            stale=tuple(stale),  # type: ignore[arg-type]
        )
