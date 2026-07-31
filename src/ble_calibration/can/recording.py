"""Raw JSONL and rotating BLF recorders for unified :class:`CanFrame` data."""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol

from ..domain.models import CanFrame


class FrameRecorder(Protocol):
    def write(self, frame: CanFrame) -> None:
        ...

    def stop(self) -> None:
        ...


class JsonlFrameRecorder:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._output = self.path.open("w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self.frame_count = 0

    def write(self, frame: CanFrame) -> None:
        with self._lock:
            if self._output is None:
                raise RuntimeError("JSONL recorder is closed")
            self._output.write(json.dumps(frame.to_json_record(), ensure_ascii=False))
            self._output.write("\n")
            self.frame_count += 1

    def stop(self) -> None:
        with self._lock:
            if self._output is not None:
                self._output.flush()
                self._output.close()
                self._output = None


class RotatingBlfRecorder:
    def __init__(
        self,
        base_path: Path,
        channel: int,
        max_frames: int = 1_000_000,
        writer_factory: Optional[Callable[..., Any]] = None,
        message_factory: Optional[Callable[..., Any]] = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        if max_frames <= 0:
            raise ValueError("max_frames must be greater than zero")
        base_text = str(base_path)
        if base_text.lower().endswith(".blf"):
            base_text = base_text[:-4]
        self.base_path = Path(base_text)
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        self.channel = channel
        self.max_frames = max_frames
        self._writer_factory = writer_factory
        self._message_factory = message_factory
        self._date_text = today().strftime("%Y%m%d")
        self._sequence = 0
        self._writer: Any = None
        self._lock = threading.Lock()
        self.current_frame_count = 0
        self.total_frame_count = 0
        self.paths: List[Path] = []
        self._open_writer()

    def _resolve_factories(self) -> tuple:
        if self._writer_factory is not None and self._message_factory is not None:
            return self._writer_factory, self._message_factory
        try:
            import can
        except ImportError as error:
            raise RuntimeError("BLF recording requires python-can==4.6.1") from error
        return self._writer_factory or can.BLFWriter, self._message_factory or can.Message

    def _current_path(self) -> Path:
        suffix = "" if self._sequence == 0 else f"_{self._sequence + 1}"
        return Path(f"{self.base_path}_{self._date_text}{suffix}.blf")

    def _open_writer(self) -> None:
        writer_factory, _ = self._resolve_factories()
        path = self._current_path()
        self._writer = writer_factory(str(path), channel=self.channel)
        self.paths.append(path)
        self.current_frame_count = 0

    def _roll(self) -> None:
        self._writer.stop()
        self._sequence += 1
        self._open_writer()

    def write(self, frame: CanFrame) -> None:
        with self._lock:
            if self._writer is None:
                raise RuntimeError("BLF recorder is closed")
            if self.current_frame_count >= self.max_frames:
                self._roll()
            _, message_factory = self._resolve_factories()
            message = message_factory(
                timestamp=frame.timestamp,
                arbitration_id=frame.arbitration_id,
                data=frame.data,
                channel=frame.channel,
                is_fd=frame.is_fd,
                bitrate_switch=frame.bitrate_switch,
                is_extended_id=frame.arbitration_id > 0x7FF,
            )
            self._writer.write(message)
            self.current_frame_count += 1
            self.total_frame_count += 1

    def stop(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.stop()
                self._writer = None
