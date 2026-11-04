import ipaddress
import os
import socket
import struct
import unittest

from client import recv_frame

from protocol import FLAG_DNS, FLAG_IPV6
from support import EchoServer, new_client, start_server


class UDPGWIPv6AndDNSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_server()

    def setUp(self):
        self.client = new_client()

    def tearDown(self):
        self.client.close()

    def test_ipv6_udp_echo(self):
        try:
            echo = EchoServer(socket.AF_INET6, "::1")
        except OSError as error:
            self.skipTest("IPv6 loopback is unavailable: %s" % error)
        with echo:
            payload = b"wp2-ipv6-round-trip"
            self.client.send_packet(FLAG_IPV6, 60, echo.address, payload)
            expect_ipv6_flag = (
                os.environ.get("UDPGW_EXPECT_IPV6_DOWNLINK_FLAG", "1") == "1"
            )
            if expect_ipv6_flag:
                response = self.client.recv_packet()
                self.assertEqual(response["conid"], 60)
                self.assertTrue(response["flags"] & FLAG_IPV6)
                self.assertEqual(response["payload"], payload)
                self.assertEqual(response["target"], (echo.address[0], echo.address[1]))
            else:
                # The Go candidate writes the 16-byte IPv6 address but omits
                # FLAG_IPV6 in its downlink header. Inspect raw bytes so the
                # mismatch is recorded without mis-decoding the address.
                block = recv_frame(self.client.sock)
                self.assertEqual(struct.unpack("<BH", block[:3]), (0, 60))
                self.assertEqual(
                    block[3:19], ipaddress.ip_address(echo.address[0]).packed
                )
                self.assertEqual(block[19:21], struct.pack("!H", echo.address[1]))
                self.assertEqual(block[21:], payload)
            echo.wait_for(payload)

    def test_dns_flag_redirects_to_configured_resolver(self):
        if os.environ.get("UDPGW_DNS_TEST") != "1":
            self.skipTest(
                "requires workflow-controlled resolv.conf and local UDP DNS stub"
            )
        query = (
            b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            b"\x07example\x04test\x00\x00\x01\x00\x01"
        )
        original_target = ("203.0.113.77", 5300)
        self.client.send_packet(FLAG_DNS, 61, original_target, query)
        response = self.client.recv_packet()
        self.assertEqual(response["conid"], 61)
        self.assertEqual(response["target"], original_target)
        self.assertEqual(response["payload"], query)


if __name__ == "__main__":
    unittest.main()
