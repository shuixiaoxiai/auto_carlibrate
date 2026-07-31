"""Wall-clock Mock capture and What-if stability gate."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ble_calibration.can import MockCanSource, decode_frame
from ble_calibration.capture import CaptureWorker
from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
    write_jsonl,
)
from ble_calibration.ui import build_generated_demo_state


class DecodeCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frames = 0
        self.decoded = 0

    def on_frame(self, frame) -> None:
        decoded = decode_frame(frame.arbitration_id, frame.data)
        with self._lock:
            self.frames += 1
            if decoded is not None:
                self.decoded += 1

    def snapshot(self) -> tuple:
        with self._lock:
            return self.frames, self.decoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--source-speed", type=float, default=100.0)
    parser.add_argument("--recompute-period", type=float, default=0.2)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-peak-memory-mb", type=float, default=256.0)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")
    if args.source_speed <= 0:
        parser.error("--source-speed must be positive")
    if args.recompute_period <= 0:
        parser.error("--recompute-period must be positive")
    if args.max_peak_memory_mb <= 0:
        parser.error("--max-peak-memory-mb must be positive")

    frames, _ = generate_mock_session(MockConfig(seed=20260730))
    state = build_generated_demo_state(seed=20260730)
    counter = DecodeCounter()
    recompute_count = 0
    max_recompute_ms = 0.0
    started = time.monotonic()
    tracemalloc.start()

    with tempfile.TemporaryDirectory() as temp_dir:
        capture_path = Path(temp_dir) / "stability.jsonl"
        write_jsonl(capture_path, frames)
        worker = CaptureWorker(
            MockCanSource(
                capture_path,
                speed=args.source_speed,
                loop=True,
            ),
            on_frame=counter.on_frame,
        )
        worker.start()
        next_recompute = started
        deadline = started + args.duration_seconds
        strict = False
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_recompute:
                strict = not strict
                if strict:
                    result = state.apply_updates(
                        unlock_thresholds=(
                            state.current_document.parameters.unlock_thresholds
                        ),
                        lock_thresholds=tuple(
                            value - 1 for value in REFERENCE_LOCK_THRESHOLDS
                        ),
                    )
                else:
                    result = state.restore()
                recompute_count += 1
                max_recompute_ms = max(max_recompute_ms, result.elapsed_ms)
                if len(result.directions) != 8:
                    raise RuntimeError("stability recompute lost direction data")
                next_recompute += args.recompute_period
            if worker.last_error is not None:
                raise RuntimeError(f"capture failed: {worker.last_error}")
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
        worker.close()

    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    frame_count, decoded_count = counter.snapshot()
    if frame_count == 0 or decoded_count == 0:
        raise RuntimeError("stability run did not receive decodable CAN frames")
    if max_recompute_ms >= 200.0:
        raise RuntimeError(
            f"recompute exceeded 200 ms: {max_recompute_ms:.2f} ms"
        )
    peak_memory_mb = peak_memory / 1024 / 1024
    if peak_memory_mb >= args.max_peak_memory_mb:
        raise RuntimeError(
            "peak traced memory exceeded limit: "
            f"{peak_memory_mb:.2f} >= {args.max_peak_memory_mb:.2f} MB"
        )
    output = {
        "ok": True,
        "duration_seconds": round(time.monotonic() - started, 3),
        "frame_count": frame_count,
        "decoded_frame_count": decoded_count,
        "recompute_count": recompute_count,
        "max_recompute_ms": round(max_recompute_ms, 3),
        "current_memory_mb": round(current_memory / 1024 / 1024, 3),
        "peak_memory_mb": round(peak_memory_mb, 3),
    }
    report_text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text + "\n", encoding="utf-8")
    print(report_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
