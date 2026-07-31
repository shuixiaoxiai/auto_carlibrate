"""Distance projection and frozen excellent/good/poor boundaries."""

from __future__ import annotations

import math

from ..domain.enums import DistanceGrade, EventType


def project_action_distance(
    event_type: EventType,
    actual_distance_m: float,
    actual_action_time: float,
    calculated_action_time: float,
    walking_speed_mps: float,
) -> float:
    for name, value in (
        ("actual_distance_m", actual_distance_m),
        ("actual_action_time", actual_action_time),
        ("calculated_action_time", calculated_action_time),
        ("walking_speed_mps", walking_speed_mps),
    ):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if actual_distance_m < 0 or walking_speed_mps <= 0:
        raise ValueError("distance must be non-negative and walking speed positive")
    delta_s = calculated_action_time - actual_action_time
    if event_type is EventType.LOCK:
        distance = actual_distance_m + walking_speed_mps * delta_s
    else:
        distance = actual_distance_m - walking_speed_mps * delta_s
    return max(0.0, distance)


def lock_grade(distance_m: float) -> DistanceGrade:
    if 8.0 <= distance_m <= 12.0:
        return DistanceGrade.EXCELLENT
    if 5.0 <= distance_m < 8.0 or 12.0 < distance_m <= 16.0:
        return DistanceGrade.GOOD
    return DistanceGrade.POOR


def unlock_grade(distance_m: float) -> DistanceGrade:
    if 2.0 <= distance_m <= 5.0:
        return DistanceGrade.EXCELLENT
    if 0.5 <= distance_m < 2.0 or 5.0 < distance_m <= 8.0:
        return DistanceGrade.GOOD
    return DistanceGrade.POOR


def distance_grade(event_type: EventType, distance_m: float) -> DistanceGrade:
    if not math.isfinite(distance_m) or distance_m < 0:
        raise ValueError("distance_m must be a finite non-negative number")
    return lock_grade(distance_m) if event_type is EventType.LOCK else unlock_grade(distance_m)
