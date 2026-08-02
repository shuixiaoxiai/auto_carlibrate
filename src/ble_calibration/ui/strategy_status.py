"""Derived, user-facing activation states for optional cloud strategies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ..cloud.models import CloudParameters
from ..domain.enums import Node


class StrategyActivation(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    INVALID = "invalid"


@dataclass(frozen=True)
class StrategyStatus:
    activation: StrategyActivation
    label: str
    detail: str


_ENABLED = StrategyStatus(StrategyActivation.ENABLED, "已开启", "当前配置可参与策略判定")
_DISABLED = StrategyStatus(StrategyActivation.DISABLED, "未开启", "当前配置不会参与策略判定")


def strategy_statuses(parameters: CloudParameters) -> Mapping[str, StrategyStatus]:
    """Return statuses using the same configuration gates as ``StrategyEngine``."""
    return {
        "mstUnlock": _master_unlock_status(parameters),
        "quickLock": _quick_lock_status(parameters),
        "quickUnlock": _quick_unlock_status(parameters),
        "mstThanSlave": _master_than_slave_status(parameters),
        "bevelAngle": _bevel_angle_status(parameters),
    }


def _master_unlock_status(parameters: CloudParameters) -> StrategyStatus:
    values = parameters.mst_unlock
    return _ENABLED if values is not None and any(value != 0 for value in values) else _DISABLED


def _quick_lock_status(parameters: CloudParameters) -> StrategyStatus:
    config = parameters.quick_lock
    if not config:
        return _DISABLED
    enabled_nodes = {
        Node.MASTER: parameters.lock_thresholds[Node.MASTER.index] != 0,
        Node.FRONT: parameters.lock_thresholds[Node.FRONT.index] != 0,
        Node.REAR: parameters.lock_thresholds[Node.REAR.index] != 0,
        Node.LEFT: parameters.lock_thresholds[Node.LEFT.index] != 0,
        Node.RIGHT: parameters.lock_thresholds[Node.RIGHT.index] != 0,
    }
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
    for weak_node, weak_field in weak_fields.items():
        if not enabled_nodes[weak_node] or int(config.get(weak_field, 0)) <= 0:
            continue
        if any(
            node is not weak_node
            and enabled_nodes[node]
            and int(config.get(strong_field, 0)) > 0
            for node, strong_field in strong_fields.items()
        ):
            return _ENABLED
    return _DISABLED


def _quick_unlock_status(parameters: CloudParameters) -> StrategyStatus:
    config = parameters.quick_unlock
    return _ENABLED if config and int(config.get("unlockTime", 0)) > 0 else _DISABLED


def _master_than_slave_status(parameters: CloudParameters) -> StrategyStatus:
    config = parameters.mst_than_slave
    return _ENABLED if config and int(config.get("diff", 0)) > 0 else _DISABLED


def _bevel_angle_status(parameters: CloudParameters) -> StrategyStatus:
    config = parameters.bevel_angle
    if not config:
        return _DISABLED
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
    enabled_offsets = [
        (field, node, int(config.get(field, 0)))
        for field, node in fields.items()
        if int(config.get(field, 0)) > 0
    ]
    if not enabled_offsets:
        return _DISABLED
    for field, node, offset in enabled_offsets:
        lock = parameters.lock_thresholds[node.index]
        unlock = parameters.unlock_thresholds[node.index]
        if lock == 0 or unlock == 0 or offset + lock >= unlock:
            return StrategyStatus(
                StrategyActivation.INVALID,
                "配置无效",
                f"{field} 不满足闭锁/解锁阈值安全前提，策略不会生效",
            )
    return _ENABLED
