"""Deterministic, UI-independent BLE lock/unlock What-if engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..cloud.models import CloudParameters
from ..domain.enums import (
    ActionOrigin,
    Direction,
    EventType,
    NODE_ORDER,
    Node,
    StrategyKind,
)
from ..domain.models import (
    ActionPoint,
    ConditionPoint,
    DirectionAnalysisResult,
    RssiSample,
    StrategyEventResult,
)


@dataclass(frozen=True)
class Candidate:
    key: str
    strategy: StrategyKind
    trigger_node: Node


Evaluator = Callable[[RssiSample], Sequence[Candidate]]


class StrategyEngine:
    def __init__(
        self,
        parameters: CloudParameters,
        lock_stable_s: float = 2.0,
        unlock_stable_s: float = 0.5,
    ) -> None:
        if lock_stable_s < 0 or unlock_stable_s < 0:
            raise ValueError("stable durations cannot be negative")
        self.parameters = parameters
        self.lock_stable_s = lock_stable_s
        self.unlock_stable_s = unlock_stable_s

    def analyze(
        self,
        direction: Direction,
        samples: Sequence[RssiSample],
    ) -> DirectionAnalysisResult:
        ordered = tuple(sorted(samples, key=lambda sample: sample.source_timestamp))
        lock = self._find_stable(
            ordered,
            self._lock_candidates,
            self.lock_stable_s,
            EventType.LOCK,
        )
        unlock = None
        if lock is not None:
            unlock_evaluator = self._build_unlock_evaluator()
            unlock = self._find_stable(
                (
                    sample
                    for sample in ordered
                    if sample.source_timestamp >= lock.action.timestamp - 1e-9
                ),
                unlock_evaluator,
                self.unlock_stable_s,
                EventType.UNLOCK,
            )
        return DirectionAnalysisResult(direction=direction, lock=lock, unlock=unlock)

    def _find_stable(
        self,
        samples: Iterable[RssiSample],
        evaluator: Evaluator,
        duration_s: float,
        event_type: EventType,
    ) -> Optional[StrategyEventResult]:
        states: Dict[str, Tuple[Candidate, RssiSample]] = {}
        for sample in samples:
            candidates = tuple(evaluator(sample))
            active_keys = {candidate.key for candidate in candidates}
            for key in tuple(states):
                if key not in active_keys:
                    states.pop(key)
            for candidate in candidates:
                if candidate.key not in states:
                    states[candidate.key] = (candidate, sample)
                stored_candidate, condition_sample = states[candidate.key]
                elapsed = sample.source_timestamp - condition_sample.source_timestamp
                if elapsed + 1e-9 < duration_s:
                    continue
                condition = ConditionPoint(
                    event_type=event_type,
                    timestamp=condition_sample.source_timestamp,
                    trigger_node=stored_candidate.trigger_node,
                    strategy=stored_candidate.strategy,
                    rssi=condition_sample.values,
                )
                action = ActionPoint(
                    event_type=event_type,
                    timestamp=condition_sample.source_timestamp + duration_s,
                    origin=ActionOrigin.CALCULATED,
                )
                return StrategyEventResult(condition=condition, action=action)
        return None

    def _enabled_nodes(self, thresholds: Sequence[int]) -> Tuple[Node, ...]:
        return tuple(
            node for node in NODE_ORDER if thresholds[node.index] != 0
        )

    @staticmethod
    def _all_valid(sample: RssiSample, nodes: Sequence[Node]) -> bool:
        return bool(nodes) and all(sample.is_valid(node) for node in nodes)

    def _base_lock_node(self, sample: RssiSample) -> Optional[Node]:
        thresholds = self.parameters.lock_thresholds
        nodes = self._enabled_nodes(thresholds)
        if not self._all_valid(sample, nodes):
            return None
        if not all(
            sample.value(node) <= thresholds[node.index]  # type: ignore[operator]
            for node in nodes
        ):
            return None
        return max(
            nodes,
            key=lambda node: sample.value(node) - thresholds[node.index],  # type: ignore[operator]
        )

    def _lock_candidates(self, sample: RssiSample) -> Sequence[Candidate]:
        base_node = self._base_lock_node(sample)
        if base_node is not None:
            return (Candidate("lock-zone", StrategyKind.BASE, base_node),)
        quick_node = self._quick_lock_node(sample)
        if quick_node is not None:
            return (Candidate("lock-zone", StrategyKind.QUICK_LOCK, quick_node),)
        return ()

    def _quick_lock_node(self, sample: RssiSample) -> Optional[Node]:
        config = self.parameters.quick_lock
        if not config:
            return None
        lock_thresholds = self.parameters.lock_thresholds
        unlock_thresholds = self.parameters.unlock_thresholds
        nodes = self._enabled_nodes(lock_thresholds)
        if not self._all_valid(sample, nodes):
            return None
        unlock_nodes = self._enabled_nodes(unlock_thresholds)
        if any(
            sample.is_valid(node)
            and sample.value(node) >= unlock_thresholds[node.index]  # type: ignore[operator]
            for node in unlock_nodes
        ):
            return None

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
        candidates = []
        for node, weak_field in weak_fields.items():
            if node not in nodes or not sample.is_valid(node):
                continue
            weak_offset = int(config.get(weak_field, 0))
            value = sample.value(node)
            threshold = lock_thresholds[node.index]
            if (
                weak_offset <= 0
                or value is None
                or value <= threshold
                or value > threshold + weak_offset
            ):
                continue
            strong_ok = True
            strong_check_count = 0
            for other in nodes:
                if other is node:
                    continue
                strong_offset = int(config.get(strong_fields[other], 0))
                if strong_offset == 0:
                    continue
                strong_check_count += 1
                other_value = sample.value(other)
                if (
                    other_value is None
                    or other_value > lock_thresholds[other.index] - strong_offset
                ):
                    strong_ok = False
                    break
            if strong_ok and strong_check_count > 0:
                candidates.append((threshold + weak_offset - value, node))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def _build_unlock_evaluator(self) -> Evaluator:
        quick_state = _QuickUnlockState(self.parameters)

        def evaluate(sample: RssiSample) -> Sequence[Candidate]:
            candidates: List[Candidate] = []
            candidates.extend(self._base_unlock_candidates(sample))
            master = self._master_unlock_candidate(sample)
            if master is not None:
                candidates.append(master)
            quick = quick_state.evaluate(sample)
            if quick is not None:
                candidates.append(quick)
            master_than_slave = self._master_than_slave_candidate(sample)
            if master_than_slave is not None:
                candidates.append(master_than_slave)
            candidates.extend(self._bevel_candidates(sample))
            return candidates

        return evaluate

    def _base_unlock_candidates(self, sample: RssiSample) -> Sequence[Candidate]:
        thresholds = self.parameters.unlock_thresholds
        candidates = []
        for node in self._enabled_nodes(thresholds):
            value = sample.value(node)
            if (
                sample.is_valid(node)
                and value is not None
                and value >= thresholds[node.index]
            ):
                candidates.append(
                    Candidate(
                        f"base-unlock:{node.value}",
                        StrategyKind.BASE,
                        node,
                    )
                )
        return candidates

    def _is_lock_zone(self, sample: RssiSample) -> bool:
        return self._base_lock_node(sample) is not None

    def _master_unlock_candidate(self, sample: RssiSample) -> Optional[Candidate]:
        thresholds = self.parameters.mst_unlock
        if thresholds is None or self._is_lock_zone(sample):
            return None
        valid_nodes = [node for node in NODE_ORDER if sample.is_valid(node)]
        if Node.MASTER not in valid_nodes:
            return None
        strongest = max(valid_nodes, key=lambda node: sample.value(node))  # type: ignore[arg-type]
        threshold = thresholds[strongest.index]
        master_value = sample.value(Node.MASTER)
        if threshold == 0 or master_value is None or master_value < threshold:
            return None
        return Candidate(
            f"master-unlock:{strongest.value}",
            StrategyKind.MASTER_UNLOCK,
            Node.MASTER,
        )

    def _master_than_slave_candidate(
        self,
        sample: RssiSample,
    ) -> Optional[Candidate]:
        config = self.parameters.mst_than_slave
        diff_units = 0 if not config else int(config.get("diff", 0))
        if diff_units <= 0 or not self._all_valid(sample, NODE_ORDER):
            return None
        master = sample.value(Node.MASTER)
        strongest_slave = max(
            sample.value(node) for node in NODE_ORDER if node is not Node.MASTER
        )
        if master is None or strongest_slave is None:
            return None
        if master < strongest_slave + diff_units * 2:
            return None
        return Candidate(
            "master-than-slave",
            StrategyKind.MASTER_THAN_SLAVE,
            Node.MASTER,
        )

    def _bevel_candidates(self, sample: RssiSample) -> Sequence[Candidate]:
        config = self.parameters.bevel_angle
        if not config:
            return ()
        field_nodes = {
            "offsetRFR": Node.RIGHT,
            "offsetRFF": Node.FRONT,
            "offsetLFL": Node.LEFT,
            "offsetLFF": Node.FRONT,
            "offsetLBL": Node.LEFT,
            "offsetLBB": Node.REAR,
            "offsetRBR": Node.RIGHT,
            "offsetRBB": Node.REAR,
        }
        if any(
            int(config.get(field, 0)) > 0
            and not self._bevel_offset_valid(node, int(config.get(field, 0)))
            for field, node in field_nodes.items()
        ):
            return ()
        pairs = (
            (
                "right-front",
                Node.RIGHT,
                "offsetRFR",
                Node.FRONT,
                "offsetRFF",
            ),
            (
                "left-front",
                Node.LEFT,
                "offsetLFL",
                Node.FRONT,
                "offsetLFF",
            ),
            (
                "left-rear",
                Node.LEFT,
                "offsetLBL",
                Node.REAR,
                "offsetLBB",
            ),
            (
                "right-rear",
                Node.RIGHT,
                "offsetRBR",
                Node.REAR,
                "offsetRBB",
            ),
        )
        results = []
        for key, first, first_field, second, second_field in pairs:
            first_offset = int(config.get(first_field, 0))
            second_offset = int(config.get(second_field, 0))
            if first_offset == 0 and second_offset == 0:
                continue
            if not sample.is_valid(first) or not sample.is_valid(second):
                continue
            if not self._bevel_offset_valid(first, first_offset):
                continue
            if not self._bevel_offset_valid(second, second_offset):
                continue
            first_value = sample.value(first)
            second_value = sample.value(second)
            first_margin = (
                first_value
                + first_offset
                - self.parameters.unlock_thresholds[first.index]
            )
            second_margin = (
                second_value
                + second_offset
                - self.parameters.unlock_thresholds[second.index]
            )
            if first_margin < 0 or second_margin < 0:
                continue
            trigger = first if first_margin <= second_margin else second
            results.append(
                Candidate(
                    f"bevel:{key}",
                    StrategyKind.BEVEL_ANGLE,
                    trigger,
                )
            )
        return results

    def _bevel_offset_valid(self, node: Node, offset: int) -> bool:
        lock_threshold = self.parameters.lock_thresholds[node.index]
        unlock_threshold = self.parameters.unlock_thresholds[node.index]
        return (
            lock_threshold != 0
            and unlock_threshold != 0
            and offset + lock_threshold < unlock_threshold
        )


class _QuickUnlockState:
    _PAIR_FIELDS = {
        frozenset((Node.FRONT, Node.RIGHT)): "frontToFr",
        frozenset((Node.FRONT, Node.LEFT)): "frontToFl",
        frozenset((Node.REAR, Node.LEFT)): "rearToFl",
        frozenset((Node.REAR, Node.RIGHT)): "rearToFr",
    }

    def __init__(self, parameters: CloudParameters) -> None:
        self.parameters = parameters
        self.previous_strongest: Optional[Node] = None
        self.transition: Optional[Tuple[float, Node, str]] = None

    def evaluate(self, sample: RssiSample) -> Optional[Candidate]:
        config = self.parameters.quick_unlock
        window_units = 0 if not config else int(config.get("unlockTime", 0))
        slaves = tuple(node for node in NODE_ORDER if node is not Node.MASTER)
        if window_units <= 0 or not all(sample.is_valid(node) for node in slaves):
            return None
        strongest = max(slaves, key=lambda node: sample.value(node))  # type: ignore[arg-type]
        if self.previous_strongest is not None and strongest is not self.previous_strongest:
            field = self._PAIR_FIELDS.get(
                frozenset((self.previous_strongest, strongest))
            )
            if field is not None and int(config.get(field, 0)) > 0:
                self.transition = (sample.source_timestamp, strongest, field)
        self.previous_strongest = strongest

        if self.transition is None:
            return None
        transition_time, target, field = self.transition
        if sample.source_timestamp - transition_time > window_units * 0.2 + 1e-9:
            self.transition = None
            return None
        offset = int(config.get(field, 0))
        threshold = self.parameters.unlock_thresholds[target.index]
        value = sample.value(target)
        if threshold == 0 or value is None or value < threshold - offset:
            return None
        return Candidate(
            f"quick-unlock:{field}:{target.value}",
            StrategyKind.QUICK_UNLOCK,
            target,
        )
