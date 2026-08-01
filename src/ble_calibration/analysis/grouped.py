"""Fixed three-group recompute orchestration and mean chart projection."""

from __future__ import annotations

import math
import time
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from statistics import fmean
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..cloud.models import CloudParameters
from ..domain.enums import ActionOrigin, Direction, DirectionStatus, EventType
from ..domain.models import (
    MAX_DIRECTION_GROUPS,
    ActionPoint,
    ConditionPoint,
    DirectionAnalysisResult,
    DirectionRecord,
    RssiSample,
    StrategyEventResult,
    VehicleEvent,
)
from .distance import distance_grade
from .recompute import (
    DirectionDataset,
    EightDirectionRecomputeService,
    QualitySummary,
    RecomputeResult,
)

GROUP_LABELS = {1: "第一组", 2: "第二组", 3: "第三组"}


@dataclass(frozen=True)
class DirectionGroupRef:
    """A summary item that distinguishes repeated measurements by group."""

    direction: Direction
    group_index: int

    @property
    def label(self) -> str:
        return f"{self.direction.label} · {GROUP_LABELS[self.group_index]}"

    @property
    def value(self) -> str:
        return f"{self.direction.value}:group-{self.group_index}"


@dataclass(frozen=True)
class MeanDirectionView:
    direction: Direction
    dataset: DirectionDataset
    analysis: DirectionAnalysisResult
    group_count: int
    lock_result_count: int
    unlock_result_count: int


@dataclass(frozen=True)
class GroupedRecomputeResult:
    group_results: Mapping[int, RecomputeResult]
    mean_directions: Mapping[Direction, MeanDirectionView]
    lock_summary: QualitySummary
    unlock_summary: QualitySummary
    elapsed_ms: float

    @property
    def directions(self) -> Mapping[Direction, DirectionAnalysisResult]:
        """Backward-compatible first-group view for non-group-aware callers."""
        first = self.group_results.get(1)
        return {} if first is None else first.directions

    def analysis_for(
        self,
        direction: Direction,
        group_index: int,
    ) -> Optional[DirectionAnalysisResult]:
        result = self.group_results.get(group_index)
        return None if result is None else result.directions.get(direction)


