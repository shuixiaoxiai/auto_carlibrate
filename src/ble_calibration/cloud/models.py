"""Cloud parameter value models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

NodeThresholds = Tuple[int, int, int, int, int]


@dataclass(frozen=True)
class CloudParameters:
    unlock_thresholds: NodeThresholds
    lock_thresholds: NodeThresholds
    mst_unlock: Optional[NodeThresholds] = None
    quick_lock: Optional[Mapping[str, int]] = None
    quick_unlock: Optional[Mapping[str, int]] = None
    mst_than_slave: Optional[Mapping[str, int]] = None
    bevel_angle: Optional[Mapping[str, int]] = None

    def to_legacy_dict(self) -> Dict[str, Any]:
        return {
            "bleUnlockThred": list(self.unlock_thresholds),
            "bleLockThred": list(self.lock_thresholds),
            "mstUnlock": None if self.mst_unlock is None else list(self.mst_unlock),
            "quickLock": None if self.quick_lock is None else dict(self.quick_lock),
            "quickUnlock": (
                None if self.quick_unlock is None else dict(self.quick_unlock)
            ),
            "mstThanSlave": (
                None if self.mst_than_slave is None else dict(self.mst_than_slave)
            ),
            "bevelAngle": (
                None if self.bevel_angle is None else dict(self.bevel_angle)
            ),
        }
