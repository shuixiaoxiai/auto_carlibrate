"""Shared CAN identifiers and byte-level RSSI/request encoding helpers."""

from __future__ import annotations

from typing import Dict, Sequence

CANID_MASTER = 0x629   # IKM_MasterSt  (64B)
CANID_NODEAB = 0x62A   # IKM_NodeABSt  (64B)
CANID_NODECD = 0x62B   # IKM_NodeCDSt  (64B)
CANID_LOCKREQ = 0x55A  # BLE_Req_0x55A (32B)

RSSI_FRAME_LENGTH = 64
LOCKREQ_FRAME_LENGTH = 32


def decode_rssi_byte(value: int) -> int:
    """Decode one RSSI byte exactly as the existing vehicle capture script."""
    if not 0 <= value <= 0xFF:
        raise ValueError(f"RSSI byte out of range: {value}")
    return value - 256


def encode_rssi_byte(value: int) -> int:
    """Encode an RSSI value accepted by ``decode_rssi_byte``."""
    if not -256 <= value <= -1:
        raise ValueError(f"RSSI value out of range: {value}")
    return value + 256


def decode_frame(can_id: int, data: Sequence[int]) -> Dict[str, int]:
    """Decode one relevant CAN frame; return an empty dict for other frames."""
    decoded: Dict[str, int] = {}
    if can_id == CANID_MASTER and len(data) > 4:
        decoded["master"] = decode_rssi_byte(data[4])
    elif can_id == CANID_NODEAB and len(data) > 28:
        decoded["front"] = decode_rssi_byte(data[4])
        decoded["rear"] = decode_rssi_byte(data[28])
    elif can_id == CANID_NODECD and len(data) > 28:
        decoded["left"] = decode_rssi_byte(data[4])
        decoded["right"] = decode_rssi_byte(data[28])
    elif can_id == CANID_LOCKREQ and len(data) > 3:
        decoded["lock_req"] = (data[3] >> 4) & 0x0F
    return decoded


def make_master_payload(master: int) -> bytes:
    payload = bytearray(RSSI_FRAME_LENGTH)
    payload[4] = encode_rssi_byte(master)
    return bytes(payload)


def make_node_ab_payload(front: int, rear: int) -> bytes:
    payload = bytearray(RSSI_FRAME_LENGTH)
    payload[4] = encode_rssi_byte(front)
    payload[28] = encode_rssi_byte(rear)
    return bytes(payload)


def make_node_cd_payload(left: int, right: int) -> bytes:
    payload = bytearray(RSSI_FRAME_LENGTH)
    payload[4] = encode_rssi_byte(left)
    payload[28] = encode_rssi_byte(right)
    return bytes(payload)


def make_lock_request_payload(lock_req: int) -> bytes:
    if not 0 <= lock_req <= 0x0F:
        raise ValueError(f"lock_req out of range: {lock_req}")
    payload = bytearray(LOCKREQ_FRAME_LENGTH)
    payload[3] = lock_req << 4
    return bytes(payload)
