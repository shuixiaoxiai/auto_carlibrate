"""Deterministic per-direction Mock sources for manual UI recording."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ..can import MemoryCanSource
from ..cloud import demo_cloud_document
from ..domain import Direction
from ..mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
)
from .state import CalibrationUiState


@dataclass(frozen=True)
class MockDistanceHint:
    lock_distance_m: float
    unlock_distance_m: float


class ManualMockProvider:
    def __init__(
        self,
        seed: int = 20260730,
        replay_speed: float = 10.0,
    ) -> None:
        frames, manifest = generate_mock_session(MockConfig(seed=seed))
        self.seed = seed
        self.replay_speed = replay_speed
        self._frames: Dict[Direction, Tuple] = {}
        self._hints: Dict[Direction, MockDistanceHint] = {}
        for item in manifest["directions"]:
            direction = Direction.from_label(str(item["name"]))
            start = float(item["start_time"])
            end = float(item["end_time"])
            self._frames[direction] = tuple(
                frame
                for frame in frames
                if start <= frame.timestamp <= end + 1e-9
            )
            self._hints[direction] = MockDistanceHint(
                float(item["lock_distance_m"]),
                float(item["unlock_distance_m"]),
            )

    def source_for(self, direction: Direction) -> MemoryCanSource:
        return MemoryCanSource(
            self._frames[direction],
            speed=self.replay_speed,
        )

    def distance_hint(self, direction: Direction) -> Optional[MockDistanceHint]:
        return self._hints.get(direction)


def build_manual_demo(
    seed: int = 20260730,
    replay_speed: float = 10.0,
) -> Tuple[CalibrationUiState, ManualMockProvider]:
    document = demo_cloud_document(
        REFERENCE_UNLOCK_THRESHOLDS,
        REFERENCE_LOCK_THRESHOLDS,
    )
    return (
        CalibrationUiState(document, ()),
        ManualMockProvider(seed=seed, replay_speed=replay_speed),
    )
