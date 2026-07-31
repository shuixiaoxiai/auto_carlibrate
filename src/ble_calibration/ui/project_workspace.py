"""Project lifecycle used by the desktop UI without depending on Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from ..analysis import DirectionDataset
from ..can.recording import JsonlFrameRecorder
from ..cloud import decode_cloud
from ..domain import CalibrationProject, Direction
from ..replay import ReplayService
from ..storage import ProjectRepository, StoredProject
from .state import CalibrationUiState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProjectWorkspace:
    database_path: Path
    project_id: str
    name: str
    created_at: str
    vehicle_name: Optional[str] = None
    vehicle_vin: Optional[str] = None
    persisted: bool = False

    @classmethod
    def create(
        cls,
        database_path: Path,
        name: str,
    ) -> "ProjectWorkspace":
        project = CalibrationProject(name=name)
        return cls(
            database_path=Path(database_path),
            project_id=project.project_id,
            name=project.name,
            created_at=project.created_at,
        )

    @property
    def capture_directory(self) -> Path:
        return (
            self.database_path.parent
            / "captures"
            / self.project_id
        )

    def capture_target(
        self,
        direction: Direction,
    ) -> Tuple[JsonlFrameRecorder, str]:
        path = self.capture_directory / f"{direction.value}.jsonl"
        return JsonlFrameRecorder(path), str(path.resolve())

    def to_stored_project(self, state: CalibrationUiState) -> StoredProject:
        project = CalibrationProject(
            project_id=self.project_id,
            name=self.name,
            created_at=self.created_at,
            updated_at=_utc_now(),
            original_cloud_hex=state.original_document.encode_hex(),
            directions=tuple(
                dataset.record
                for dataset in sorted(
                    state.datasets,
                    key=lambda item: item.record.direction.index,
                )
            ),
        )
        return StoredProject(
            project=project,
            current_cloud_hex=state.current_document.encode_hex(),
            capture_format="jsonl",
            vehicle_name=self.vehicle_name,
            vehicle_vin=self.vehicle_vin,
        )

    def save(self, state: CalibrationUiState) -> None:
        stored = self.to_stored_project(state)
        with ProjectRepository(self.database_path) as repository:
            repository.save_project(stored)
            history = repository.parameter_history(self.project_id)
            current_hex = state.current_document.encode_hex()
            if not history or history[-1].cloud_hex != current_hex:
                repository.append_parameter_history(
                    self.project_id,
                    current_hex,
                    "桌面界面保存",
                )
            repository.save_analysis(
                self.project_id,
                state.result,
                current_hex,
            )
            repository.clear_recovery(self.project_id)
        self.persisted = True

    def save_recovery(self, state: CalibrationUiState) -> None:
        with ProjectRepository(self.database_path) as repository:
            repository.save_recovery(self.to_stored_project(state))

    @classmethod
    def load(
        cls,
        database_path: Path,
        project_id: str,
    ) -> Tuple["ProjectWorkspace", CalibrationUiState]:
        database_path = Path(database_path)
        with ProjectRepository(database_path) as repository:
            stored = repository.load_project(project_id)
        original_hex = (
            stored.project.original_cloud_hex or stored.current_cloud_hex
        )
        if original_hex is None:
            raise ValueError("project has no cloud HEX")
        current_hex = stored.current_cloud_hex or original_hex
        datasets: Tuple[DirectionDataset, ...] = ReplayService(
            base_dir=database_path.parent
        ).rebuild_project(stored)
        state = CalibrationUiState(
            decode_cloud(original_hex),
            datasets,
            current_document=decode_cloud(current_hex),
        )
        workspace = cls(
            database_path=database_path,
            project_id=stored.project.project_id,
            name=stored.project.name,
            created_at=stored.project.created_at,
            vehicle_name=stored.vehicle_name,
            vehicle_vin=stored.vehicle_vin,
            persisted=True,
        )
        return workspace, state
