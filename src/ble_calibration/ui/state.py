"""UI-facing state for fixed three-group charts, means, and summaries."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence, Tuple

from ..analysis import (
    DirectionDataset,
    EightDirectionRecomputeService,
    GroupedRecomputeResult,
    MeanDirectionView,
    ThreeGroupRecomputeService,
)
from ..cloud import CloudDocument, decode_cloud
from ..domain import MAX_DIRECTION_GROUPS, Direction


class CalibrationUiState:
    """Own reversible What-if parameters and immutable grouped datasets."""

    def __init__(
        self,
        original_document: CloudDocument,
        datasets: Sequence[DirectionDataset],
        service: Optional[EightDirectionRecomputeService] = None,
        *,
        current_document: Optional[CloudDocument] = None,
        default_walking_speed_mps: float = 1.0,
    ) -> None:
        self.original_document = original_document
        self.current_document = current_document or original_document
        self.datasets = self._sorted(datasets)
        self.service = ThreeGroupRecomputeService(service)
        self.default_walking_speed_mps = self._validated_speed(
            default_walking_speed_mps
        )
        self._using_original = (
            self.current_document.encode_hex() == self.original_document.encode_hex()
        )
        self.result = self._recompute()

    @property
    def using_original(self) -> bool:
        return self._using_original

    @property
    def record_count(self) -> int:
        return len(self.datasets)

    @property
    def recorded_directions(self) -> Tuple[Direction, ...]:
        present = {dataset.record.direction for dataset in self.datasets}
        return tuple(direction for direction in Direction if direction in present)

    def dataset_for(
        self,
        direction: Direction,
        group_index: Optional[int] = None,
    ) -> Optional[DirectionDataset]:
        if group_index is None:
            group_index = self.latest_group_for(direction)
        if group_index is None:
            return None
        return next(
            (
                dataset
                for dataset in self.datasets
                if dataset.record.direction is direction
                and dataset.record.group_index == group_index
            ),
            None,
        )

    def group_indices(self, direction: Direction) -> Tuple[int, ...]:
        return tuple(
            dataset.record.group_index
            for dataset in self.datasets
            if dataset.record.direction is direction
        )

    def latest_group_for(self, direction: Direction) -> Optional[int]:
        candidates = tuple(
            dataset
            for dataset in self.datasets
            if dataset.record.direction is direction
        )
        if not candidates:
            return None
        latest = max(
            candidates,
            key=lambda dataset: (
                self._recorded_timestamp(dataset.record.recorded_at),
                dataset.record.group_index,
            ),
        )
        return latest.record.group_index

    def next_capture_group(self, direction: Direction) -> int:
        occupied = set(self.group_indices(direction))
        for group_index in range(1, MAX_DIRECTION_GROUPS + 1):
            if group_index not in occupied:
                return group_index
        return MAX_DIRECTION_GROUPS

    def mean_for(self, direction: Direction) -> Optional[MeanDirectionView]:
        return self.result.mean_directions.get(direction)

    def replace_cloud_hex(self, hex_text: str) -> GroupedRecomputeResult:
        document = decode_cloud(hex_text)
        self.original_document = document
        self.current_document = document
        self._using_original = True
        self.result = self._recompute()
        return self.result

    def apply_updates(
        self,
        *,
        unlock_thresholds: Sequence[int],
        lock_thresholds: Sequence[int],
        mst_unlock: Optional[Sequence[int]] = None,
        strategy_updates: Optional[Mapping[str, Mapping[str, int]]] = None,
    ) -> GroupedRecomputeResult:
        self.current_document = self.current_document.with_updates(
            unlock_thresholds=unlock_thresholds,
            lock_thresholds=lock_thresholds,
            mst_unlock=mst_unlock,
            strategy_updates=strategy_updates,
        )
        self._using_original = False
        self.result = self._recompute()
        return self.result

    def restore(self) -> GroupedRecomputeResult:
        self.current_document = self.original_document
        self._using_original = True
        self.result = self._recompute()
        return self.result

    def update_default_walking_speed(self, walking_speed_mps: float) -> None:
        self.default_walking_speed_mps = self._validated_speed(walking_speed_mps)

    def update_measurements(
        self,
        direction: Direction,
        lock_distance_m: Optional[float],
        unlock_distance_m: Optional[float],
        walking_speed_mps: float,
        group_index: Optional[int] = None,
    ) -> GroupedRecomputeResult:
        target_group = (
            self.latest_group_for(direction) if group_index is None else group_index
        )
        if target_group is None:
            raise KeyError(direction.value)
        updated = []
        found = False
        for dataset in self.datasets:
            if (
                dataset.record.direction is not direction
                or dataset.record.group_index != target_group
            ):
                updated.append(dataset)
                continue
            found = True
            updated.append(
                DirectionDataset(
                    record=replace(
                        dataset.record,
                        actual_lock_distance_m=lock_distance_m,
                        actual_unlock_distance_m=unlock_distance_m,
                        walking_speed_mps=self._validated_speed(walking_speed_mps),
                    ),
                    samples=dataset.samples,
                )
            )
        if not found:
            raise KeyError(f"{direction.value}/group-{target_group}")
        self.datasets = self._sorted(updated)
        self.result = self._recompute()
        return self.result

    def upsert_dataset(self, dataset: DirectionDataset) -> GroupedRecomputeResult:
        key = (dataset.record.direction, dataset.record.group_index)
        retained = tuple(
            item
            for item in self.datasets
            if (item.record.direction, item.record.group_index) != key
        )
        self.datasets = self._sorted(retained + (dataset,))
        self.result = self._recompute()
        return self.result

    def remove_dataset(
        self,
        direction: Direction,
        group_index: int,
    ) -> GroupedRecomputeResult:
        self.datasets = self._sorted(
            dataset
            for dataset in self.datasets
            if not (
                dataset.record.direction is direction
                and dataset.record.group_index == group_index
            )
        )
        self.result = self._recompute()
        return self.result

    def remove_direction(self, direction: Direction) -> GroupedRecomputeResult:
        """Backward-compatible removal of every group for one direction."""
        self.datasets = self._sorted(
            dataset
            for dataset in self.datasets
            if dataset.record.direction is not direction
        )
        self.result = self._recompute()
        return self.result

    def encoded_hex(self) -> str:
        return self.current_document.encode_hex()

    def _recompute(self) -> GroupedRecomputeResult:
        return self.service.recompute(
            self.current_document.parameters,
            self.datasets,
            use_actual_action_times=self._using_original,
        )

    @staticmethod
    def _sorted(datasets) -> Tuple[DirectionDataset, ...]:
        return tuple(
            sorted(
                tuple(datasets),
                key=lambda item: (
                    item.record.direction.index,
                    item.record.group_index,
                ),
            )
        )

    @staticmethod
    def _validated_speed(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or not 0.1 <= value <= 5.0:
            raise ValueError("walking_speed_mps must be between 0.1 and 5.0")
        return value

    @staticmethod
    def _recorded_timestamp(value: Optional[str]) -> float:
        if value is None:
            return float("-inf")
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            recorded_at = datetime.fromisoformat(normalized)
        except ValueError:
            return float("-inf")
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return recorded_at.timestamp()
