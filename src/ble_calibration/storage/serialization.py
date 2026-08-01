"""JSON-safe serialization for derived What-if results."""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from ..analysis import GroupedRecomputeResult
from ..analysis.recompute import QualitySummary, RecomputeResult
from ..domain.models import StrategyEventResult


def _event_result_to_dict(
    result: Optional[StrategyEventResult],
) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "condition": {
            "event_type": result.condition.event_type.value,
            "timestamp": result.condition.timestamp,
            "trigger_node": result.condition.trigger_node.value,
            "strategy": result.condition.strategy.value,
            "rssi": list(result.condition.rssi),
            "label": result.condition.label,
        },
        "action": {
            "event_type": result.action.event_type.value,
            "timestamp": result.action.timestamp,
            "origin": result.action.origin.value,
        },
        "distance_m": result.distance_m,
        "grade": result.grade.value,
    }


def _summary_to_dict(summary: QualitySummary) -> Dict[str, Any]:
    return {
        "total": summary.total,
        "excellent": summary.excellent,
        "good": summary.good,
        "poor": summary.poor,
        "excellent_rate_percent": summary.excellent_rate_percent,
        "good_directions": [direction.value for direction in summary.good_directions],
        "poor_directions": [direction.value for direction in summary.poor_directions],
        "untriggered_directions": [
            direction.value for direction in summary.untriggered_directions
        ],
    }


def _single_result_to_dict(result: RecomputeResult) -> Dict[str, Any]:
    return {
        "directions": {
            direction.value: {
                "analysis_version": analysis.analysis_version,
                "lock": _event_result_to_dict(analysis.lock),
                "unlock": _event_result_to_dict(analysis.unlock),
            }
            for direction, analysis in result.directions.items()
        },
        "lock_summary": _summary_to_dict(result.lock_summary),
        "unlock_summary": _summary_to_dict(result.unlock_summary),
        "elapsed_ms": result.elapsed_ms,
    }


def recompute_result_to_dict(
    result: Union[RecomputeResult, GroupedRecomputeResult],
) -> Dict[str, Any]:
    if isinstance(result, RecomputeResult):
        return _single_result_to_dict(result)
    first_group = result.group_results.get(1)
    return {
        "directions": (
            {} if first_group is None else _single_result_to_dict(first_group)["directions"]
        ),
        "groups": {
            str(group_index): _single_result_to_dict(group_result)
            for group_index, group_result in result.group_results.items()
        },
        "means": {
            direction.value: {
                "group_count": mean.group_count,
                "lock_result_count": mean.lock_result_count,
                "unlock_result_count": mean.unlock_result_count,
                "lock": _event_result_to_dict(mean.analysis.lock),
                "unlock": _event_result_to_dict(mean.analysis.unlock),
            }
            for direction, mean in result.mean_directions.items()
        },
        "lock_summary": _summary_to_dict(result.lock_summary),
        "unlock_summary": _summary_to_dict(result.unlock_summary),
        "elapsed_ms": result.elapsed_ms,
    }
