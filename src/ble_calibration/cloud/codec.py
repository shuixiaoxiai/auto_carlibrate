"""Lossless decoder/editor/encoder for digital-key cloud calibration HEX."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .models import CloudParameters, NodeThresholds

BASE_LEN = 35
NODE_COUNT = 5

QUICK_LOCK_FIELDS = (
    "weakFront",
    "weakRear",
    "weakFl",
    "weakFr",
    "strongMst",
    "strongFront",
    "strongRear",
    "strongFl",
    "strongFr",
    "reserve",
)
QUICK_UNLOCK_FIELDS = (
    "unlockTime",
    "frontToFr",
    "frontToFl",
    "rearToFl",
    "rearToFr",
    "reserve",
)
MST_THAN_SLAVE_FIELDS = ("diff", "reserve")
BEVEL_ANGLE_FIELDS = (
    "offsetRFR",
    "offsetRFF",
    "offsetLFL",
    "offsetLFF",
    "offsetLBL",
    "offsetLBB",
    "offsetRBR",
    "offsetRBB",
)


class CloudCodecError(ValueError):
    """Invalid HEX, malformed TLV, or an unsupported edit."""


@dataclass(frozen=True)
class NibbleLocation:
    byte_index: int
    high: bool


@dataclass(frozen=True)
class CloudDocument:
    raw: bytes
    parameters: CloudParameters
    _byte_fields: Mapping[str, Tuple[int, ...]]
    _nibble_fields: Mapping[str, Mapping[str, NibbleLocation]]

    @property
    def original_hex(self) -> str:
        return self.raw.hex().upper()

    def encode_hex(self, parameters: Optional[CloudParameters] = None) -> str:
        target = self.parameters if parameters is None else parameters
        output = bytearray(self.raw)
        self._write_signed_list(
            output,
            "unlock_thresholds",
            target.unlock_thresholds,
        )
        self._write_signed_list(output, "lock_thresholds", target.lock_thresholds)
        self._write_optional_signed_list(output, "mst_unlock", target.mst_unlock)
        self._write_optional_nibbles(output, "quick_lock", target.quick_lock)
        self._write_optional_nibbles(output, "quick_unlock", target.quick_unlock)
        self._write_optional_nibbles(
            output,
            "mst_than_slave",
            target.mst_than_slave,
        )
        self._write_optional_nibbles(output, "bevel_angle", target.bevel_angle)
        return output.hex().upper()

    def with_updates(
        self,
        *,
        unlock_thresholds: Optional[Sequence[int]] = None,
        lock_thresholds: Optional[Sequence[int]] = None,
        mst_unlock: Optional[Sequence[int]] = None,
        strategy_updates: Optional[Mapping[str, Mapping[str, int]]] = None,
    ) -> "CloudDocument":
        parameters = self.parameters
        if unlock_thresholds is not None:
            parameters = replace(
                parameters,
                unlock_thresholds=_node_thresholds(unlock_thresholds),
            )
        if lock_thresholds is not None:
            parameters = replace(
                parameters,
                lock_thresholds=_node_thresholds(lock_thresholds),
            )
        if mst_unlock is not None:
            if parameters.mst_unlock is None:
                raise CloudCodecError("mstUnlock is not present in the source HEX")
            parameters = replace(
                parameters,
                mst_unlock=_node_thresholds(mst_unlock),
            )

        field_to_attribute = {
            "quickLock": "quick_lock",
            "quickUnlock": "quick_unlock",
            "mstThanSlave": "mst_than_slave",
            "bevelAngle": "bevel_angle",
        }
        for field_name, updates in (strategy_updates or {}).items():
            try:
                attribute = field_to_attribute[field_name]
            except KeyError as error:
                raise CloudCodecError(f"unknown strategy: {field_name}") from error
            current = getattr(parameters, attribute)
            if current is None:
                raise CloudCodecError(f"{field_name} is not present in the source HEX")
            merged = dict(current)
            for key, value in updates.items():
                if key not in merged:
                    raise CloudCodecError(f"unknown field: {field_name}.{key}")
                _validate_nibble(f"{field_name}.{key}", value)
                merged[key] = value
            parameters = replace(parameters, **{attribute: merged})

        return decode_cloud(self.encode_hex(parameters))

    def _write_signed_list(
        self,
        output: bytearray,
        field_name: str,
        values: Sequence[int],
    ) -> None:
        locations = self._byte_fields[field_name]
        if len(values) != len(locations):
            raise CloudCodecError(
                f"{field_name} requires {len(locations)} values, got {len(values)}"
            )
        for index, value in zip(locations, values):
            output[index] = _encode_signed_byte(field_name, value)

    def _write_optional_signed_list(
        self,
        output: bytearray,
        field_name: str,
        values: Optional[Sequence[int]],
    ) -> None:
        locations = self._byte_fields.get(field_name)
        if locations is None:
            if values is not None:
                raise CloudCodecError(f"{field_name} is not present in the source HEX")
            return
        if values is None:
            raise CloudCodecError(
                f"{field_name} cannot be removed; set its values to zero to disable it"
            )
        self._write_signed_list(output, field_name, values)

    def _write_optional_nibbles(
        self,
        output: bytearray,
        field_name: str,
        values: Optional[Mapping[str, int]],
    ) -> None:
        locations = self._nibble_fields.get(field_name)
        if locations is None:
            if values is not None:
                raise CloudCodecError(f"{field_name} is not present in the source HEX")
            return
        if values is None:
            raise CloudCodecError(
                f"{field_name} cannot be removed; set its values to zero to disable it"
            )
        for name, location in locations.items():
            if name not in values:
                raise CloudCodecError(f"missing field: {field_name}.{name}")
            value = values[name]
            _validate_nibble(f"{field_name}.{name}", value)
            original = output[location.byte_index]
            output[location.byte_index] = (
                ((value << 4) | (original & 0x0F))
                if location.high
                else ((original & 0xF0) | value)
            )


def _node_thresholds(values: Sequence[int]) -> NodeThresholds:
    if len(values) != NODE_COUNT:
        raise CloudCodecError(f"node thresholds require {NODE_COUNT} values")
    result = tuple(int(value) for value in values)
    for value in result:
        _encode_signed_byte("threshold", value)
    return result  # type: ignore[return-value]


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value


def _encode_signed_byte(name: str, value: int) -> int:
    if not isinstance(value, int) or not -128 <= value <= 127:
        raise CloudCodecError(f"{name} value must fit in a signed byte: {value}")
    return value & 0xFF


def _validate_nibble(name: str, value: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0x0F:
        raise CloudCodecError(f"{name} must be between 0 and 15")


def _nibble_values(
    data: bytes,
    start: int,
    length: int,
    fields: Sequence[str],
) -> Tuple[Dict[str, int], Dict[str, NibbleLocation]]:
    values: Dict[str, int] = {}
    locations: Dict[str, NibbleLocation] = {}
    field_index = 0
    for byte_index in range(start, start + length):
        for high in (True, False):
            if field_index >= len(fields):
                return values, locations
            name = fields[field_index]
            values[name] = (
                (data[byte_index] >> 4) & 0x0F
                if high
                else data[byte_index] & 0x0F
            )
            locations[name] = NibbleLocation(byte_index, high)
            field_index += 1
    return values, locations


def decode_cloud(hex_text: str) -> CloudDocument:
    compact = "".join(hex_text.split())
    if len(compact) % 2:
        raise CloudCodecError("HEX must contain an even number of characters")
    try:
        data = bytes.fromhex(compact)
    except ValueError as error:
        raise CloudCodecError(f"invalid HEX: {error}") from error
    if len(data) < 11:
        raise CloudCodecError("cloud data is shorter than the threshold block")

    byte_fields: Dict[str, Tuple[int, ...]] = {
        "unlock_thresholds": tuple(range(1, 6)),
        "lock_thresholds": tuple(range(6, 11)),
    }
    nibble_fields: Dict[str, Dict[str, NibbleLocation]] = {}
    mst_unlock = None
    quick_lock = None
    quick_unlock = None
    mst_than_slave = None
    bevel_angle = None

    outer_index = BASE_LEN + 1
    while outer_index + 2 <= len(data):
        high_tag = data[outer_index]
        high_length = data[outer_index + 1]
        high_payload_start = outer_index + 2
        high_end = high_payload_start + high_length
        if high_end > len(data):
            raise CloudCodecError("outer TLV length exceeds cloud data")
        if high_tag != 0:
            outer_index = high_end
            continue

        parent_index = high_payload_start
        while parent_index < high_end:
            parent_head = data[parent_index]
            parent_tag = parent_head & 0x07
            parent_length = (parent_head >> 3) & 0x1F
            parent_payload_start = parent_index + 1
            parent_end = parent_payload_start + parent_length
            if parent_end > high_end:
                raise CloudCodecError("parent TLV length exceeds outer TLV")

            child_index = parent_payload_start
            while child_index < parent_end:
                child_head = data[child_index]
                child_tag = child_head & 0x07
                child_length = (child_head >> 3) & 0x1F
                child_start = child_index + 1
                child_end = child_start + child_length
                if child_end > parent_end:
                    raise CloudCodecError("child TLV length exceeds parent TLV")

                if parent_tag == 0 and child_tag == 1:
                    if child_length < NODE_COUNT:
                        raise CloudCodecError("mstUnlock must contain five bytes")
                    locations = tuple(range(child_start, child_start + NODE_COUNT))
                    byte_fields["mst_unlock"] = locations
                    mst_unlock = tuple(_signed(data[index]) for index in locations)
                elif parent_tag == 0 and child_tag == 3:
                    quick_lock, locations = _nibble_values(
                        data,
                        child_start,
                        child_length,
                        QUICK_LOCK_FIELDS,
                    )
                    nibble_fields["quick_lock"] = locations
                elif parent_tag == 0 and child_tag == 4:
                    quick_unlock, locations = _nibble_values(
                        data,
                        child_start,
                        child_length,
                        QUICK_UNLOCK_FIELDS,
                    )
                    nibble_fields["quick_unlock"] = locations
                elif parent_tag == 0 and child_tag == 5:
                    mst_than_slave, locations = _nibble_values(
                        data,
                        child_start,
                        child_length,
                        MST_THAN_SLAVE_FIELDS,
                    )
                    nibble_fields["mst_than_slave"] = locations
                elif parent_tag == 1 and child_tag == 3:
                    bevel_angle, locations = _nibble_values(
                        data,
                        child_start,
                        child_length,
                        BEVEL_ANGLE_FIELDS,
                    )
                    nibble_fields["bevel_angle"] = locations
                child_index = child_end
            parent_index = parent_end
        break

    parameters = CloudParameters(
        unlock_thresholds=_node_thresholds([_signed(value) for value in data[1:6]]),
        lock_thresholds=_node_thresholds([_signed(value) for value in data[6:11]]),
        mst_unlock=(
            None if mst_unlock is None else _node_thresholds(mst_unlock)
        ),
        quick_lock=quick_lock,
        quick_unlock=quick_unlock,
        mst_than_slave=mst_than_slave,
        bevel_angle=bevel_angle,
    )
    return CloudDocument(data, parameters, byte_fields, nibble_fields)
