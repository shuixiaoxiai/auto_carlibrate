"""Exact candidate replay and lexicographic business scoring."""

from __future__ import annotations

from dataclasses import replace
from statistics import fmean
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ..analysis import DirectionDataset, distance_grade, project_action_distance
from ..cloud.models import CloudParameters
from ..domain.enums import DistanceGrade, EventType, NODE_ORDER, Node, StrategyKind
from ..strategy import StrategyEngine
from .constraints import thresholds_are_legal
from .models import (
    CandidateEvaluation,
    OptimizationConfig,
    OptimizationMetrics,
    SampleOutcome,
)


def parameters_key(parameters: CloudParameters) -> Tuple[object, ...]:
    return (
        parameters.unlock_thresholds,
        parameters.lock_thresholds,
        parameters.mst_unlock,
        _mapping_key(parameters.quick_lock),
        _mapping_key(parameters.quick_unlock),
        _mapping_key(parameters.mst_than_slave),
        _mapping_key(parameters.bevel_angle),
    )


def _mapping_key(values: Optional[Mapping[str, int]]) -> Optional[Tuple[Tuple[str, int], ...]]:
    return None if values is None else tuple(sorted((key, int(value)) for key, value in values.items()))


def strategy_kind(parameters: CloudParameters) -> Optional[StrategyKind]:
    enabled = []
    if parameters.mst_unlock and any(parameters.mst_unlock):
        enabled.append(StrategyKind.MASTER_UNLOCK)
    if _quick_lock_enabled(parameters):
        enabled.append(StrategyKind.QUICK_LOCK)
    if parameters.quick_unlock and int(parameters.quick_unlock.get("unlockTime", 0)) > 0:
        enabled.append(StrategyKind.QUICK_UNLOCK)
    if parameters.mst_than_slave and int(parameters.mst_than_slave.get("diff", 0)) > 0:
        enabled.append(StrategyKind.MASTER_THAN_SLAVE)
    if parameters.bevel_angle and any(int(value) for value in parameters.bevel_angle.values()):
        enabled.append(StrategyKind.BEVEL_ANGLE)
    return enabled[0] if len(enabled) == 1 else None


def _quick_lock_enabled(parameters: CloudParameters) -> bool:
    config = parameters.quick_lock
    if not config:
        return False
    weak_fields = {
        Node.FRONT: "weakFront",
        Node.REAR: "weakRear",
        Node.LEFT: "weakFl",
        Node.RIGHT: "weakFr",
    }
    strong_fields = {
        Node.MASTER: "strongMst",
        Node.FRONT: "strongFront",
        Node.REAR: "strongRear",
        Node.LEFT: "strongFl",
        Node.RIGHT: "strongFr",
    }
    enabled_nodes = tuple(
        node
        for node in NODE_ORDER
        if parameters.lock_thresholds[node.index] != 0
    )
    return any(
        weak_node in enabled_nodes
        and int(config.get(weak_field, 0)) > 0
        and any(
            node is not weak_node
            and node in enabled_nodes
            and int(config.get(strong_fields[node], 0)) > 0
            for node in NODE_ORDER
        )
        for weak_node, weak_field in weak_fields.items()
    )


