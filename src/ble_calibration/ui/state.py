"""UI-facing state that keeps charts and summaries on one recompute result."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional, Sequence

from ..analysis import (
    DirectionDataset,
    EightDirectionRecomputeService,
    RecomputeResult,
)
from ..cloud import CloudDocument, decode_cloud
from ..domain import Direction


class CalibrationUiState:
    """Own the reversible What-if document and immutable direction datasets."""

    def __init__(
        self,
        original_document: CloudDocument,
        datasets: Sequence[DirectionDataset],
        service: Optional[EightDirectionRecomputeService] = None,
        *,
        current_document: Optional[CloudDocument] = None,
    ) -> None:
        self.original_document = original_document
        self.current_document = current_document or original_document
        self.datasets = tuple(datasets)
        self.service = service or EightDirectionRecomputeService()
        self._using_original = (
            self.current_document.encode_hex() == self.original_document.encode_hex()
        )
        self.result = self._recompute()

    @property
    def using_original(self) -> bool:
        return self._using_original

    def dataset_for(self, direction: Direction) -> Optional[DirectionDataset]:
        return next(
            (
                dataset
                for dataset in self.datasets
                if dataset.record.direction is direction
            ),
            None,
        )

    def replace_cloud_hex(self, hex_text: str) -> RecomputeResult:
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
    ) -> RecomputeResult:
        self.current_document = self.current_document.with_updates(
            unlock_thresholds=unlock_thresholds,
            lock_thresholds=lock_thresholds,
            mst_unlock=mst_unlock,
            strategy_updates=strategy_updates,
        )
        self._using_original = False
        self.result = self._recompute()
        return self.result

    def restore(self) -> RecomputeResult:
        self.current_document = self.original_document
        self._using_original = True
        self.result = self._recompute()
        return self.result

    def update_measurements(
        self,
        direction: Direction,
        lock_distance_m: Optional[float],
        unlock_distance_m: Optional[float],
        walking_speed_mps: float,
    ) -> RecomputeResult:
        updated = []
        found = False
        for dataset in self.datasets:
            if dataset.record.direction is not direction:
                updated.append(dataset)
                continue
            found = True
            updated.append(
                DirectionDataset(
                    record=replace(
                        dataset.record,
                        actual_lock_distance_m=lock_distance_m,
                        actual_unlock_distance_m=unlock_distance_m,
                        walking_speed_mps=walking_speed_mps,
                    ),
                    samples=dataset.samples,
                )
            )
        if not found:
            raise KeyError(direction.value)
        self.datasets = tuple(updated)
        self.result = self._recompute()
        return self.result

    def upsert_dataset(self, dataset: DirectionDataset) -> RecomputeResult:
        retained = tuple(
            item
            for item in self.datasets
            if item.record.direction is not dataset.record.direction
        )
        self.datasets = tuple(
            sorted(
                retained + (dataset,),
                key=lambda item: item.record.direction.index,
            )
        )
        self.result = self._recompute()
        return self.result

    def remove_direction(self, direction: Direction) -> RecomputeResult:
        self.datasets = tuple(
            dataset
            for dataset in self.datasets
            if dataset.record.direction is not direction
        )
        self.result = self._recompute()
        return self.result

    def encoded_hex(self) -> str:
        return self.current_document.encode_hex()

    def _recompute(self) -> RecomputeResult:
        return self.service.recompute(
            self.current_document.parameters,
            self.datasets,
            use_actual_action_times=self._using_original,
        )
