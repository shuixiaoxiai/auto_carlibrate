"""Deterministic constrained block-coordinate threshold search."""

from __future__ import annotations

import itertools
import time
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

from ..analysis import DirectionDataset
from ..cloud.models import CloudParameters
from ..domain.enums import Direction, NODE_ORDER, Node, StrategyKind
from .constraints import (
    DIRECTION_ACTIVE_NODES,
    affected_directions,
    eligible_datasets,
    legalize_threshold_gaps,
    optimization_readiness,
    threshold_bounds,
    thresholds_are_legal,
)
from .evaluator import CandidateEvaluator, parameters_key, replace_threshold_pair
from .models import (
    CandidateEvaluation,
    OptimizationConfig,
    OptimizationProgress,
    OptimizationResult,
)
from .strategies import disable_optional_strategies, strategy_presets


ProgressCallback = Callable[[OptimizationProgress], None]
CancelCheck = Callable[[], bool]


class OptimizationCancelled(RuntimeError):
    pass


class AutomaticThresholdOptimizer:
    """Search legal integer thresholds without introducing runtime dependencies."""

    def __init__(self, config: Optional[OptimizationConfig] = None) -> None:
        self.config = config or OptimizationConfig()
        self._evaluated_keys: Set[Tuple[object, ...]] = set()
        self._progress: Optional[ProgressCallback] = None
        self._cancel: Optional[CancelCheck] = None
        self._bounds: Mapping[Node, Tuple[Tuple[int, int], Tuple[int, int]]] = {}
        self._search_evaluation_limit = 1

    def optimize(
        self,
        source_parameters: CloudParameters,
        datasets: Sequence[DirectionDataset],
        *,
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[CancelCheck] = None,
    ) -> OptimizationResult:
        started = time.perf_counter()
        self._progress = progress
        self._cancel = cancel
        self._evaluated_keys = set()
        self._search_evaluation_limit = max(
            1,
            self.config.maximum_evaluations - len(NODE_ORDER) * 4,
        )
        readiness = optimization_readiness(source_parameters, datasets, self.config)
        if not readiness.can_start:
            raise ValueError("；".join(readiness.errors))
        eligible = eligible_datasets(datasets)
        self._bounds = threshold_bounds(source_parameters, self.config)
        evaluator = CandidateEvaluator(source_parameters, eligible, self.config)

        baseline = self._evaluate(evaluator, source_parameters, phase="precheck")
        evaluator.set_baseline(baseline)
        base_parameters = legalize_threshold_gaps(
            disable_optional_strategies(source_parameters),
            self.config,
        )
        base = self._evaluate(evaluator, base_parameters, phase="base")
        best_base = self._search_thresholds(evaluator, base, phase="base")
        best = best_base
        stop_reason = (
            "基础阈值满足全部要求"
            if best_base.feasible
            else "基础阈值达到搜索平台，未满足全部要求"
        )

        if not best_base.feasible and self.config.allow_strategy_fallback:
            best, strategy_reason = self._search_strategies(
                evaluator,
                best_base,
                source_parameters,
            )
            stop_reason = strategy_reason

        best = evaluator.evaluate(best.parameters)
        robustness_passed, robustness_total = self._robustness(evaluator, best)
        if self._search_budget_reached() and not best.feasible:
            stop_reason = "在当前范围和候选评估上限内未找到满足全部要求的方案"
        return OptimizationResult(
            baseline=baseline,
            recommendation=best,
            readiness=readiness,
            evaluated_candidates=len(self._evaluated_keys),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            stop_reason=stop_reason,
            robustness_passed=robustness_passed,
            robustness_total=robustness_total,
        )

    def _search_thresholds(
        self,
        evaluator: CandidateEvaluator,
        initial: CandidateEvaluation,
        *,
        phase: str,
        maximum_sweeps: Optional[int] = None,
    ) -> CandidateEvaluation:
        incumbent = initial
        sweeps = maximum_sweeps or self.config.maximum_sweeps
        for sweep in range(1, sweeps + 1):
            self._check_cancelled()
            improved = False
            direction_order = self._direction_order(incumbent)
            visited_slaves = set()
            for direction in direction_order:
                for node in DIRECTION_ACTIVE_NODES[direction]:
                    if node is Node.MASTER or node in visited_slaves:
                        continue
                    visited_slaves.add(node)
                    candidate = self._scan_node(
                        evaluator,
                        incumbent,
                        node,
                        phase,
                        f"第 {sweep} 轮：优化{direction.label}相关{node.label}节点",
                    )
                    accepted = self._accept_full(evaluator, incumbent, candidate)
                    if accepted.score > incumbent.score:
                        incumbent = accepted
                        improved = True
                    if self._search_budget_reached():
                        return incumbent

            candidate = self._scan_node(
                evaluator,
                incumbent,
                Node.MASTER,
                phase,
                f"第 {sweep} 轮：全方向优化主节点",
            )
            accepted = self._accept_full(evaluator, incumbent, candidate)
            if accepted.score > incumbent.score:
                incumbent = accepted
                improved = True
            if self._search_budget_reached():
                return incumbent

            joint = self._joint_validation(
                evaluator,
                incumbent,
                direction_order,
                phase,
            )
            accepted = self._accept_full(evaluator, incumbent, joint)
            if accepted.score > incumbent.score:
                incumbent = accepted
                improved = True
            if not improved:
                break
        return incumbent

    def _scan_node(
        self,
        evaluator: CandidateEvaluator,
        incumbent: CandidateEvaluation,
        node: Node,
        phase: str,
        message: str,
    ) -> CandidateEvaluation:
        best = incumbent
        affected = affected_directions((node,))
        candidates = self._node_candidates(incumbent.parameters, node)
        for parameters in candidates:
            if self._search_budget_reached():
                break
            evaluation = self._evaluate(
                evaluator,
                parameters,
                phase=phase,
                message=message,
                incumbent=incumbent,
                affected=affected,
            )
            if self._candidate_key(evaluation) > self._candidate_key(best):
                best = evaluation
        return best

    def _node_candidates(
        self,
        parameters: CloudParameters,
        node: Node,
    ) -> Tuple[CloudParameters, ...]:
        lock = parameters.lock_thresholds[node.index]
        unlock = parameters.unlock_thresholds[node.index]
        if lock == 0 and unlock == 0:
            return ()
        (lock_min, lock_max), (unlock_min, unlock_max) = self._bounds[node]
        pairs = set()
        step = self.config.threshold_step_db
        for candidate_lock in range(lock_min, lock_max + 1, step):
            pairs.add((candidate_lock, unlock))
        for candidate_unlock in range(unlock_min, unlock_max + 1, step):
            pairs.add((lock, candidate_unlock))
        for delta in range(-self.config.threshold_radius_db, self.config.threshold_radius_db + 1, step):
            pairs.add((lock + delta, unlock + delta))
            pairs.add((lock - abs(delta), unlock + abs(delta)))
            pairs.add((lock + abs(delta), unlock - abs(delta)))
        output = []
        for candidate_lock, candidate_unlock in sorted(pairs):
            if (candidate_lock, candidate_unlock) == (lock, unlock):
                continue
            if not lock_min <= candidate_lock <= lock_max:
                continue
            if not unlock_min <= candidate_unlock <= unlock_max:
                continue
            candidate = replace_threshold_pair(
                parameters,
                node,
                candidate_lock,
                candidate_unlock,
            )
            if thresholds_are_legal(candidate, self.config):
                output.append(candidate)
        return tuple(output)

    def _joint_validation(
        self,
        evaluator: CandidateEvaluator,
        incumbent: CandidateEvaluation,
        directions: Sequence[Direction],
        phase: str,
    ) -> CandidateEvaluation:
        best = incumbent
        moves = (
            (-1, 0), (1, 0), (0, -1), (0, 1),
            (-1, -1), (1, 1), (-1, 1), (1, -1),
        )
        for direction in directions:
            nodes = DIRECTION_ACTIVE_NODES[direction]
            for node_pair in itertools.combinations(nodes, 2):
                for first_move, second_move in itertools.product(moves, repeat=2):
                    if self._search_budget_reached():
                        return best
                    parameters = incumbent.parameters
                    valid = True
                    for node, (lock_delta, unlock_delta) in zip(
                        node_pair,
                        (first_move, second_move),
                    ):
                        lock = parameters.lock_thresholds[node.index]
                        unlock = parameters.unlock_thresholds[node.index]
                        if lock == 0 and unlock == 0:
                            valid = False
                            break
                        parameters = replace_threshold_pair(
                            parameters,
                            node,
                            lock + lock_delta,
                            unlock + unlock_delta,
                        )
                    if not valid or not self._inside_bounds(parameters, node_pair):
                        continue
                    if not thresholds_are_legal(parameters, self.config):
                        continue
                    evaluation = self._evaluate(
                        evaluator,
                        parameters,
                        phase=phase,
                        message=f"联合校验{direction.label}相关节点",
                        incumbent=incumbent,
                        affected=affected_directions(node_pair),
                    )
                    if self._candidate_key(evaluation) > self._candidate_key(best):
                        best = evaluation
        return best

    def _search_strategies(
        self,
        evaluator: CandidateEvaluator,
        base: CandidateEvaluation,
        source: CloudParameters,
    ) -> Tuple[CandidateEvaluation, str]:
        self._emit("strategy", "基础阈值已到平台，开始逐个尝试单一策略", base)
        family_best = []
        for kind, presets in strategy_presets(base.parameters, source).items():
            best = None
            for parameters in presets:
                if self._search_budget_reached():
                    break
                evaluation = self._evaluate(
                    evaluator,
                    parameters,
                    phase="strategy",
                    message=f"尝试{_strategy_label(kind)}",
                )
                if best is None or self._candidate_key(evaluation) > self._candidate_key(best):
                    best = evaluation
            if best is not None:
                family_best.append((kind, best))
        family_best.sort(
            key=lambda item: self._candidate_key(item[1]),
            reverse=True,
        )
        best_overall = base
        for kind, preset in family_best:
            if self._search_budget_reached():
                break
            refined = self._search_thresholds(
                evaluator,
                preset,
                phase="strategy",
                maximum_sweeps=min(2, self.config.maximum_sweeps),
            )
            if self._candidate_key(refined) > self._candidate_key(best_overall):
                best_overall = refined
            if refined.feasible:
                return refined, f"基础阈值不足，启用单一{_strategy_label(kind)}后满足全部要求"
        if best_overall.feasible:
            return best_overall, "单一策略方案满足全部要求"
        return best_overall, "基础阈值及单一策略均达到搜索平台，未满足全部要求"

    def _robustness(
        self,
        evaluator: CandidateEvaluator,
        best: CandidateEvaluation,
    ) -> Tuple[int, int]:
        passed = total = 0
        delta = self.config.robustness_delta_db
        for node in NODE_ORDER:
            if best.parameters.lock_thresholds[node.index] == 0:
                continue
            for event_is_unlock in (False, True):
                for sign in (-1, 1):
                    if self._budget_reached():
                        return passed, total
                    lock = best.parameters.lock_thresholds[node.index]
                    unlock = best.parameters.unlock_thresholds[node.index]
                    if event_is_unlock:
                        unlock += sign * delta
                    else:
                        lock += sign * delta
                    candidate = replace_threshold_pair(best.parameters, node, lock, unlock)
                    if not self._inside_bounds(candidate, (node,)):
                        continue
                    if not thresholds_are_legal(candidate, self.config):
                        continue
                    evaluation = self._evaluate(
                        evaluator,
                        candidate,
                        phase="verify",
                        message="执行 ±1 dB 稳定性检查",
                    )
                    total += 1
                    passed += evaluation.feasible
        return passed, total

    def _evaluate(
        self,
        evaluator: CandidateEvaluator,
        parameters: CloudParameters,
        *,
        phase: str,
        message: str = "检查当前参数与数据",
        incumbent: Optional[CandidateEvaluation] = None,
        affected: Optional[Iterable[Direction]] = None,
    ) -> CandidateEvaluation:
        self._check_cancelled()
        key = parameters_key(parameters)
        evaluation = evaluator.evaluate(
            parameters,
            incumbent=incumbent,
            affected=affected,
        )
        if key not in self._evaluated_keys:
            self._evaluated_keys.add(key)
            self._emit(phase, message, evaluation)
        return evaluation

    def _accept_full(
        self,
        evaluator: CandidateEvaluator,
        incumbent: CandidateEvaluation,
        candidate: CandidateEvaluation,
    ) -> CandidateEvaluation:
        if self._candidate_key(candidate) <= self._candidate_key(incumbent):
            return incumbent
        full = evaluator.evaluate(candidate.parameters)
        return full if self._candidate_key(full) > self._candidate_key(incumbent) else incumbent

    def _inside_bounds(
        self,
        parameters: CloudParameters,
        nodes: Iterable[Node],
    ) -> bool:
        for node in nodes:
            (lock_min, lock_max), (unlock_min, unlock_max) = self._bounds[node]
            if not lock_min <= parameters.lock_thresholds[node.index] <= lock_max:
                return False
            if not unlock_min <= parameters.unlock_thresholds[node.index] <= unlock_max:
                return False
        return True

    @staticmethod
    def _direction_order(evaluation: CandidateEvaluation) -> Tuple[Direction, ...]:
        penalties: Dict[Direction, float] = {direction: 0.0 for direction in Direction}
        for sample in evaluation.samples:
            penalty = 0.0
            if sample.lock_grade.value == "poor":
                penalty += 100.0
            elif sample.lock_grade.value == "good":
                penalty += 10.0
            if sample.unlock_grade.value == "poor":
                penalty += 100.0
            elif sample.unlock_grade.value == "good":
                penalty += 10.0
            penalties[sample.direction] += penalty
        return tuple(
            sorted(Direction, key=lambda direction: (-penalties[direction], direction.index))
        )

    @staticmethod
    def _candidate_key(evaluation: CandidateEvaluation) -> Tuple[object, ...]:
        flat = (
            evaluation.parameters.lock_thresholds
            + evaluation.parameters.unlock_thresholds
        )
        return evaluation.score + tuple(float(value) for value in flat)

    def _budget_reached(self) -> bool:
        return len(self._evaluated_keys) >= self.config.maximum_evaluations

    def _search_budget_reached(self) -> bool:
        return len(self._evaluated_keys) >= self._search_evaluation_limit

    def _check_cancelled(self) -> None:
        if self._cancel is not None and self._cancel():
            raise OptimizationCancelled("automatic optimization cancelled")

    def _emit(
        self,
        phase: str,
        message: str,
        best: CandidateEvaluation,
    ) -> None:
        if self._progress is None:
            return
        self._progress(
            OptimizationProgress(
                phase=phase,
                message=message,
                evaluated_candidates=len(self._evaluated_keys),
                maximum_evaluations=self.config.maximum_evaluations,
                best_lock_rate_percent=best.metrics.lock_excellent_rate_percent,
                best_unlock_rate_percent=best.metrics.unlock_excellent_rate_percent,
            )
        )


def _strategy_label(kind: StrategyKind) -> str:
    return {
        StrategyKind.MASTER_UNLOCK: "主节点单独解锁策略",
        StrategyKind.QUICK_LOCK: "快速闭锁策略",
        StrategyKind.QUICK_UNLOCK: "快速解锁策略",
        StrategyKind.MASTER_THAN_SLAVE: "主节点强于从节点策略",
        StrategyKind.BEVEL_ANGLE: "斜角策略",
    }[kind]
