"""Application settings with JSON persistence and validation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class CanSettings:
    interface: str = "zlgcan"
    device_type: str = "ZCAN_USBCANFD_200U"
    device_index: int = 0
    channel: int = 0
    bitrate: int = 500_000
    data_bitrate: int = 2_000_000
    resistance_enabled: bool = True
    library_path: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.interface.strip():
            raise ValueError("CAN interface cannot be empty")
        if not self.device_type.strip():
            raise ValueError("CAN device_type cannot be empty")
        if self.device_index < 0 or self.channel < 0:
            raise ValueError("CAN device index and channel cannot be negative")
        if self.bitrate <= 0 or self.data_bitrate <= 0:
            raise ValueError("CAN bitrates must be greater than zero")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanSettings":
        return cls(
            interface=str(data.get("interface", cls.interface)),
            device_type=str(data.get("device_type", cls.device_type)),
            device_index=int(data.get("device_index", cls.device_index)),
            channel=int(data.get("channel", cls.channel)),
            bitrate=int(data.get("bitrate", cls.bitrate)),
            data_bitrate=int(data.get("data_bitrate", cls.data_bitrate)),
            resistance_enabled=bool(
                data.get("resistance_enabled", cls.resistance_enabled)
            ),
            library_path=(
                None if data.get("library_path") in (None, "") else str(data["library_path"])
            ),
        )


@dataclass(frozen=True)
class RuntimeSettings:
    stale_period_multiplier: float = 3.0
    aligned_sample_rate_hz: float = 10.0
    ui_refresh_rate_hz: float = 20.0
    recompute_budget_ms: int = 200
    default_walking_speed_mps: float = 1.0

    def __post_init__(self) -> None:
        if self.stale_period_multiplier < 1:
            raise ValueError("stale_period_multiplier must be at least 1")
        if self.aligned_sample_rate_hz <= 0 or self.ui_refresh_rate_hz <= 0:
            raise ValueError("sample and refresh rates must be greater than zero")
        if self.recompute_budget_ms <= 0:
            raise ValueError("recompute_budget_ms must be greater than zero")
        if self.default_walking_speed_mps <= 0:
            raise ValueError("default_walking_speed_mps must be greater than zero")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeSettings":
        return cls(
            stale_period_multiplier=float(
                data.get("stale_period_multiplier", cls.stale_period_multiplier)
            ),
            aligned_sample_rate_hz=float(
                data.get("aligned_sample_rate_hz", cls.aligned_sample_rate_hz)
            ),
            ui_refresh_rate_hz=float(
                data.get("ui_refresh_rate_hz", cls.ui_refresh_rate_hz)
            ),
            recompute_budget_ms=int(
                data.get("recompute_budget_ms", cls.recompute_budget_ms)
            ),
            default_walking_speed_mps=float(
                data.get(
                    "default_walking_speed_mps",
                    cls.default_walking_speed_mps,
                )
            ),
        )


@dataclass(frozen=True)
class AppSettings:
    can: CanSettings = field(default_factory=CanSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AppSettings":
        return cls(
            can=CanSettings.from_dict(data.get("can", {})),
            runtime=RuntimeSettings.from_dict(data.get("runtime", {})),
        )


def default_user_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "BLECalibration"
    return Path.home() / ".ble-calibration"


def load_settings(path: Path) -> AppSettings:
    if not path.exists():
        return AppSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load settings from {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("settings root must be a JSON object")
    return AppSettings.from_dict(raw)


def save_settings(path: Path, settings: AppSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
