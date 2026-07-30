"""Generate deterministic eight-direction CAN data without a vehicle.

The generated frames use the same CAN identifiers and byte positions as
``can_read_save.py``. JSONL output requires only the Python standard library.
BLF output is optional and becomes available when ``python-can`` is installed.

Examples:

  python tools/mock_can_generate.py
  python tools/mock_can_generate.py --output mock_data/session.jsonl --seed 7
  python tools/mock_can_generate.py --output mock_data/session.blf --format blf
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from .can_protocol import (
        CANID_LOCKREQ,
        CANID_MASTER,
        CANID_NODEAB,
        CANID_NODECD,
        make_lock_request_payload,
        make_master_payload,
        make_node_ab_payload,
        make_node_cd_payload,
    )
except ImportError:
    from can_protocol import (
        CANID_LOCKREQ,
        CANID_MASTER,
        CANID_NODEAB,
        CANID_NODECD,
        make_lock_request_payload,
        make_master_payload,
        make_node_ab_payload,
        make_node_cd_payload,
    )

DIRECTIONS = [
    "正前",
    "右前",
    "正右",
    "右后",
    "正后",
    "左后",
    "正左",
    "左前",
]

NODE_NAMES = ["master", "front", "rear", "left", "right"]
NODE_ANGLES = [0.0, 0.0, 180.0, 270.0, 90.0]
REFERENCE_LOCK_THRESHOLDS = [-78, -77, -79, -78, -78]
REFERENCE_UNLOCK_THRESHOLDS = [-67, -66, -68, -67, -67]
MOCK_LOCK_DISTANCES = [8.6, 10.2, 12.1, 14.3, 9.4, 13.6, 11.0, 8.9]
MOCK_UNLOCK_DISTANCES = [1.4, 2.6, 4.2, 5.5, 3.1, 4.8, 2.0, 5.8]


@dataclass(frozen=True)
class MockFrame:
    timestamp: float
    arbitration_id: int
    data: bytes
    channel: int = 0
    is_fd: bool = True
    bitrate_switch: bool = True

    def to_json_record(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "arbitration_id": self.arbitration_id,
            "channel": self.channel,
            "is_fd": self.is_fd,
            "bitrate_switch": self.bitrate_switch,
            "data": self.data.hex().upper(),
        }


@dataclass(frozen=True)
class MockConfig:
    seed: int = 20260730
    sample_rate_hz: float = 10.0
    direction_duration_s: float = 24.0
    direction_gap_s: float = 2.0
    turn_time_s: float = 13.0
    request_hold_s: float = 0.5
    channel: int = 0
    blf_start_epoch_s: float = 1_700_000_000.0


def angle_difference(first: float, second: float) -> float:
    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


def deterministic_noise(
    sample_index: int,
    direction_index: int,
    node_index: int,
    seed: int,
) -> float:
    seed_phase = (seed % 10_000) * 0.001
    return (
        math.sin(sample_index * 1.73 + direction_index * 2.31 + node_index * 1.19 + seed_phase)
        * 0.9
        + math.sin(sample_index * 0.41 + node_index * 2.7 + seed_phase * 0.3) * 0.45
    )


def rssi_value(
    local_time: float,
    sample_index: int,
    direction_index: int,
    node_index: int,
    config: MockConfig,
) -> int:
    direction_angle = direction_index * 45.0
    if node_index == 0:
        directional_gain = 7.5 * math.cos(math.radians(direction_angle))
    else:
        directional_gain = 5.2 * math.cos(
            math.radians(angle_difference(direction_angle, NODE_ANGLES[node_index]))
        )

    noise = deterministic_noise(sample_index, direction_index, node_index, config.seed)
    if local_time <= 10.0:
        value = -47.0 - 3.65 * local_time + directional_gain + noise
    elif local_time <= config.turn_time_s:
        value = -84.0 + directional_gain * 0.25 + noise
    else:
        value = -84.0 + 3.2 * (local_time - config.turn_time_s) + directional_gain + noise
    return max(-127, min(-1, int(round(value))))


def build_direction_samples(
    direction_index: int,
    config: MockConfig,
) -> List[Tuple[float, List[int]]]:
    sample_period = 1.0 / config.sample_rate_hz
    sample_count = int(round(config.direction_duration_s * config.sample_rate_hz))
    samples: List[Tuple[float, List[int]]] = []
    for sample_index in range(sample_count + 1):
        local_time = round(sample_index * sample_period, 6)
        values = [
            rssi_value(local_time, sample_index, direction_index, node_index, config)
            for node_index in range(len(NODE_NAMES))
        ]
        samples.append((local_time, values))
    return samples


def detect_reference_events(
    samples: Sequence[Tuple[float, Sequence[int]]],
    config: MockConfig,
) -> Tuple[Optional[float], Optional[float]]:
    sample_period = 1.0 / config.sample_rate_hz
    lock_duration = 0.0
    unlock_durations = [0.0] * len(NODE_NAMES)
    lock_time: Optional[float] = None
    unlock_time: Optional[float] = None

    for local_time, values in samples:
        if local_time <= config.turn_time_s and lock_time is None:
            in_lock_zone = all(
                value <= REFERENCE_LOCK_THRESHOLDS[index]
                for index, value in enumerate(values)
            )
            lock_duration = lock_duration + sample_period if in_lock_zone else 0.0
            if lock_duration + 1e-9 >= 2.0:
                lock_time = round(local_time + sample_period, 6)

        if local_time >= config.turn_time_s and unlock_time is None:
            for index, value in enumerate(values):
                if value >= REFERENCE_UNLOCK_THRESHOLDS[index]:
                    unlock_durations[index] += sample_period
                else:
                    unlock_durations[index] = 0.0
                if unlock_durations[index] + 1e-9 >= 0.5:
                    unlock_time = round(local_time + sample_period, 6)
                    break

    return lock_time, unlock_time


def request_at_time(
    local_time: float,
    lock_time: Optional[float],
    unlock_time: Optional[float],
    hold_time: float,
) -> int:
    if lock_time is not None and lock_time <= local_time < lock_time + hold_time:
        return 2
    if unlock_time is not None and unlock_time <= local_time < unlock_time + hold_time:
        return 1
    return 0


def generate_mock_session(config: MockConfig) -> Tuple[List[MockFrame], Dict[str, Any]]:
    if config.sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be greater than zero")
    if config.direction_duration_s <= config.turn_time_s:
        raise ValueError("direction_duration_s must be greater than turn_time_s")
    if config.direction_gap_s < 0:
        raise ValueError("direction_gap_s cannot be negative")

    sample_period = 1.0 / config.sample_rate_hz
    frame_offsets = [0.0, sample_period * 0.1, sample_period * 0.2, sample_period * 0.3]
    frames: List[MockFrame] = []
    direction_manifests: List[Dict[str, Any]] = []

    for direction_index, direction_name in enumerate(DIRECTIONS):
        direction_start = direction_index * (
            config.direction_duration_s + config.direction_gap_s
        )
        samples = build_direction_samples(direction_index, config)
        lock_time, unlock_time = detect_reference_events(samples, config)

        for local_time, values in samples:
            request = request_at_time(
                local_time,
                lock_time,
                unlock_time,
                config.request_hold_s,
            )
            base_time = direction_start + local_time
            frames.extend([
                MockFrame(
                    timestamp=round(base_time + frame_offsets[0], 6),
                    arbitration_id=CANID_MASTER,
                    data=make_master_payload(values[0]),
                    channel=config.channel,
                ),
                MockFrame(
                    timestamp=round(base_time + frame_offsets[1], 6),
                    arbitration_id=CANID_NODEAB,
                    data=make_node_ab_payload(values[1], values[2]),
                    channel=config.channel,
                ),
                MockFrame(
                    timestamp=round(base_time + frame_offsets[2], 6),
                    arbitration_id=CANID_NODECD,
                    data=make_node_cd_payload(values[3], values[4]),
                    channel=config.channel,
                ),
                MockFrame(
                    timestamp=round(base_time + frame_offsets[3], 6),
                    arbitration_id=CANID_LOCKREQ,
                    data=make_lock_request_payload(request),
                    channel=config.channel,
                ),
            ])

        direction_manifests.append({
            "index": direction_index,
            "name": direction_name,
            "start_time": round(direction_start, 6),
            "end_time": round(direction_start + config.direction_duration_s, 6),
            "turn_time": round(direction_start + config.turn_time_s, 6),
            "lock_event_time": (
                None if lock_time is None else round(direction_start + lock_time, 6)
            ),
            "unlock_event_time": (
                None if unlock_time is None else round(direction_start + unlock_time, 6)
            ),
            "lock_distance_m": MOCK_LOCK_DISTANCES[direction_index],
            "unlock_distance_m": MOCK_UNLOCK_DISTANCES[direction_index],
        })

    frames.sort(key=lambda frame: (frame.timestamp, frame.arbitration_id))
    manifest: Dict[str, Any] = {
        "schema": "digital-key-mock-can/v1",
        "generator": "tools/mock_can_generate.py",
        "config": asdict(config),
        "reference_thresholds": {
            "lock": REFERENCE_LOCK_THRESHOLDS,
            "unlock": REFERENCE_UNLOCK_THRESHOLDS,
            "lock_stable_s": 2.0,
            "unlock_stable_s": 0.5,
        },
        "frame_count": len(frames),
        "directions": direction_manifests,
    }
    return frames, manifest


def write_jsonl(path: Path, frames: Sequence[MockFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for frame in frames:
            output.write(json.dumps(frame.to_json_record(), ensure_ascii=False))
            output.write("\n")


def write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
        output.write("\n")


def write_blf(
    path: Path,
    frames: Sequence[MockFrame],
    start_epoch_s: float,
) -> None:
    try:
        import can
    except ImportError as error:
        raise RuntimeError(
            "BLF output requires python-can; install it or use --format jsonl"
        ) from error

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = can.BLFWriter(str(path), channel=frames[0].channel if frames else 0)
    try:
        for frame in frames:
            writer.write(can.Message(
                timestamp=start_epoch_s + frame.timestamp,
                arbitration_id=frame.arbitration_id,
                data=frame.data,
                channel=frame.channel,
                is_fd=frame.is_fd,
                bitrate_switch=frame.bitrate_switch,
                is_extended_id=False,
            ))
    finally:
        writer.stop()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成八方向数字钥匙 RSSI 与解闭锁 Mock CAN 数据"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mock_data/eight_directions.jsonl"),
        help="输出文件，默认 mock_data/eight_directions.jsonl",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "jsonl", "blf"],
        default="auto",
        help="输出格式；auto 根据扩展名判断",
    )
    parser.add_argument("--manifest", type=Path, default=None, help="会话清单 JSON 路径")
    parser.add_argument("--seed", type=int, default=MockConfig.seed)
    parser.add_argument("--sample-rate", type=float, default=MockConfig.sample_rate_hz)
    parser.add_argument(
        "--direction-duration",
        type=float,
        default=MockConfig.direction_duration_s,
    )
    parser.add_argument("--direction-gap", type=float, default=MockConfig.direction_gap_s)
    parser.add_argument("--turn-time", type=float, default=MockConfig.turn_time_s)
    parser.add_argument("--request-hold", type=float, default=MockConfig.request_hold_s)
    parser.add_argument("--channel", type=int, default=MockConfig.channel)
    parser.add_argument("--blf-start-epoch", type=float, default=MockConfig.blf_start_epoch_s)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    output_format = args.format
    if output_format == "auto":
        output_format = "blf" if args.output.suffix.lower() == ".blf" else "jsonl"
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    config = MockConfig(
        seed=args.seed,
        sample_rate_hz=args.sample_rate,
        direction_duration_s=args.direction_duration,
        direction_gap_s=args.direction_gap,
        turn_time_s=args.turn_time,
        request_hold_s=args.request_hold,
        channel=args.channel,
        blf_start_epoch_s=args.blf_start_epoch,
    )

    try:
        frames, manifest = generate_mock_session(config)
        if output_format == "blf":
            write_blf(args.output, frames, config.blf_start_epoch_s)
        else:
            write_jsonl(args.output, frames)
        write_manifest(manifest_path, manifest)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"生成失败: {error}", file=sys.stderr)
        return 1

    print(
        f"生成完成: {args.output}  帧数={len(frames)}  "
        f"方向={len(manifest['directions'])}  清单={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