class CandidateEvaluator:
    """Replay candidates with the production engine and cache per raw group."""

    def __init__(
        self,
        source_parameters: CloudParameters,
        datasets: Sequence[DirectionDataset],
        config: OptimizationConfig,
    ) -> None:
        self.source_parameters = source_parameters
        self.datasets = tuple(datasets)
        self.config = config
        self._sample_cache: Dict[Tuple[Tuple[object, ...], str], SampleOutcome] = {}
        self._full_cache: Dict[Tuple[object, ...], CandidateEvaluation] = {}
        self._baseline_unlocks: Dict[str, Optional[float]] = {}

    def set_baseline(self, evaluation: CandidateEvaluation) -> None:
        self._baseline_unlocks = {
            sample.recording_id: sample.unlock_distance_m
            for sample in evaluation.samples
        }

    def evaluate(
        self,
        parameters: CloudParameters,
        *,
        incumbent: Optional[CandidateEvaluation] = None,
        affected: Optional[Iterable[Direction]] = None,
    ) -> CandidateEvaluation:
        key = parameters_key(parameters)
        if affected is None and key in self._full_cache:
            return self._full_cache[key]
        affected_set = None if affected is None else set(affected)
        incumbent_samples = (
            {}
            if incumbent is None
            else {sample.recording_id: sample for sample in incumbent.samples}
        )
        samples = []
        for dataset in self.datasets:
            if (
                affected_set is not None
                and dataset.record.direction not in affected_set
                and dataset.record.recording_id in incumbent_samples
            ):
                samples.append(incumbent_samples[dataset.record.recording_id])
                continue
            cache_key = (key, dataset.record.recording_id)
            sample = self._sample_cache.get(cache_key)
            if sample is None:
                sample = self._replay_sample(parameters, dataset)
                self._sample_cache[cache_key] = sample
            samples.append(sample)
        evaluation = self._score(parameters, tuple(samples))
        if affected is None:
            self._full_cache[key] = evaluation
        return evaluation

    @staticmethod
    def _replay_sample(
        parameters: CloudParameters,
        dataset: DirectionDataset,
    ) -> SampleOutcome:
        analysis = StrategyEngine(parameters).analyze(
            dataset.record.direction,
            dataset.samples,
        )
        lock_distance, lock_grade_value = _event_distance(
            dataset,
            analysis.lock,
            EventType.LOCK,
        )
        unlock_distance, unlock_grade_value = _event_distance(
            dataset,
            analysis.unlock,
            EventType.UNLOCK,
        )
        return SampleOutcome(
            direction=dataset.record.direction,
            group_index=dataset.record.group_index,
            recording_id=dataset.record.recording_id,
            lock_distance_m=lock_distance,
            unlock_distance_m=unlock_distance,
            lock_grade=lock_grade_value,
            unlock_grade=unlock_grade_value,
            lock_trigger_node=(
                None if analysis.lock is None else analysis.lock.condition.trigger_node
            ),
            unlock_trigger_node=(
                None if analysis.unlock is None else analysis.unlock.condition.trigger_node
            ),
            lock_strategy=(
                None if analysis.lock is None else analysis.lock.condition.strategy
            ),
            unlock_strategy=(
                None if analysis.unlock is None else analysis.unlock.condition.strategy
            ),
            lock_untriggered=analysis.lock is None or lock_distance is None,
            unlock_untriggered=analysis.unlock is None or unlock_distance is None,
        )

    def _score(
        self,
        parameters: CloudParameters,
        samples: Tuple[SampleOutcome, ...],
    ) -> CandidateEvaluation:
        lock_excellent = sum(sample.lock_grade is DistanceGrade.EXCELLENT for sample in samples)
        lock_good = sum(sample.lock_grade is DistanceGrade.GOOD for sample in samples)
        lock_poor = len(samples) - lock_excellent - lock_good
        unlock_excellent = sum(sample.unlock_grade is DistanceGrade.EXCELLENT for sample in samples)
        unlock_good = sum(sample.unlock_grade is DistanceGrade.GOOD for sample in samples)
        unlock_poor = len(samples) - unlock_excellent - unlock_good
        lock_rate = 0.0 if not samples else lock_excellent * 100.0 / len(samples)
        unlock_rate = 0.0 if not samples else unlock_excellent * 100.0 / len(samples)
        ordering_violations = sum(
            sample.lock_distance_m is not None
            and sample.unlock_distance_m is not None
            and sample.lock_distance_m <= sample.unlock_distance_m
            for sample in samples
        )
        near_violations = sum(self._is_near_unlock_violation(sample) for sample in samples)
        penalties = tuple(
            penalty
            for sample in samples
            for penalty in (
                _distance_penalty(EventType.LOCK, sample.lock_distance_m),
                _distance_penalty(EventType.UNLOCK, sample.unlock_distance_m),
            )
        )
        unlock_distances = tuple(
            sample.unlock_distance_m
            for sample in samples
            if sample.unlock_distance_m is not None
        )
        lock_distances = tuple(
            sample.lock_distance_m
            for sample in samples
            if sample.lock_distance_m is not None
        )
        metrics = OptimizationMetrics(
            lock_total=len(samples),
            lock_excellent=lock_excellent,
            lock_good=lock_good,
            lock_poor=lock_poor,
            lock_untriggered=sum(sample.lock_untriggered for sample in samples),
            unlock_total=len(samples),
            unlock_excellent=unlock_excellent,
            unlock_good=unlock_good,
            unlock_poor=unlock_poor,
            unlock_untriggered=sum(sample.unlock_untriggered for sample in samples),
            lock_excellent_rate_percent=lock_rate,
            unlock_excellent_rate_percent=unlock_rate,
            ordering_violations=ordering_violations,
            near_unlock_violations=near_violations,
            total_distance_penalty_m=sum(penalties),
            worst_distance_penalty_m=max(penalties, default=0.0),
            minimum_unlock_distance_m=(
                min(unlock_distances) if unlock_distances else None
            ),
            mean_lock_distance_m=(fmean(lock_distances) if lock_distances else None),
        )
        legal = thresholds_are_legal(parameters, self.config)
        strategy_legal = _strategy_configuration_legal(parameters)
        feasible = (
            legal
            and strategy_legal
            and bool(samples)
            and near_violations == 0
            and ordering_violations == 0
            and lock_poor == 0
            and unlock_poor == 0
            and lock_rate + 1e-9 >= self.config.minimum_excellent_rate_percent
            and unlock_rate + 1e-9 >= self.config.minimum_excellent_rate_percent
        )
        violations = _violation_messages(
            metrics,
            legal,
            strategy_legal,
            self.config,
        )
        total_change, changed_nodes = _change_metrics(parameters, self.source_parameters)
        main_gap = (
            parameters.unlock_thresholds[Node.MASTER.index]
            - parameters.lock_thresholds[Node.MASTER.index]
        )
        score = (
            float(feasible),
            float(-near_violations),
            float(-ordering_violations),
            float(-(lock_poor + unlock_poor)),
            min(lock_rate, unlock_rate),
            float(lock_excellent + unlock_excellent),
            (
                -1.0
                if metrics.minimum_unlock_distance_m is None
                else metrics.minimum_unlock_distance_m
            ),
            (
                -1.0
                if metrics.mean_lock_distance_m is None
                else metrics.mean_lock_distance_m
            ),
            -metrics.total_distance_penalty_m,
            -metrics.worst_distance_penalty_m,
            float(main_gap),
            float(-total_change),
            float(-changed_nodes),
        )
        return CandidateEvaluation(
            parameters=parameters,
            samples=samples,
            metrics=metrics,
            violations=violations,
            feasible=feasible,
            score=score,
            strategy_kind=strategy_kind(parameters),
        )

    def _is_near_unlock_violation(self, sample: SampleOutcome) -> bool:
        distance = sample.unlock_distance_m
        if distance is None:
            return False
        baseline = self._baseline_unlocks.get(sample.recording_id)
        if baseline is not None and baseline < self.config.minimum_new_unlock_distance_m:
            return distance + 1e-9 < baseline
        return distance + 1e-9 < self.config.minimum_new_unlock_distance_m


