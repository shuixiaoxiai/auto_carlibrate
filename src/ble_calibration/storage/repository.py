"""SQLite project repository with history and crash-recovery snapshots."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from ..analysis.recompute import RecomputeResult
from ..domain.models import CalibrationProject, DirectionRecord
from .models import (
    AnalysisSnapshot,
    ParameterHistoryEntry,
    ProjectSummary,
    StoredProject,
)
from .serialization import recompute_result_to_dict

SCHEMA_VERSION = 1


class ProjectNotFoundError(KeyError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self.database_path),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    original_cloud_hex TEXT,
                    current_cloud_hex TEXT,
                    analysis_version TEXT NOT NULL,
                    capture_path TEXT,
                    capture_format TEXT,
                    vehicle_name TEXT,
                    vehicle_vin TEXT
                );

                CREATE TABLE IF NOT EXISTS directions (
                    project_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY (project_id, direction),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS parameter_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    cloud_hex TEXT NOT NULL,
                    note TEXT,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS analysis_history (
                    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    cloud_hex TEXT,
                    analysis_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS recovery_snapshots (
                    project_id TEXT PRIMARY KEY,
                    saved_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                """
            )
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def save_project(self, stored: StoredProject) -> None:
        project = stored.project
        if stored.capture_format not in (None, "jsonl", "blf"):
            raise ValueError("capture_format must be jsonl, blf, or None")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO projects (
                    project_id, schema_version, name, created_at, updated_at,
                    original_cloud_hex, current_cloud_hex, analysis_version,
                    capture_path, capture_format, vehicle_name, vehicle_vin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    original_cloud_hex=excluded.original_cloud_hex,
                    current_cloud_hex=excluded.current_cloud_hex,
                    analysis_version=excluded.analysis_version,
                    capture_path=excluded.capture_path,
                    capture_format=excluded.capture_format,
                    vehicle_name=excluded.vehicle_name,
                    vehicle_vin=excluded.vehicle_vin
                """,
                (
                    project.project_id,
                    project.schema,
                    project.name,
                    project.created_at,
                    project.updated_at,
                    project.original_cloud_hex,
                    stored.current_cloud_hex,
                    project.analysis_version,
                    stored.capture_path,
                    stored.capture_format,
                    stored.vehicle_name,
                    stored.vehicle_vin,
                ),
            )
            self._connection.execute(
                "DELETE FROM directions WHERE project_id = ?",
                (project.project_id,),
            )
            self._connection.executemany(
                """
                INSERT INTO directions (project_id, direction, record_json)
                VALUES (?, ?, ?)
                """,
                (
                    (
                        project.project_id,
                        record.direction.value,
                        json.dumps(record.to_dict(), ensure_ascii=False),
                    )
                    for record in project.directions
                ),
            )

    def load_project(self, project_id: str) -> StoredProject:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise ProjectNotFoundError(project_id)
            direction_rows = self._connection.execute(
                """
                SELECT record_json FROM directions
                WHERE project_id = ?
                ORDER BY direction
                """,
                (project_id,),
            ).fetchall()
        directions = tuple(sorted((
            DirectionRecord.from_dict(json.loads(item["record_json"]))
            for item in direction_rows
        ), key=lambda record: record.direction.index))
        project = CalibrationProject(
            project_id=row["project_id"],
            schema=row["schema_version"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            original_cloud_hex=row["original_cloud_hex"],
            directions=directions,
            analysis_version=row["analysis_version"],
        )
        return StoredProject(
            project=project,
            current_cloud_hex=row["current_cloud_hex"],
            capture_path=row["capture_path"],
            capture_format=row["capture_format"],
            vehicle_name=row["vehicle_name"],
            vehicle_vin=row["vehicle_vin"],
        )

    def list_projects(self) -> Tuple[ProjectSummary, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT p.project_id, p.name, p.updated_at, p.capture_path,
                       COUNT(d.direction) AS direction_count
                FROM projects p
                LEFT JOIN directions d ON d.project_id = p.project_id
                GROUP BY p.project_id
                ORDER BY p.updated_at DESC, p.name
                """
            ).fetchall()
        return tuple(
            ProjectSummary(
                project_id=row["project_id"],
                name=row["name"],
                updated_at=row["updated_at"],
                direction_count=int(row["direction_count"]),
                capture_path=row["capture_path"],
            )
            for row in rows
        )

    def append_parameter_history(
        self,
        project_id: str,
        cloud_hex: str,
        note: Optional[str] = None,
    ) -> ParameterHistoryEntry:
        changed_at = _utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO parameter_history (project_id, changed_at, cloud_hex, note)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, changed_at, cloud_hex, note),
            )
        return ParameterHistoryEntry(
            int(cursor.lastrowid),
            project_id,
            changed_at,
            cloud_hex,
            note,
        )

    def parameter_history(
        self,
        project_id: str,
    ) -> Tuple[ParameterHistoryEntry, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM parameter_history
                WHERE project_id = ?
                ORDER BY history_id
                """,
                (project_id,),
            ).fetchall()
        return tuple(
            ParameterHistoryEntry(
                int(row["history_id"]),
                row["project_id"],
                row["changed_at"],
                row["cloud_hex"],
                row["note"],
            )
            for row in rows
        )

    def save_analysis(
        self,
        project_id: str,
        result: RecomputeResult,
        cloud_hex: Optional[str],
    ) -> AnalysisSnapshot:
        created_at = _utc_now()
        payload = recompute_result_to_dict(result)
        analysis_version = next(
            (
                item.analysis_version
                for item in result.directions.values()
            ),
            "ble-calibration-analysis/v1",
        )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO analysis_history (
                    project_id, created_at, cloud_hex, analysis_version, result_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    created_at,
                    cloud_hex,
                    analysis_version,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return AnalysisSnapshot(
            int(cursor.lastrowid),
            project_id,
            created_at,
            cloud_hex,
            analysis_version,
            payload,
        )

    def latest_analysis(self, project_id: str) -> Optional[AnalysisSnapshot]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM analysis_history
                WHERE project_id = ?
                ORDER BY analysis_id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return AnalysisSnapshot(
            int(row["analysis_id"]),
            row["project_id"],
            row["created_at"],
            row["cloud_hex"],
            row["analysis_version"],
            json.loads(row["result_json"]),
        )

    def save_recovery(self, stored: StoredProject) -> None:
        snapshot = {
            "project": stored.project.to_dict(),
            "current_cloud_hex": stored.current_cloud_hex,
            "capture_path": stored.capture_path,
            "capture_format": stored.capture_format,
            "vehicle_name": stored.vehicle_name,
            "vehicle_vin": stored.vehicle_vin,
        }
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO recovery_snapshots (project_id, saved_at, snapshot_json)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    saved_at=excluded.saved_at,
                    snapshot_json=excluded.snapshot_json
                """,
                (
                    stored.project.project_id,
                    _utc_now(),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )

    def load_recovery(self, project_id: str) -> Optional[StoredProject]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT snapshot_json FROM recovery_snapshots
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        snapshot = json.loads(row["snapshot_json"])
        project = CalibrationProject.from_dict(snapshot["project"])
        return StoredProject(
            project=project,
            current_cloud_hex=snapshot.get("current_cloud_hex"),
            capture_path=snapshot.get("capture_path"),
            capture_format=snapshot.get("capture_format"),
            vehicle_name=snapshot.get("vehicle_name"),
            vehicle_vin=snapshot.get("vehicle_vin"),
        )

    def clear_recovery(self, project_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM recovery_snapshots WHERE project_id = ?",
                (project_id,),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "ProjectRepository":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
