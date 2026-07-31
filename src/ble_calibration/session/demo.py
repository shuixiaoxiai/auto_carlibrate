"""Replay a generated manifest through the real direction-session controller."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..domain.enums import Direction, DirectionStatus, EventType
from ..domain.models import CanFrame
from .controller import DirectionSessionController


def load_can_jsonl(path: Path) -> List[CanFrame]:
    frames = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                frames.append(CanFrame.from_json_record(json.loads(line)))
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"invalid CAN frame at line {line_number}: {error}") from error
    frames.sort(key=lambda frame: (frame.timestamp, frame.arbitration_id))
    return frames


def replay_manifest_session(
    frame_path: Path,
    manifest_path: Path,
) -> Tuple[DirectionSessionController, Dict[str, Any]]:
    frames = load_can_jsonl(frame_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    directions = manifest.get("directions")
    if not isinstance(directions, list) or not directions:
        raise ValueError("manifest has no directions")

    controller = DirectionSessionController()
    result_items = []
    for item in directions:
        direction = Direction.from_label(str(item["name"]))
        controller.select_direction(direction)
        controller.start()
        controller.set_distances(
            float(item["lock_distance_m"]),
            float(item["unlock_distance_m"]),
        )
        start_time = float(item["start_time"])
        end_time = float(item["end_time"])
        for frame in frames:
            if frame.timestamp < start_time:
                continue
            if frame.timestamp > end_time + 1e-9:
                break
            controller.process_frame(frame)
        controller.manual_stop(end_time)

        record = controller.record_for(direction)
        assert record is not None
        result_items.append({
            "direction": direction.label,
            "status": record.status.value,
            "sample_count": record.sample_count,
            "lock_event_time": (
                None
                if record.event(EventType.LOCK) is None
                else record.event(EventType.LOCK).timestamp
            ),
            "unlock_event_time": (
                None
                if record.event(EventType.UNLOCK) is None
                else record.event(EventType.UNLOCK).timestamp
            ),
            "lock_distance_m": record.actual_lock_distance_m,
            "unlock_distance_m": record.actual_unlock_distance_m,
        })

    complete_count = sum(
        record.status is DirectionStatus.COMPLETE for record in controller.records
    )
    summary = {
        "direction_count": len(result_items),
        "complete_count": complete_count,
        "incomplete_count": len(result_items) - complete_count,
        "directions": result_items,
    }
    return controller, summary
