"""Project lifecycle used by the desktop UI without depending on Qt widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

from ..analysis import DirectionDataset
from ..can.recording import (
    FrameRecorder,
    JsonlFrameRecorder,
    RotatingBlfRecorder,
)
from ..cloud import decode_cloud
from ..domain import MAX_DIRECTION_GROUPS, CalibrationProject, Direction
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
    capture_format: str = "jsonl"
    capture_channel: int = 0
    capture_path: Optional[str] = None
    persisted: bool = False

    @classmethod
    def create(
        cls,
        database_path: Path,
        name: str,
        capture_format: str = "jsonl",
        capture_channel: int = 0,
    ) -> "ProjectWorkspace":
        if capture_format not in ("jsonl", "blf"):
            raise ValueError("capture_format must be jsonl or blf")
        if capture_channel < 0:
            raise ValueError("capture_channel cannot be negative")
        project = CalibrationProject(name=name)
        return cls(
            database_path=Path(database_path),
            project_id=project.project_id,
            name=project.name,
            created_at=project.created_at,
            capture_format=capture_format,
            capture_channel=capture_channel,
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
        group_index: int = 1,
        recording_id: Optional[str] = None,
    ) -> Tuple[FrameRecorder, str]:
        if not 1 <= group_index <= MAX_DIRECTION_GROUPS:
            raise ValueError(
                f"group_index must be between 1 and {MAX_DIRECTION_GROUPS}"
            )
        recording_id = recording_id or str(uuid4())
        group_directory = (
            self.capture_directory
            / direction.value
            / f"group-{group_index}"
        )
        if self.capture_format == "jsonl":
            path = group_directory / f"{recording_id}.jsonl"
            return JsonlFrameRecorder(path), str(path.resolve())
        if self.capture_format == "blf":
            recorder = RotatingBlfRecorder(
                group_directory / recording_id,
                channel=self.capture_channel,
            )
            return recorder, str(recorder.paths[0].resolve())
        raise ValueError(f"unsupported capture format: {self.capture_format}")

    def realtime_capture_target(
        self,
        session_id: Optional[str] = None,
    ) -> Tuple[RotatingBlfRecorder, Path]:
        """Create one BLF recorder for an entire phone test session."""
        if self.capture_format != "blf":
            raise ValueError("real-time saving requires BLF capture format")
        session_id = session_id or str(uuid4())
        session_directory = self.capture_directory / "sessions" / session_id
        recorder = RotatingBlfRecorder(
            session_directory / session_id,
            channel=self.capture_channel,
        )
        return recorder, session_directory / "manifest.json"

    def finalize_realtime_capture(
        self,
        recorder: RotatingBlfRecorder,
        manifest_path: Path,
        *,
        started_at: str,
    ) -> str:
        """Persist the complete BLF-volume list and make it the project capture."""
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "format": "blf",
                    "started_at": started_at,
                    "ended_at": _utc_now(),
                    "channel": self.capture_channel,
                    "frame_count": recorder.total_frame_count,
                    "files": [str(path.resolve()) for path in recorder.paths],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        self.capture_path = str(manifest_path.resolve())
        return self.capture_path

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
                    key=lambda item: (
                        item.record.direction.index,
                        item.record.group_index,
                    ),
                )
            ),
            default_walking_speed_mps=state.default_walking_speed_mps,
        )
        return StoredProject(
            project=project,
            current_cloud_hex=state.current_document.encode_hex(),
            capture_path=self.capture_path,
            capture_format=self.capture_format,
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
            default_walking_speed_mps=stored.project.default_walking_speed_mps,
        )
        workspace = cls(
            database_path=database_path,
            project_id=stored.project.project_id,
            name=stored.project.name,
            created_at=stored.project.created_at,
            vehicle_name=stored.vehicle_name,
            vehicle_vin=stored.vehicle_vin,
            capture_format=stored.capture_format or "jsonl",
            capture_path=stored.capture_path,
            persisted=True,
        )
        return workspace, state
