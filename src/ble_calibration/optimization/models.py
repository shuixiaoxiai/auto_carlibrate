"""Immutable models for deterministic automatic threshold optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..cloud.models import CloudParameters
from ..domain.enums import Direction, DistanceGrade, Node, StrategyKind


@dataclass(frozen=True)
class OptimizationConfig:
    """Frozen business constraints and bounded-search controls."""

    threshold_radius_db: int = 10
    threshold_min: int = -128
    threshold_max: int = -1
    threshold_step_db: int = 1
    minimum_gap_db: int = 3
    minimum_excellent_rate_percent: float = 75.0
    minimum_new_unlock_distance_m: float = 1.0
    maximum_sweeps: int = 4
    maximum_evaluations: int = 4000
    allow_strategy_fallback: bool = True
    robustness_delta_db: int = 1

    def __post_init__(self) -> None:
        if self.threshold_radius_db < 0:
            raise ValueError("threshold_radius_db cannot be negative")
        if self.threshold_min >= self.threshold_max:
            raise ValueError("threshold_min must be less than threshold_max")
        if self.threshold_max >= 0:
            raise ValueError("optimizer must not generate zero thresholds")
        if self.threshold_step_db <= 0:
            raise ValueError("threshold_step_db must be positive")
        if self.minimum_gap_db < 0:
            raise ValueError("minimum_gap_db cannot be negative")
        if not 0.0 <= self.minimum_excellent_rate_percent <= 100.0:
            raise ValueError("minimum excellent rate must be between 0 and 100")
        if self.minimum_new_unlock_distance_m < 0:
            raise ValueError("minimum unlock distance cannot be negative")
        if self.maximum_sweeps <= 0 or self.maximum_evaluations <= 0:
            raise ValueError("search limits must be positive")
        if self.robustness_delta_db <= 0:
            raise ValueError("robustness_delta_db must be positive")


@dataclass(frozen=True)
class OptimizationReadiness:
    can_start: bool
    total_datasets: int
    eligible_datasets: int
    eligible_directions: int
    skipped_labels: Tuple[str, ...]
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...]

    @property
    def low_confidence(self) -> bool:
        return self.eligible_directions < 8 or self.eligible_datasets < 24


@dataclass(frozen=True)
class SampleOutcome:
    direction: Direction
    group_index: int
    recording_id: str
    lock_distance_m: Optional[float]
    unlock_distance_m: Optional[float]
    lock_grade: DistanceGrade
    unlock_grade: DistanceGrade
    lock_trigger_node: Optional[Node]
    unlock_trigger_node: Optional[Node]
    lock_strategy: Optional[StrategyKind]
    unlock_strategy: Optional[StrategyKind]
    lock_untriggered: bool
    unlock_untriggered: bool

    @property
    def label(self) -> str:
        return f"{self.direction.label} · 第 {self.group_index} 组"


@dataclass(frozen=True)
class OptimizationMetrics:
    lock_total: int
    lock_excellent: int
    lock_good: int
    lock_poor: int
    lock_untriggered: int
    unlock_total: int
    unlock_excellent: int
    unlock_good: int
    unlock_poor: int
    unlock_untriggered: int
    lock_excellent_rate_percent: float
    unlock_excellent_rate_percent: float
    ordering_violations: int
    near_unlock_violations: int
    total_distance_penalty_m: float
    worst_distance_penalty_m: float
    minimum_unlock_distance_m: Optional[float]
    mean_lock_distance_m: Optional[float]


@dataclass(frozen=True)
class CandidateEvaluation:
    parameters: CloudParameters
    samples: Tuple[SampleOutcome, ...]
    metrics: OptimizationMetrics
    violations: Tuple[str, ...]
    feasible: bool
    score: Tuple[float, ...]
    strategy_kind: Optional[StrategyKind]

    @property
    def main_gap_db(self) -> int:
        return (
            self.parameters.unlock_thresholds[Node.MASTER.index]
            - self.parameters.lock_thresholds[Node.MASTER.index]
        )


@dataclass(frozen=True)
class OptimizationProgress:
    phase: str
    message: str
    evaluated_candidates: int
    maximum_evaluations: int
    best_lock_rate_percent: float
    best_unlock_rate_percent: float


@dataclass(frozen=True)
class OptimizationResult:
    baseline: CandidateEvaluation
    recommendation: CandidateEvaluation
    readiness: OptimizationReadiness
    evaluated_candidates: int
    elapsed_ms: float
    stop_reason: str
    robustness_passed: int
    robustness_total: int

    @property
    def can_apply(self) -> bool:
        return self.recommendation.feasible
