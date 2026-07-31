"""Storage-facing project value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..domain.models import CalibrationProject


@dataclass(frozen=True)
class StoredProject:
    project: CalibrationProject
    current_cloud_hex: Optional[str] = None
    capture_path: Optional[str] = None
    capture_format: Optional[str] = None
    vehicle_name: Optional[str] = None
    vehicle_vin: Optional[str] = None


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    name: str
    updated_at: str
    direction_count: int
    capture_path: Optional[str]


@dataclass(frozen=True)
class ParameterHistoryEntry:
    history_id: int
    project_id: str
    changed_at: str
    cloud_hex: str
    note: Optional[str]


@dataclass(frozen=True)
class AnalysisSnapshot:
    analysis_id: int
    project_id: str
    created_at: str
    cloud_hex: Optional[str]
    analysis_version: str
    payload: Mapping[str, Any]
