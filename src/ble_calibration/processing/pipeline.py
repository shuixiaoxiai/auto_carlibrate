"""Convert raw CAN frames into aligned samples and actual vehicle events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from ..can.protocol import decode_frame
from ..domain.enums import Direction
from ..domain.models import CanFrame, RssiSample, VehicleEvent
from .alignment import RssiTimeAligner
from .events import RequestEdgeDetector


@dataclass(frozen=True)
class FrameProcessingResult:
    decoded: Mapping[str, int]
    samples: Tuple[RssiSample, ...] = ()
    event: Optional[VehicleEvent] = None


class CanFrameProcessor:
    def __init__(
        self,
        sample_rate_hz: float = 10.0,
        stale_timeout_s: float = 0.3,
    ) -> None:
        self.aligner = RssiTimeAligner(sample_rate_hz, stale_timeout_s)
        self.edges = RequestEdgeDetector()

    def reset(self) -> None:
        self.aligner.reset()
        self.edges.reset()

    def process(
        self,
        frame: CanFrame,
        direction: Optional[Direction],
    ) -> FrameProcessingResult:
        decoded = decode_frame(frame.arbitration_id, frame.data)
        out_of_order_before = self.aligner.out_of_order_count
        samples = self.aligner.ingest(frame, decoded)
        if self.aligner.out_of_order_count > out_of_order_before:
            return FrameProcessingResult(decoded, samples)
        event = None
        if "lock_req" in decoded:
            event = self.edges.observe(
                decoded["lock_req"],
                frame.timestamp,
                direction,
            )
        if (
            event is not None
            and self.aligner.origin_timestamp is not None
            and (not samples or samples[-1].source_timestamp < frame.timestamp - 1e-9)
        ):
            samples = samples + (self.aligner.snapshot_at(frame.timestamp),)
        return FrameProcessingResult(decoded, samples, event)
