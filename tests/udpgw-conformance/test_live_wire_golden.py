import ipaddress
import socket
import struct
import unittest

from client import recv_exact
from protocol import FLAG_IPV6, pack_frame
from support import EchoServer, new_client, start_server


class LiveWireGoldenVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_server()

    def setUp(self):
        self.client = new_client()

    def tearDown(self):
        self.client.close()

    def assert_live_response_matches_wire(self, echo, conid, payload, ipv6=False):
        target = echo.address[:2]
        self.client.send_packet(0, conid, target, payload)

        length_prefix = recv_exact(self.client.sock, 2)
        (body_length,) = struct.unpack("<H", length_prefix)
        response_body = recv_exact(self.client.sock, body_length)

        flags = FLAG_IPV6 if ipv6 else 0
        address = ipaddress.ip_address(target[0]).packed
        expected_body = (
            struct.pack("<BH", flags, conid)
            + address
            + struct.pack("!H", target[1])
            + payload
        )
        expected_frame = pack_frame(expected_body)
        self.assertEqual(
            length_prefix + response_body,
            expected_frame,
            "live C UDPGW response bytes differ from the exact PacketProto/UDPGW vector",
        )
        echo.wait_for(payload)

    def test_live_ipv4_response_matches_exact_wire_vector(self):
        payload = b"\x00WP6\xFF\x10ipv4-golden"
        with EchoServer(socket.AF_INET, "127.0.0.1") as echo:
            self.assert_live_response_matches_wire(
                echo, 0x1234, payload, ipv6=False
            )

    def test_live_ipv6_response_matches_exact_wire_vector(self):
        try:
            echo = EchoServer(socket.AF_INET6, "::1")
        except OSError as error:
            self.skipTest("IPv6 loopback is unavailable: %s" % error)
        with echo:
            payload = b"\x00WP6\xFF\x20ipv6-golden"
            self.assert_live_response_matches_wire(
                echo, 0xBEEF, payload, ipv6=True
            )


if __name__ == "__main__":
    unittest.main()