class ThreeGroupRecomputeService:
    """Run the existing eight-direction engine once per fixed group."""

    def __init__(
        self,
        single_service: Optional[EightDirectionRecomputeService] = None,
        *,
        sample_interval_s: float = 0.1,
        interpolation_gap_s: float = 0.25,
    ) -> None:
        if sample_interval_s <= 0:
            raise ValueError("sample_interval_s must be greater than zero")
        if interpolation_gap_s <= 0:
            raise ValueError("interpolation_gap_s must be greater than zero")
        self.single_service = single_service or EightDirectionRecomputeService()
        self.sample_interval_s = sample_interval_s
        self.interpolation_gap_s = interpolation_gap_s

    def recompute(
        self,
        parameters: CloudParameters,
        datasets: Sequence[DirectionDataset],
        *,
        use_actual_action_times: bool = False,
    ) -> GroupedRecomputeResult:
        seen = set()
        grouped: Dict[int, list[DirectionDataset]] = {
            index: [] for index in range(1, MAX_DIRECTION_GROUPS + 1)
        }
        for dataset in datasets:
            key = (dataset.record.direction, dataset.record.group_index)
            if key in seen:
                raise ValueError(
                    "duplicate direction group dataset: "
                    f"{key[0].value}/{key[1]}"
                )
            seen.add(key)
            grouped[dataset.record.group_index].append(dataset)

        started = time.perf_counter()
        group_results: Dict[int, RecomputeResult] = {}
        for group_index, items in grouped.items():
            if not items:
                continue
            ordered = tuple(
                sorted(items, key=lambda item: item.record.direction.index)
            )
            group_results[group_index] = self.single_service.recompute(
                parameters,
                ordered,
                use_actual_action_times=use_actual_action_times,
            )

        lock_summary = self._aggregate_summary(group_results, EventType.LOCK)
        unlock_summary = self._aggregate_summary(group_results, EventType.UNLOCK)
        mean_directions = self._build_means(datasets, group_results)
        return GroupedRecomputeResult(
            group_results=group_results,
            mean_directions=mean_directions,
            lock_summary=lock_summary,
            unlock_summary=unlock_summary,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    @staticmethod
    def _aggregate_summary(
        group_results: Mapping[int, RecomputeResult],
        event_type: EventType,
    ) -> QualitySummary:
        total = excellent = 0
        good_refs = []
        poor_refs = []
        untriggered_refs = []
        for group_index, result in sorted(group_results.items()):
            summary = (
                result.lock_summary
                if event_type is EventType.LOCK
                else result.unlock_summary
            )
            total += summary.total
            excellent += summary.excellent
            good_refs.extend(
                DirectionGroupRef(direction, group_index)
                for direction in summary.good_directions
            )
            poor_refs.extend(
                DirectionGroupRef(direction, group_index)
                for direction in summary.poor_directions
            )
            untriggered_refs.extend(
                DirectionGroupRef(direction, group_index)
                for direction in summary.untriggered_directions
            )
        return QualitySummary(
            total=total,
            excellent=excellent,
            good=len(good_refs),
            poor=len(poor_refs),
            excellent_rate_percent=(
                None if total == 0 else excellent * 100.0 / total
            ),
            good_directions=tuple(good_refs),
            poor_directions=tuple(poor_refs),
            untriggered_directions=tuple(untriggered_refs),
        )

    def _build_means(
        self,
        datasets: Sequence[DirectionDataset],
        group_results: Mapping[int, RecomputeResult],
    ) -> Mapping[Direction, MeanDirectionView]:
        output: Dict[Direction, MeanDirectionView] = {}
        for direction in Direction:
            items = tuple(
                sorted(
                    (
                        dataset
                        for dataset in datasets
                        if dataset.record.direction is direction
                    ),
                    key=lambda dataset: dataset.record.group_index,
                )
            )
            if not items or not any(dataset.samples for dataset in items):
                continue
            analyzed_items = tuple(
                (dataset, analysis)
                for dataset in items
                for analysis in (
                    self._analysis_for(group_results, dataset.record),
                )
                if analysis is not None
            )
            mean_dataset = self._mean_dataset(direction, items)
            lock = self._mean_event(analyzed_items, EventType.LOCK)
            unlock = self._mean_event(analyzed_items, EventType.UNLOCK)
            output[direction] = MeanDirectionView(
                direction=direction,
                dataset=mean_dataset,
                analysis=DirectionAnalysisResult(
                    direction=direction,
                    lock=lock,
                    unlock=unlock,
                ),
                group_count=len(items),
                lock_result_count=sum(
                    analysis.lock is not None for _, analysis in analyzed_items
                ),
                unlock_result_count=sum(
                    analysis.unlock is not None for _, analysis in analyzed_items
                ),
            )
        return output

    @staticmethod
    def _analysis_for(
        group_results: Mapping[int, RecomputeResult],
        record: DirectionRecord,
    ) -> Optional[DirectionAnalysisResult]:
        result = group_results.get(record.group_index)
        return None if result is None else result.directions.get(record.direction)

    def _mean_dataset(
        self,
        direction: Direction,
        datasets: Sequence[DirectionDataset],
    ) -> DirectionDataset:
        sampled = tuple(dataset for dataset in datasets if dataset.samples)
        origins = tuple(self._origin(dataset) for dataset in sampled)
        durations = tuple(
            dataset.samples[-1].source_timestamp - origin
            for dataset, origin in zip(sampled, origins)
        )
        common_duration = max(0.0, min(durations))
        step_count = int(math.floor(common_duration / self.sample_interval_s)) + 1
        node_points = tuple(
            tuple(self._valid_points(dataset, origin, node_index) for node_index in range(5))
            for dataset, origin in zip(sampled, origins)
        )
        mean_samples = []
        for step_index in range(step_count):
            timestamp = round(step_index * self.sample_interval_s, 9)
            values = []
            for node_index in range(5):
                candidates = [
                    value
                    for points in node_points
                    for value in (self._interpolate(points[node_index], timestamp),)
                    if value is not None
                ]
                values.append(None if not candidates else round(fmean(candidates), 2))
            mean_samples.append(
                RssiSample(
                    relative_time=timestamp,
                    source_timestamp=timestamp,
                    values=tuple(values),
                    node_age_ms=tuple(
                        None if value is None else 0.0 for value in values
                    ),
                    stale=tuple(value is None for value in values),
                )
            )

        lock_distance = self._mean_optional(
            dataset.record.actual_lock_distance_m for dataset in datasets
        )
        unlock_distance = self._mean_optional(
            dataset.record.actual_unlock_distance_m for dataset in datasets
        )
        walking_speed = fmean(
            dataset.record.walking_speed_mps for dataset in datasets
        )
        events = []
        for event_type in (EventType.LOCK, EventType.UNLOCK):
            relative_times = []
            for dataset in datasets:
                event = dataset.record.event(event_type)
                if event is not None:
                    relative_times.append(event.timestamp - self._origin(dataset))
            if relative_times:
                events.append(
                    VehicleEvent.from_request(
                        event_type.request_value,
                        max(0.0, fmean(relative_times)),
                        direction,
                    )
                )
        status = (
            DirectionStatus.COMPLETE
            if all(dataset.record.status is DirectionStatus.COMPLETE for dataset in datasets)
            else DirectionStatus.INCOMPLETE
        )
        record = DirectionRecord(
            direction=direction,
            status=status,
            start_timestamp=0.0,
            end_timestamp=common_duration,
            walking_speed_mps=walking_speed,
            actual_lock_distance_m=lock_distance,
            actual_unlock_distance_m=unlock_distance,
            vehicle_events=tuple(events),
            sample_count=len(mean_samples),
            group_index=1,
            recording_id=f"mean-{direction.value}",
            recorded_at=None,
        )
        return DirectionDataset(record=record, samples=tuple(mean_samples))

    def _mean_event(
        self,
        analyzed_items: Sequence[Tuple[DirectionDataset, DirectionAnalysisResult]],
        event_type: EventType,
    ) -> Optional[StrategyEventResult]:
        results = []
        for dataset, analysis in analyzed_items:
            result = analysis.lock if event_type is EventType.LOCK else analysis.unlock
            if result is None:
                continue
            origin = self._origin(dataset)
            results.append((result, origin))
        if not results:
            return None

        condition_time = fmean(
            result.condition.timestamp - origin for result, origin in results
        )
        action_time = fmean(
            result.action.timestamp - origin for result, origin in results
        )
        trigger_node = Counter(
            result.condition.trigger_node for result, _ in results
        ).most_common(1)[0][0]
        strategy = Counter(
            result.condition.strategy for result, _ in results
        ).most_common(1)[0][0]
        rssi = tuple(
            self._rounded_mean(
                result.condition.rssi[node_index] for result, _ in results
            )
            for node_index in range(5)
        )
        origins = {result.action.origin for result, _ in results}
        action_origin = (
            next(iter(origins)) if len(origins) == 1 else ActionOrigin.CALCULATED
        )
        distance = self._mean_optional(result.distance_m for result, _ in results)
        return StrategyEventResult(
            condition=ConditionPoint(
                event_type=event_type,
                timestamp=max(0.0, condition_time),
                trigger_node=trigger_node,
                strategy=strategy,
                rssi=rssi,
            ),
            action=ActionPoint(
                event_type=event_type,
                timestamp=max(condition_time, action_time, 0.0),
                origin=action_origin,
            ),
            distance_m=distance,
            grade=(
                distance_grade(event_type, distance)
                if distance is not None
                else results[0][0].grade
            ),
        )

    @staticmethod
    def _origin(dataset: DirectionDataset) -> float:
        if dataset.record.start_timestamp is not None:
            return dataset.record.start_timestamp
        return 0.0 if not dataset.samples else dataset.samples[0].source_timestamp

    @staticmethod
    def _valid_points(
        dataset: DirectionDataset,
        origin: float,
        node_index: int,
    ) -> Tuple[Tuple[float, float], ...]:
        return tuple(
            (sample.source_timestamp - origin, float(sample.values[node_index]))
            for sample in dataset.samples
            if sample.values[node_index] is not None and not sample.stale[node_index]
        )

    def _interpolate(
        self,
        points: Sequence[Tuple[float, float]],
        timestamp: float,
    ) -> Optional[float]:
        if not points:
            return None
        times = [point[0] for point in points]
        index = bisect_left(times, timestamp)
        if index < len(points) and abs(points[index][0] - timestamp) <= 1e-9:
            return points[index][1]
        if index == 0 or index == len(points):
            nearest = points[0] if index == 0 else points[-1]
            return (
                nearest[1]
                if abs(nearest[0] - timestamp) <= self.sample_interval_s / 2
                else None
            )
        left_time, left_value = points[index - 1]
        right_time, right_value = points[index]
        if right_time - left_time > self.interpolation_gap_s:
            return None
        ratio = (timestamp - left_time) / (right_time - left_time)
        return left_value + (right_value - left_value) * ratio

    @staticmethod
    def _mean_optional(values) -> Optional[float]:
        present = [float(value) for value in values if value is not None]
        return None if not present else fmean(present)

    @staticmethod
    def _rounded_mean(values) -> Optional[int]:
        present = [float(value) for value in values if value is not None]
        return None if not present else int(round(fmean(present)))
