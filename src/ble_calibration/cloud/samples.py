"""Bundled cloud data used only by the hardware-free UI demonstration."""

from __future__ import annotations

from typing import Sequence

from .codec import CloudDocument, decode_cloud

DEMO_CLOUD_HEX = (
    "00C2C7CABEC1BEC4C6BBBE0332143C1E37282828285F50505050000000002D14141B1B0100"
    "30B029FFFF0000001A4644442B03000300001C4400000D059908535914143211000000000000"
    "002333000000221D9C9C00"
)


def demo_cloud_document(
    unlock_thresholds: Sequence[int],
    lock_thresholds: Sequence[int],
) -> CloudDocument:
    """Return an encodable demo document aligned to a Mock threshold set."""

    return decode_cloud(DEMO_CLOUD_HEX).with_updates(
        unlock_thresholds=unlock_thresholds,
        lock_thresholds=lock_thresholds,
    )
