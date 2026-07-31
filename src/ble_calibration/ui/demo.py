"""Qt-independent construction of the deterministic eight-direction UI state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from ..analysis import DirectionDataset
from ..cloud import demo_cloud_document
from ..domain import Direction
from ..mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
)
from ..session import DirectionSessionController
from ..session.demo import load_can_jsonl
from .state import CalibrationUiState


def _datasets_from_frames(frames, manifest) -> Tuple[DirectionDataset, ...]:
    controller = DirectionSessionController()
    datasets = []
    for item in manifest["directions"]:
        direction = Direction.from_label(str(item["name"]))
        controller.select_direction(direction)
        controller.start(walking_speed_mps=1.0)
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
        record = controller.manual_stop(end_time)
        datasets.append(
            DirectionDataset(record, controller.samples_for(direction))
        )
    return tuple(datasets)


def build_generated_demo_state(seed: int = 20260730) -> CalibrationUiState:
    frames, manifest = generate_mock_session(MockConfig(seed=seed))
    document = demo_cloud_document(
        REFERENCE_UNLOCK_THRESHOLDS,
        REFERENCE_LOCK_THRESHOLDS,
    )
    return CalibrationUiState(document, _datasets_from_frames(frames, manifest))


def build_file_demo_state(
    frame_path: Path,
    manifest_path: Path,
) -> CalibrationUiState:
    frames = load_can_jsonl(frame_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thresholds = manifest["reference_thresholds"]
    document = demo_cloud_document(
        thresholds["unlock"],
        thresholds["lock"],
    )
    return CalibrationUiState(document, _datasets_from_frames(frames, manifest))
