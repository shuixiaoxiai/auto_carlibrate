"""Live ZLG CAN source with imports deferred until Windows connection time."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..config.settings import CanSettings
from ..domain.models import CanFrame
from .source import CanSource, CanSourceError, SourceState

BusFactory = Callable[..., Any]


class ZlgCanSource(CanSource):
    def __init__(
        self,
        settings: CanSettings,
        bus_factory: Optional[BusFactory] = None,
        device_type: Any = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self._bus_factory = bus_factory
        self._device_type = device_type
        self._bus: Any = None

    def _resolve_dependencies(self) -> tuple:
        if self._bus_factory is not None:
            return self._bus_factory, self._device_type or self.settings.device_type
        try:
            import can
            from zlgcan.zlgcan import ZCANDeviceType
        except ImportError as error:
            raise CanSourceError(
                "ZLG CAN requires python-can==4.6.1 and zlgcan==0.3.0"
            ) from error
        try:
            device_type = getattr(ZCANDeviceType, self.settings.device_type)
        except AttributeError as error:
            raise CanSourceError(
                f"unknown ZLG device type: {self.settings.device_type}"
            ) from error
        return can.Bus, device_type

    def connect(self) -> None:
        if self.state in (SourceState.CONNECTED, SourceState.RUNNING):
            return
        self._set_state(SourceState.CONNECTING, "opening ZLG CAN device")
        try:
            bus_factory, device_type = self._resolve_dependencies()
            kwargs = {
                "interface": self.settings.interface,
                "device_type": device_type,
                "device_index": self.settings.device_index,
                "configs": [{
                    "bitrate": self.settings.bitrate,
                    "dbitrate": self.settings.data_bitrate,
                    "resistance": 1 if self.settings.resistance_enabled else 0,
                }],
            }
            if self.settings.library_path:
                kwargs["libpath"] = self.settings.library_path
            self._bus = bus_factory(**kwargs)
        except Exception as error:
            self._bus = None
            self._set_state(SourceState.ERROR, str(error))
            if isinstance(error, CanSourceError):
                raise
            raise CanSourceError(f"cannot open ZLG CAN device: {error}") from error
        self._set_state(SourceState.CONNECTED, "ZLG CAN device opened")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        if self._bus is None:
            if self.state in (SourceState.STOPPED, SourceState.ERROR):
                return None
            raise CanSourceError("ZLG CAN source is not connected")
        if self.state is SourceState.CONNECTED:
            self._set_state(SourceState.RUNNING, "receiving CAN frames")

        deadline = time.monotonic() + timeout
        while self._bus is not None:
            remaining = max(0.0, deadline - time.monotonic())
            message = self._bus.recv(timeout=remaining)
            if message is None:
                return None
            if getattr(message, "channel", self.settings.channel) != self.settings.channel:
                if remaining <= 0:
                    return None
                continue
            return CanFrame(
                timestamp=float(message.timestamp),
                arbitration_id=int(message.arbitration_id),
                data=bytes(message.data),
                channel=int(getattr(message, "channel", self.settings.channel)),
                is_fd=bool(getattr(message, "is_fd", True)),
                bitrate_switch=bool(getattr(message, "bitrate_switch", True)),
                receive_monotonic=time.monotonic(),
            )
        return None

    def stop(self) -> None:
        if self.state is SourceState.STOPPED and self._bus is None:
            return
        self._set_state(SourceState.STOPPING, "closing ZLG CAN device")
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception as error:
                self._set_state(SourceState.ERROR, f"device close failed: {error}")
                raise CanSourceError(f"cannot close ZLG CAN device: {error}") from error
        self._set_state(SourceState.STOPPED, "ZLG CAN device closed")
