"""Eight-direction What-if recompute service and quality summaries."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..cloud import CloudDocument
from ..cloud.models import CloudParameters
from ..domain.enums import ActionOrigin, Direction, DistanceGrade, EventType
from ..domain.models import (
    ActionPoint,
    DirectionAnalysisResult,
    DirectionRecord,
    RssiSample,
    StrategyEventResult,
)
from ..strategy import StrategyEngine
from .distance import distance_grade, project_action_distance


@dataclass(frozen=True)
class DirectionDataset:
    record: DirectionRecord
    samples: Tuple[RssiSample, ...]


@dataclass(frozen=True)
class QualitySummary:
    total: int
    excellent: int
    good: int
    poor: int
    excellent_rate_percent: Optional[float]
    good_directions: Tuple[Direction, ...]
    poor_directions: Tuple[Direction, ...]
    untriggered_directions: Tuple[Direction, ...]


@dataclass(frozen=True)
class RecomputeResult:
    directions: Mapping[Direction, DirectionAnalysisResult]
    lock_summary: QualitySummary
    unlock_summary: QualitySummary
    elapsed_ms: float


class EightDirectionRecomputeService:
    def __init__(
        self,
        lock_stable_s: float = 2.0,
        unlock_stable_s: float = 0.5,
    ) -> None:
        self.lock_stable_s = lock_stable_s
        self.unlock_stable_s = unlock_stable_s

    def recompute(
        self,
        parameters: CloudParameters,
        datasets: Sequence[DirectionDataset],
        *,
        use_actual_action_times: bool = False,
    ) -> RecomputeResult:
        if len(datasets) > 8:
            raise ValueError("at most eight direction datasets are supported")
        seen = set()
        for dataset in datasets:
            direction = dataset.record.direction
            if direction in seen:
                raise ValueError(f"duplicate direction dataset: {direction.value}")
            seen.add(direction)

        started = time.perf_counter()
        engine = StrategyEngine(
            parameters,
            lock_stable_s=self.lock_stable_s,
            unlock_stable_s=self.unlock_stable_s,
        )
        results: Dict[Direction, DirectionAnalysisResult] = {}
        for dataset in datasets:
            direction = dataset.record.direction
            analyzed = engine.analyze(direction, dataset.samples)
            results[direction] = DirectionAnalysisResult(
                direction=direction,
                lock=self._attach_distance(
                    analyzed.lock,
                    dataset.record,
                    EventType.LOCK,
                    use_actual_action_times,
                ),
                unlock=self._attach_distance(
                    analyzed.unlock,
                    dataset.record,
                    EventType.UNLOCK,
                    use_actual_action_times,
                ),
                analysis_version=analyzed.analysis_version,
            )

        lock_summary = self._summary(
            datasets,
            results,
            EventType.LOCK,
            use_actual_action_times,
        )
        unlock_summary = self._summary(
            datasets,
            results,
            EventType.UNLOCK,
            use_actual_action_times,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return RecomputeResult(results, lock_summary, unlock_summary, elapsed_ms)

    @staticmethod
    def _attach_distance(
        result: Optional[StrategyEventResult],
        record: DirectionRecord,
        event_type: EventType,
        use_actual_action_time: bool,
    ) -> Optional[StrategyEventResult]:
        if result is None:
            return None
        actual_event = record.event(event_type)
        actual_distance = (
            record.actual_lock_distance_m
            if event_type is EventType.LOCK
            else record.actual_unlock_distance_m
        )
        if actual_event is None or actual_distance is None:
            return result
        if use_actual_action_time:
            return replace(
                result,
                action=ActionPoint(
                    event_type=event_type,
                    timestamp=actual_event.timestamp,
                    origin=ActionOrigin.VEHICLE,
                ),
                distance_m=actual_distance,
                grade=distance_grade(event_type, actual_distance),
            )
        projected = project_action_distance(
            event_type=event_type,
            actual_distance_m=actual_distance,
            actual_action_time=actual_event.timestamp,
            calculated_action_time=result.action.timestamp,
            walking_speed_mps=record.walking_speed_mps,
        )
        return replace(
            result,
            distance_m=projected,
            grade=distance_grade(event_type, projected),
        )

    @staticmethod
    def _summary(
        datasets: Sequence[DirectionDataset],
        results: Mapping[Direction, DirectionAnalysisResult],
        event_type: EventType,
        use_actual_action_times: bool,
    ) -> QualitySummary:
        excellent = 0
        good_directions = []
        poor_directions = []
        untriggered_directions = []
        total = 0
        for dataset in datasets:
            direction = dataset.record.direction
            actual_distance = (
                dataset.record.actual_lock_distance_m
                if event_type is EventType.LOCK
                else dataset.record.actual_unlock_distance_m
            )
            if actual_distance is None:
                continue
            total += 1
            analysis = results[direction]
            event_result = (
                analysis.lock if event_type is EventType.LOCK else analysis.unlock
            )
            if event_result is None or event_result.distance_m is None:
                actual_event = dataset.record.event(event_type)
                if use_actual_action_times and actual_event is not None:
                    grade = distance_grade(event_type, actual_distance)
                    if grade is DistanceGrade.EXCELLENT:
                        excellent += 1
                    elif grade is DistanceGrade.GOOD:
                        good_directions.append(direction)
                    else:
                        poor_directions.append(direction)
                else:
                    poor_directions.append(direction)
                    untriggered_directions.append(direction)
            elif event_result.grade is DistanceGrade.EXCELLENT:
                excellent += 1
            elif event_result.grade is DistanceGrade.GOOD:
                good_directions.append(direction)
            else:
                poor_directions.append(direction)
        good = len(good_directions)
        poor = len(poor_directions)
        rate = None if total == 0 else excellent * 100.0 / total
        return QualitySummary(
            total=total,
            excellent=excellent,
            good=good,
            poor=poor,
            excellent_rate_percent=rate,
            good_directions=tuple(good_directions),
            poor_directions=tuple(poor_directions),
            untriggered_directions=tuple(untriggered_directions),
        )


class WhatIfSession:
    """Mutable parameter selection with a lossless one-click restore point."""

    def __init__(
        self,
        original_document: CloudDocument,
        datasets: Sequence[DirectionDataset],
        service: Optional[EightDirectionRecomputeService] = None,
    ) -> None:
        self.original_document = original_document
        self.current_document = original_document
        self.datasets = tuple(datasets)
        self.service = service or EightDirectionRecomputeService()
        self._using_original = True

    def recompute(self) -> RecomputeResult:
        return self.service.recompute(
            self.current_document.parameters,
            self.datasets,
            use_actual_action_times=self._using_original,
        )

    def apply_updates(self, **updates) -> RecomputeResult:
        self.current_document = self.current_document.with_updates(**updates)
        self._using_original = False
        return self.recompute()

    def restore(self) -> RecomputeResult:
        self.current_document = self.original_document
        self._using_original = True
        return self.recompute()

    def encoded_hex(self) -> str:
        return self.current_document.encode_hex()
