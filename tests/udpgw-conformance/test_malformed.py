import os
import unittest

from client import UDPGWClient
from protocol import (
    FLAG_IPV6,
    UDPGW_MTU,
    UDP_MTU,
    pack_frame,
    pack_udpgw,
)
from support import EchoServer, new_client, start_server


class UDPGWMalformedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_server()

    def setUp(self):
        self.client = new_client()

    def tearDown(self):
        self.client.close()

    def _send_malformed_then_verify_session(self, malformed):
        self.client.send_raw_payload(malformed)
        self.assertFalse(self.client.has_data(timeout=0.1))
        with EchoServer() as echo:
            payload = b"wp2-valid-after-malformed"
            self.client.send_packet(0, 70, echo.address, payload)
            response = self.client.recv_packet()
            self.assertEqual(response["conid"], 70)
            self.assertEqual(response["payload"], payload)
            echo.wait_for(payload)

    def test_short_udpgw_header_is_dropped(self):
        self._send_malformed_then_verify_session(b"\x00")

    def test_missing_ipv4_address_is_dropped(self):
        self._send_malformed_then_verify_session(b"\x00\x71\x00")

    def test_missing_ipv6_address_is_dropped(self):
        self._send_malformed_then_verify_session(bytes((FLAG_IPV6, 0x72, 0x00)))

    def test_udp_payload_over_configured_mtu_is_dropped(self):
        malformed = pack_udpgw(
            0,
            0x73,
            ("127.0.0.1", 9999),
            b"x" * (UDP_MTU + 1),
        )
        self._send_malformed_then_verify_session(malformed)

    def test_packetproto_frame_over_decoder_mtu_behavior(self):
        self.assertEqual(UDPGW_MTU, UDP_MTU + 3 + 18)
        oversized = b"x" * (UDPGW_MTU + 1)
        self.client.send_raw_frame(pack_frame(oversized))

        expect_disconnect = os.environ.get(
            "UDPGW_EXPECT_OVERSIZE_FRAME_DISCONNECT", "1"
        ) == "1"
        if expect_disconnect:
            self.assertTrue(
                self.client.wait_closed(timeout=3.0),
                "oversized PacketProto frame should close the client connection",
            )
            return

        # Rust currently accepts the u16-sized frame, rejects its UDP payload,
        # and keeps the TCP client alive. Verify the stream remains usable.
        with EchoServer() as echo:
            payload = b"wp7-rust-valid-after-oversized-frame"
            self.client.send_packet(0, 0x74, echo.address, payload)
            response = self.client.recv_packet()
            self.assertEqual(response["conid"], 0x74)
            self.assertEqual(response["payload"], payload)
            echo.wait_for(payload)


if __name__ == "__main__":
    unittest.main()
