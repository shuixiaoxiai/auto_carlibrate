import unittest

from ble_calibration.cloud import CloudCodecError, decode_cloud
from tools.parse_cloud import parse_cloud

SAMPLE_HEX = (
    "00C2C7CABEC1BEC4C6BBBE0332143C1E37282828285F50505050000000002D14141B1B0100"
    "30B029FFFF0000001A4644442B03000300001C4400000D059908535914143211000000000000"
    "002333000000221D9C9C00"
)


class CloudCodecTests(unittest.TestCase):
    def test_decode_matches_reference_parser(self) -> None:
        document = decode_cloud(SAMPLE_HEX)
        self.assertEqual(document.parameters.to_legacy_dict(), parse_cloud(SAMPLE_HEX))

    def test_no_change_round_trip_is_byte_identical(self) -> None:
        document = decode_cloud(SAMPLE_HEX.lower())
        self.assertEqual(document.encode_hex(), SAMPLE_HEX)

    def test_threshold_and_nibble_updates_round_trip(self) -> None:
        original = decode_cloud(SAMPLE_HEX)
        updated = original.with_updates(
            unlock_thresholds=[-63, -58, -55, -67, -64],
            lock_thresholds=[-67, -61, -59, -70, -67],
            strategy_updates={
                "quickLock": {"weakFront": 2, "strongRear": 7},
                "bevelAngle": {"offsetRFR": 4},
            },
        )
        parsed = parse_cloud(updated.encode_hex())
        self.assertEqual(parsed["bleUnlockThred"], [-63, -58, -55, -67, -64])
        self.assertEqual(parsed["bleLockThred"], [-67, -61, -59, -70, -67])
        self.assertEqual(parsed["quickLock"]["weakFront"], 2)
        self.assertEqual(parsed["quickLock"]["strongRear"], 7)
        self.assertEqual(parsed["bevelAngle"]["offsetRFR"], 4)

    def test_only_located_bytes_change(self) -> None:
        original = decode_cloud(SAMPLE_HEX)
        updated = original.with_updates(
            strategy_updates={"quickLock": {"weakFront": 2}}
        )
        differences = [
            index
            for index, (before, after) in enumerate(
                zip(original.raw, updated.raw)
            )
            if before != after
        ]
        self.assertEqual(differences, [50])

    def test_missing_strategy_cannot_be_inserted(self) -> None:
        document = decode_cloud(SAMPLE_HEX[:72])
        with self.assertRaises(CloudCodecError):
            document.with_updates(
                strategy_updates={"quickLock": {"weakFront": 2}}
            )

    def test_invalid_hex_and_values_are_rejected(self) -> None:
        with self.assertRaises(CloudCodecError):
            decode_cloud("ABC")
        with self.assertRaises(CloudCodecError):
            decode_cloud("GG")
        document = decode_cloud(SAMPLE_HEX)
        with self.assertRaises(CloudCodecError):
            document.with_updates(lock_thresholds=[-60])
        with self.assertRaises(CloudCodecError):
            document.with_updates(
                strategy_updates={"quickLock": {"weakFront": 16}}
            )


if __name__ == "__main__":
    unittest.main()
