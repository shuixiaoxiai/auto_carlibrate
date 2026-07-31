import unittest

from ble_calibration.can.protocol import (
    CANID_LOCKREQ,
    make_lock_request_payload,
    make_master_payload,
    make_node_ab_payload,
    make_node_cd_payload,
)
from ble_calibration.domain import CanFrame, Direction, EventType, Node
from ble_calibration.processing import CanFrameProcessor, RequestEdgeDetector, RssiTimeAligner


class ProcessingTests(unittest.TestCase):
    def test_time_aligner_builds_five_node_sample_and_stale_flags(self) -> None:
        aligner = RssiTimeAligner(sample_rate_hz=10, stale_timeout_s=0.3)
        frames = [
            CanFrame(0.00, 0x629, make_master_payload(-70)),
            CanFrame(0.01, 0x62A, make_node_ab_payload(-71, -72)),
            CanFrame(0.02, 0x62B, make_node_cd_payload(-73, -74)),
            CanFrame(0.10, 0x629, make_master_payload(-69)),
        ]
        samples = []
        for frame in frames:
            samples.extend(aligner.ingest(frame))

        at_point_one = next(sample for sample in samples if sample.relative_time == 0.1)
        self.assertEqual(at_point_one.values, (-69, -71, -72, -73, -74))
        self.assertTrue(all(not flag for flag in at_point_one.stale))
        self.assertEqual(at_point_one.value(Node.FRONT), -71)

        stale_samples = aligner.ingest(CanFrame(0.5, 0x123, b""))
        at_point_five = next(
            sample for sample in stale_samples if sample.relative_time == 0.5
        )
        self.assertTrue(all(at_point_five.stale))

    def test_out_of_order_frame_is_counted_and_does_not_emit(self) -> None:
        aligner = RssiTimeAligner()
        aligner.ingest(CanFrame(1.0, 0x629, make_master_payload(-70)))
        samples = aligner.ingest(CanFrame(0.9, 0x629, make_master_payload(-60)))
        self.assertEqual(samples, ())
        self.assertEqual(aligner.out_of_order_count, 1)

    def test_request_edges_are_deduplicated(self) -> None:
        detector = RequestEdgeDetector()
        values = [0, 2, 2, 0, 1, 1, 0]
        events = [
            detector.observe(value, float(index), Direction.FRONT)
            for index, value in enumerate(values)
        ]
        emitted = [event for event in events if event is not None]
        self.assertEqual(
            [event.event_type for event in emitted],
            [EventType.LOCK, EventType.UNLOCK],
        )

    def test_pipeline_does_not_emit_event_for_out_of_order_frame(self) -> None:
        processor = CanFrameProcessor()
        processor.process(
            CanFrame(1.0, CANID_LOCKREQ, make_lock_request_payload(0)),
            Direction.FRONT,
        )
        result = processor.process(
            CanFrame(0.9, CANID_LOCKREQ, make_lock_request_payload(2)),
            Direction.FRONT,
        )
        self.assertIsNone(result.event)


if __name__ == "__main__":
    unittest.main()
