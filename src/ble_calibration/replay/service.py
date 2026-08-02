"""Rebuild aligned direction datasets from JSONL or BLF attachments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..analysis import DirectionDataset
from ..domain.models import CanFrame, DirectionRecord
from ..processing import CanFrameProcessor
from ..session.demo import load_can_jsonl
from ..storage.models import StoredProject


class ReplayError(RuntimeError):
    pass


class ReplayService:
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        blf_reader_factory: Optional[Callable[[str], Iterable[Any]]] = None,
        sample_rate_hz: float = 10.0,
        stale_timeout_s: float = 0.3,
    ) -> None:
        self.base_dir = None if base_dir is None else Path(base_dir)
        self._blf_reader_factory = blf_reader_factory
        self.sample_rate_hz = sample_rate_hz
        self.stale_timeout_s = stale_timeout_s

    def load_frames(self, path: Path, capture_format: Optional[str] = None) -> List[CanFrame]:
        resolved = self._resolve_path(path)
        file_format = capture_format or resolved.suffix.lower().lstrip(".")
        if file_format == "blf" and resolved.name == "manifest.json":
            manifest = json.loads(resolved.read_text(encoding="utf-8"))
            if manifest.get("format") != "blf":
                raise ReplayError(f"unsupported capture manifest: {resolved}")
            frames = []
            for item in manifest.get("files", []):
                frames.extend(self.load_frames(Path(str(item)), "blf"))
            frames.sort(key=lambda frame: (frame.timestamp, frame.arbitration_id))
            return frames
        if file_format == "jsonl":
            return load_can_jsonl(resolved)
        if file_format == "blf":
            return self._load_blf(resolved)
        raise ReplayError(f"unsupported capture format: {file_format}")

    def _resolve_path(self, path: Path) -> Path:
        if path.is_absolute() or self.base_dir is None:
            return path
        return self.base_dir / path

    def _load_blf(self, path: Path) -> List[CanFrame]:
        reader_factory = self._blf_reader_factory
        if reader_factory is None:
            try:
                import can
            except ImportError as error:
                raise ReplayError("BLF replay requires python-can==4.6.1") from error
            reader_factory = can.BLFReader
        reader = reader_factory(str(path))
        frames = []
        try:
            for message in reader:
                channel = getattr(message, "channel", 0)
                frames.append(
                    CanFrame(
                        timestamp=float(message.timestamp),
                        arbitration_id=int(message.arbitration_id),
                        data=bytes(message.data),
                        channel=0 if channel is None else int(channel),
                        is_fd=bool(getattr(message, "is_fd", True)),
                        bitrate_switch=bool(
                            getattr(message, "bitrate_switch", True)
                        ),
                    )
                )
        finally:
            stop = getattr(reader, "stop", None)
            if callable(stop):
                stop()
        frames.sort(key=lambda frame: (frame.timestamp, frame.arbitration_id))
        return frames

    def rebuild_project(self, stored: StoredProject) -> Tuple[DirectionDataset, ...]:
        cache: Dict[Tuple[str, str], List[CanFrame]] = {}
        datasets = []
        for record in stored.project.directions:
            capture_path = record.raw_data_file or stored.capture_path
            if capture_path is None:
                raise ReplayError(
                    f"direction {record.direction.label} has no raw capture path"
                )
            capture_format = stored.capture_format or Path(capture_path).suffix.lstrip(".")
            key = (capture_path, capture_format)
            if key not in cache:
                cache[key] = self.load_frames(Path(capture_path), capture_format)
            datasets.append(self.rebuild_direction(record, cache[key]))
        return tuple(datasets)

    def rebuild_direction(
        self,
        record: DirectionRecord,
        frames: Sequence[CanFrame],
    ) -> DirectionDataset:
        if record.start_timestamp is None or record.end_timestamp is None:
            if record.sample_count == 0:
                return DirectionDataset(record=record, samples=())
            raise ReplayError(
                f"direction {record.direction.label} has no complete time range"
            )
        processor = CanFrameProcessor(
            sample_rate_hz=self.sample_rate_hz,
            stale_timeout_s=self.stale_timeout_s,
        )
        samples = []
        for frame in frames:
            if frame.timestamp < record.start_timestamp:
                continue
            if frame.timestamp > record.end_timestamp:
                break
            result = processor.process(frame, record.direction)
            samples.extend(result.samples)
        return DirectionDataset(record=record, samples=tuple(samples))
