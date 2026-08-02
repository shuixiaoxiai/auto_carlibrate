"""Direction influence and threshold legality rules."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from ..analysis import DirectionDataset
from ..cloud.models import CloudParameters
from ..domain.enums import Direction, EventType, NODE_ORDER, Node
from .models import OptimizationConfig, OptimizationReadiness


DIRECTION_ACTIVE_NODES: Mapping[Direction, Tuple[Node, ...]] = {
    Direction.FRONT: (Node.MASTER, Node.FRONT),
    Direction.FRONT_RIGHT: (Node.MASTER, Node.FRONT, Node.RIGHT),
    Direction.RIGHT: (Node.MASTER, Node.RIGHT),
    Direction.REAR_RIGHT: (Node.MASTER, Node.RIGHT, Node.REAR),
    Direction.REAR: (Node.MASTER, Node.REAR),
    Direction.REAR_LEFT: (Node.MASTER, Node.LEFT, Node.REAR),
    Direction.LEFT: (Node.MASTER, Node.LEFT),
    Direction.FRONT_LEFT: (Node.MASTER, Node.LEFT, Node.FRONT),
}

NODE_AFFECTED_DIRECTIONS: Mapping[Node, Tuple[Direction, ...]] = {
    Node.MASTER: tuple(Direction),
    Node.FRONT: (
        Direction.FRONT,
        Direction.FRONT_RIGHT,
        Direction.FRONT_LEFT,
    ),
    Node.REAR: (
        Direction.REAR,
        Direction.REAR_RIGHT,
        Direction.REAR_LEFT,
    ),
    Node.LEFT: (
        Direction.LEFT,
        Direction.FRONT_LEFT,
        Direction.REAR_LEFT,
    ),
    Node.RIGHT: (
        Direction.RIGHT,
        Direction.FRONT_RIGHT,
        Direction.REAR_RIGHT,
    ),
}


def affected_directions(nodes: Iterable[Node]) -> Tuple[Direction, ...]:
    affected = {
        direction
        for node in nodes
        for direction in NODE_AFFECTED_DIRECTIONS[node]
    }
    return tuple(direction for direction in Direction if direction in affected)


def eligible_datasets(
    datasets: Sequence[DirectionDataset],
) -> Tuple[DirectionDataset, ...]:
    return tuple(dataset for dataset in datasets if not dataset_issue(dataset))


def dataset_issue(dataset: DirectionDataset) -> str:
    record = dataset.record
    missing = []
    if not dataset.samples:
        missing.append("RSSI")
    if record.event(EventType.LOCK) is None:
        missing.append("实车闭锁时刻")
    if record.event(EventType.UNLOCK) is None:
        missing.append("实车解锁时刻")
    if record.actual_lock_distance_m is None:
        missing.append("实测闭锁距离")
    if record.actual_unlock_distance_m is None:
        missing.append("实测解锁距离")
    return "" if not missing else "缺少" + "、".join(missing)


def optimization_readiness(
    parameters: CloudParameters,
    datasets: Sequence[DirectionDataset],
    config: OptimizationConfig,
) -> OptimizationReadiness:
    skipped = []
    eligible = []
    for dataset in datasets:
        issue = dataset_issue(dataset)
        if issue:
            skipped.append(
                f"{dataset.record.direction.label}第 {dataset.record.group_index} 组：{issue}"
            )
        else:
            eligible.append(dataset)

    errors = list(threshold_source_errors(parameters, config))
    if not eligible:
        errors.append(
            "没有完整的方向组；至少需要一组同时包含 RSSI、实车解闭锁时刻、"
            "实测解闭锁距离和有效步速的数据"
        )
    directions = {dataset.record.direction for dataset in eligible}
    warnings = []
    if eligible and len(directions) < 8:
        warnings.append(
            f"仅覆盖 {len(directions)}/8 个方向，推荐结果将标记为低置信度"
        )
    elif eligible and len(eligible) < 24:
        warnings.append(
            f"当前有 {len(eligible)}/24 个完整方向组，建议每方向采集三组后复验"
        )
    if skipped:
        warnings.append(f"将跳过 {len(skipped)} 个不完整方向组")
    return OptimizationReadiness(
        can_start=not errors,
        total_datasets=len(datasets),
        eligible_datasets=len(eligible),
        eligible_directions=len(directions),
        skipped_labels=tuple(skipped),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def threshold_source_errors(
    parameters: CloudParameters,
    config: OptimizationConfig,
) -> Tuple[str, ...]:
    errors = []
    for node in NODE_ORDER:
        lock = parameters.lock_thresholds[node.index]
        unlock = parameters.unlock_thresholds[node.index]
        if (lock == 0) != (unlock == 0):
            errors.append(f"{node.label}节点解闭锁禁用状态不一致")
            continue
        if lock == 0:
            continue
        if not config.threshold_min <= lock <= config.threshold_max:
            errors.append(f"{node.label}节点闭锁阈值超出合法范围")
        if not config.threshold_min <= unlock <= config.threshold_max:
            errors.append(f"{node.label}节点解锁阈值超出合法范围")
        lock_lower = max(
            config.threshold_min,
            lock - config.threshold_radius_db,
        )
        unlock_upper = min(
            config.threshold_max,
            unlock + config.threshold_radius_db,
        )
        if unlock_upper - lock_lower < config.minimum_gap_db:
            errors.append(
                f"{node.label}节点在 ±{config.threshold_radius_db} dB 范围内无法满足阈值间隔"
            )
    return tuple(errors)


def thresholds_are_legal(
    parameters: CloudParameters,
    config: OptimizationConfig,
) -> bool:
    for node in NODE_ORDER:
        lock = parameters.lock_thresholds[node.index]
        unlock = parameters.unlock_thresholds[node.index]
        if lock == 0 and unlock == 0:
            continue
        if lock == 0 or unlock == 0:
            return False
        if not config.threshold_min <= lock <= config.threshold_max:
            return False
        if not config.threshold_min <= unlock <= config.threshold_max:
            return False
        if unlock - lock < config.minimum_gap_db:
            return False
    return True


def threshold_bounds(
    source: CloudParameters,
    config: OptimizationConfig,
) -> Mapping[Node, Tuple[Tuple[int, int], Tuple[int, int]]]:
    output: Dict[Node, Tuple[Tuple[int, int], Tuple[int, int]]] = {}
    for node in NODE_ORDER:
        lock = source.lock_thresholds[node.index]
        unlock = source.unlock_thresholds[node.index]
        if lock == 0 and unlock == 0:
            output[node] = ((0, 0), (0, 0))
            continue
        output[node] = (
            (
                max(config.threshold_min, lock - config.threshold_radius_db),
                min(config.threshold_max, lock + config.threshold_radius_db),
            ),
            (
                max(config.threshold_min, unlock - config.threshold_radius_db),
                min(config.threshold_max, unlock + config.threshold_radius_db),
            ),
        )
    return output


def legalize_threshold_gaps(
    parameters: CloudParameters,
    config: OptimizationConfig,
) -> CloudParameters:
    """Repair source gap violations conservatively before local search."""
    locks = list(parameters.lock_thresholds)
    unlocks = list(parameters.unlock_thresholds)
    for node in NODE_ORDER:
        lock = locks[node.index]
        unlock = unlocks[node.index]
        if lock == 0 and unlock == 0:
            continue
        if unlock - lock >= config.minimum_gap_db:
            continue
        lock_lower = max(
            config.threshold_min,
            parameters.lock_thresholds[node.index] - config.threshold_radius_db,
        )
        unlock_upper = min(
            config.threshold_max,
            parameters.unlock_thresholds[node.index] + config.threshold_radius_db,
        )
        candidate_lock = max(lock_lower, unlock - config.minimum_gap_db)
        candidate_unlock = unlock
        if candidate_unlock - candidate_lock < config.minimum_gap_db:
            candidate_unlock = min(
                unlock_upper,
                candidate_lock + config.minimum_gap_db,
            )
        locks[node.index] = candidate_lock
        unlocks[node.index] = candidate_unlock
    return replace(
        parameters,
        lock_thresholds=tuple(locks),
        unlock_thresholds=tuple(unlocks),
    )
