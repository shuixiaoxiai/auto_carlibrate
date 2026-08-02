"""Registered one-at-a-time fallback strategy presets."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, Mapping, Optional, Tuple

from ..cloud.models import CloudParameters
from ..domain.enums import Node, StrategyKind


QUICK_LOCK_FIELDS = (
    "weakFront", "weakRear", "weakFl", "weakFr", "strongMst",
    "strongFront", "strongRear", "strongFl", "strongFr", "reserve",
)
QUICK_UNLOCK_FIELDS = (
    "unlockTime", "frontToFr", "frontToFl", "rearToFl", "rearToFr", "reserve",
)
MST_THAN_SLAVE_FIELDS = ("diff", "reserve")
BEVEL_FIELDS = (
    "offsetRFR", "offsetRFF", "offsetLFL", "offsetLFF",
    "offsetLBL", "offsetLBB", "offsetRBR", "offsetRBB",
)


def disable_optional_strategies(parameters: CloudParameters) -> CloudParameters:
    return replace(
        parameters,
        mst_unlock=(
            None if parameters.mst_unlock is None else (0, 0, 0, 0, 0)
        ),
        quick_lock=_zero_existing(parameters.quick_lock, QUICK_LOCK_FIELDS),
        quick_unlock=_zero_existing(parameters.quick_unlock, QUICK_UNLOCK_FIELDS),
        mst_than_slave=_zero_existing(
            parameters.mst_than_slave,
            MST_THAN_SLAVE_FIELDS,
        ),
        bevel_angle=_zero_existing(parameters.bevel_angle, BEVEL_FIELDS),
    )


def strategy_presets(
    base: CloudParameters,
    source: CloudParameters,
) -> Mapping[StrategyKind, Tuple[CloudParameters, ...]]:
    """Build deterministic, bounded presets with exactly one strategy enabled."""
    disabled = disable_optional_strategies(base)
    output: Dict[StrategyKind, list[CloudParameters]] = {
        kind: []
        for kind in (
            StrategyKind.MASTER_UNLOCK,
            StrategyKind.QUICK_LOCK,
            StrategyKind.QUICK_UNLOCK,
            StrategyKind.MASTER_THAN_SLAVE,
            StrategyKind.BEVEL_ANGLE,
        )
    }

    if source.mst_unlock and any(source.mst_unlock):
        output[StrategyKind.MASTER_UNLOCK].append(
            replace(disabled, mst_unlock=tuple(source.mst_unlock))
        )
    for delta in (1, 2, 3, 5):
        values = tuple(
            max(-128, threshold - delta)
            for threshold in disabled.unlock_thresholds
        )
        output[StrategyKind.MASTER_UNLOCK].append(
            replace(disabled, mst_unlock=values)
        )

    if source.quick_lock and any(int(value) for value in source.quick_lock.values()):
        output[StrategyKind.QUICK_LOCK].append(
            replace(disabled, quick_lock=dict(source.quick_lock))
        )
    weak_fields = ("weakFront", "weakRear", "weakFl", "weakFr")
    for weak_field in weak_fields:
        for weak in (1, 3, 5):
            values = {field: 0 for field in QUICK_LOCK_FIELDS}
            values[weak_field] = weak
            for strong_field in (
                "strongMst", "strongFront", "strongRear", "strongFl", "strongFr"
            ):
                values[strong_field] = 1
            output[StrategyKind.QUICK_LOCK].append(
                replace(disabled, quick_lock=values)
            )

    if source.quick_unlock and int(source.quick_unlock.get("unlockTime", 0)) > 0:
        output[StrategyKind.QUICK_UNLOCK].append(
            replace(disabled, quick_unlock=dict(source.quick_unlock))
        )
    transition_fields = ("frontToFr", "frontToFl", "rearToFl", "rearToFr")
    for field in transition_fields:
        for offset in (1, 3, 5):
            values = {name: 0 for name in QUICK_UNLOCK_FIELDS}
            values["unlockTime"] = 3
            values[field] = offset
            output[StrategyKind.QUICK_UNLOCK].append(
                replace(disabled, quick_unlock=values)
            )

    if source.mst_than_slave and int(source.mst_than_slave.get("diff", 0)) > 0:
        output[StrategyKind.MASTER_THAN_SLAVE].append(
            replace(disabled, mst_than_slave=dict(source.mst_than_slave))
        )
    for diff in (1, 2, 3, 4, 5):
        output[StrategyKind.MASTER_THAN_SLAVE].append(
            replace(disabled, mst_than_slave={"diff": diff, "reserve": 0})
        )

    if source.bevel_angle and any(int(value) for value in source.bevel_angle.values()):
        output[StrategyKind.BEVEL_ANGLE].append(
            replace(disabled, bevel_angle=dict(source.bevel_angle))
        )
    bevel_pairs = (
        ("offsetRFR", "offsetRFF", Node.RIGHT, Node.FRONT),
        ("offsetLFL", "offsetLFF", Node.LEFT, Node.FRONT),
        ("offsetLBL", "offsetLBB", Node.LEFT, Node.REAR),
        ("offsetRBR", "offsetRBB", Node.RIGHT, Node.REAR),
    )
    for first_field, second_field, first_node, second_node in bevel_pairs:
        max_offset = min(
            5,
            disabled.unlock_thresholds[first_node.index]
            - disabled.lock_thresholds[first_node.index]
            - 1,
            disabled.unlock_thresholds[second_node.index]
            - disabled.lock_thresholds[second_node.index]
            - 1,
        )
        for offset in range(1, max(0, max_offset) + 1, 2):
            values = {name: 0 for name in BEVEL_FIELDS}
            values[first_field] = offset
            values[second_field] = offset
            output[StrategyKind.BEVEL_ANGLE].append(
                replace(disabled, bevel_angle=values)
            )

    return {
        kind: _unique(candidates)
        for kind, candidates in output.items()
        if candidates
    }


def strategy_updates(parameters: CloudParameters) -> Mapping[str, Mapping[str, int]]:
    output = {}
    if parameters.quick_lock is not None:
        output["quickLock"] = dict(parameters.quick_lock)
    if parameters.quick_unlock is not None:
        output["quickUnlock"] = dict(parameters.quick_unlock)
    if parameters.mst_than_slave is not None:
        output["mstThanSlave"] = dict(parameters.mst_than_slave)
    if parameters.bevel_angle is not None:
        output["bevelAngle"] = dict(parameters.bevel_angle)
    return output


def _zero_existing(
    values: Optional[Mapping[str, int]],
    fields: Iterable[str],
) -> Optional[Mapping[str, int]]:
    return None if values is None else {field: 0 for field in fields}


def _unique(candidates):
    seen = set()
    output = []
    for parameters in candidates:
        key = repr(parameters)
        if key in seen:
            continue
        seen.add(key)
        output.append(parameters)
    return tuple(output)
