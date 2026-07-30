import unittest

from tools.can_protocol import (
    CANID_LOCKREQ,
    CANID_MASTER,
    CANID_NODEAB,
    CANID_NODECD,
    decode_frame,
    make_lock_request_payload,
    make_master_payload,
    make_node_ab_payload,
    make_node_cd_payload,
)


class CanProtocolTests(unittest.TestCase):
    def test_rssi_frames_round_trip(self) -> None:
        self.assertEqual(
            decode_frame(CANID_MASTER, make_master_payload(-71)),
            {"master": -71},
        )
        self.assertEqual(
            decode_frame(CANID_NODEAB, make_node_ab_payload(-72, -83)),
            {"front": -72, "rear": -83},
        )
        self.assertEqual(
            decode_frame(CANID_NODECD, make_node_cd_payload(-69, -88)),
            {"left": -69, "right": -88},
        )

    def test_lock_request_round_trip(self) -> None:
        self.assertEqual(
            decode_frame(CANID_LOCKREQ, make_lock_request_payload(1)),
            {"lock_req": 1},
        )
        self.assertEqual(
            decode_frame(CANID_LOCKREQ, make_lock_request_payload(2)),
            {"lock_req": 2},
        )

    def test_irrelevant_or_short_frames_are_ignored(self) -> None:
        self.assertEqual(decode_frame(0x123, bytes(64)), {})
        self.assertEqual(decode_frame(CANID_MASTER, bytes(4)), {})


if __name__ == "__main__":
    unittest.main()