def _event_distance(dataset, result, event_type: EventType):
    if result is None:
        return None, DistanceGrade.POOR
    actual_event = dataset.record.event(event_type)
    actual_distance = (
        dataset.record.actual_lock_distance_m
        if event_type is EventType.LOCK
        else dataset.record.actual_unlock_distance_m
    )
    if actual_event is None or actual_distance is None:
        return None, DistanceGrade.POOR
    distance = project_action_distance(
        event_type=event_type,
        actual_distance_m=actual_distance,
        actual_action_time=actual_event.timestamp,
        calculated_action_time=result.action.timestamp,
        walking_speed_mps=dataset.record.walking_speed_mps,
    )
    return distance, distance_grade(event_type, distance)


def _distance_penalty(event_type: EventType, distance: Optional[float]) -> float:
    if distance is None:
        return 100.0
    lower, upper = (8.0, 12.0) if event_type is EventType.LOCK else (2.0, 5.0)
    if distance < lower:
        return lower - distance
    if distance > upper:
        return distance - upper
    return 0.0


def _change_metrics(
    parameters: CloudParameters,
    source: CloudParameters,
) -> Tuple[int, int]:
    total = 0
    changed = 0
    for node in NODE_ORDER:
        deltas = (
            abs(parameters.lock_thresholds[node.index] - source.lock_thresholds[node.index]),
            abs(parameters.unlock_thresholds[node.index] - source.unlock_thresholds[node.index]),
        )
        total += sum(deltas)
        changed += any(deltas)
    return total, changed


