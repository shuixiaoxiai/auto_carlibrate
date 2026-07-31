"""CAN decoding, alignment, and event processing."""

from .alignment import RssiTimeAligner
from .events import RequestEdgeDetector
from .pipeline import CanFrameProcessor, FrameProcessingResult

__all__ = [
    "CanFrameProcessor",
    "FrameProcessingResult",
    "RequestEdgeDetector",
    "RssiTimeAligner",
]