def _violation_messages(
    metrics: OptimizationMetrics,
    legal: bool,
    strategy_legal: bool,
    config: OptimizationConfig,
) -> Tuple[str, ...]:
    messages = []
    if not legal:
        messages.append(f"存在解闭锁阈值差小于 {config.minimum_gap_db} dB 的节点")
    if not strategy_legal:
        messages.append("附加策略参数与当前阈值不兼容")
    if metrics.near_unlock_violations:
        messages.append(
            f"{metrics.near_unlock_violations} 组新增小于 {config.minimum_new_unlock_distance_m:g}m 的解锁"
        )
    if metrics.ordering_violations:
        messages.append(f"{metrics.ordering_violations} 组闭锁距离不大于解锁距离")
    if metrics.lock_poor:
        messages.append(f"闭锁存在 {metrics.lock_poor} 组差或未触发")
    if metrics.unlock_poor:
        messages.append(f"解锁存在 {metrics.unlock_poor} 组差或未触发")
    if metrics.lock_excellent_rate_percent + 1e-9 < config.minimum_excellent_rate_percent:
        messages.append(
            f"闭锁优秀率 {metrics.lock_excellent_rate_percent:.1f}% 未达到 {config.minimum_excellent_rate_percent:g}%"
        )
    if metrics.unlock_excellent_rate_percent + 1e-9 < config.minimum_excellent_rate_percent:
        messages.append(
            f"解锁优秀率 {metrics.unlock_excellent_rate_percent:.1f}% 未达到 {config.minimum_excellent_rate_percent:g}%"
        )
    return tuple(messages)


def _strategy_configuration_legal(parameters: CloudParameters) -> bool:
    config = parameters.bevel_angle
    if not config:
        return True
    fields = {
        "offsetRFR": Node.RIGHT,
        "offsetRFF": Node.FRONT,
        "offsetLFL": Node.LEFT,
        "offsetLFF": Node.FRONT,
        "offsetLBL": Node.LEFT,
        "offsetLBB": Node.REAR,
        "offsetRBR": Node.RIGHT,
        "offsetRBB": Node.REAR,
    }
    for field, node in fields.items():
        offset = int(config.get(field, 0))
        if offset <= 0:
            continue
        lock = parameters.lock_thresholds[node.index]
        unlock = parameters.unlock_thresholds[node.index]
        if lock == 0 or unlock == 0 or offset + lock >= unlock:
            return False
    return True


def replace_threshold_pair(
    parameters: CloudParameters,
    node: Node,
    lock: int,
    unlock: int,
) -> CloudParameters:
    locks = list(parameters.lock_thresholds)
    unlocks = list(parameters.unlock_thresholds)
    locks[node.index] = lock
    unlocks[node.index] = unlock
    return replace(
        parameters,
        lock_thresholds=tuple(locks),
        unlock_thresholds=tuple(unlocks),
    )
