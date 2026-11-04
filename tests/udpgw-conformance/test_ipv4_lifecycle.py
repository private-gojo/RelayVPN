import unittest

from protocol import (
    FLAG_KEEPALIVE,
    FLAG_REBIND,
    pack_frame,
    pack_udpgw,
)
from support import EchoServer, new_client, start_server


class UDPGWIPv4LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_server()

    def setUp(self):
        self.client = new_client()

    def tearDown(self):
        self.client.close()

    def _round_trip(self, echo, conid, payload, flags=0):
        self.client.send_packet(flags, conid, echo.address, payload)
        response = self.client.recv_packet()
        self.assertEqual(response["conid"], conid)
        self.assertEqual(response["payload"], payload)
        self.assertEqual(response["target"], (echo.address[0], echo.address[1]))
        return echo.wait_for(payload)

    def test_ipv4_udp_echo(self):
        with EchoServer() as echo:
            source = self._round_trip(echo, 1, b"wp2-ipv4-round-trip")
            self.assertEqual(len(source), 2)

    def test_fragmented_tcp_packetproto_frame(self):
        with EchoServer() as echo:
            payload = b"wp2-fragmented-stream"
            packet = pack_udpgw(0, 2, echo.address, payload)
            frame = pack_frame(packet)
            self.client.send_raw_frame(frame, chunk_sizes=[1, 1, 2, 3])
            response = self.client.recv_packet()
            self.assertEqual(response["conid"], 2)
            self.assertEqual(response["payload"], payload)
            echo.wait_for(payload)

    def test_concatenated_packetproto_frames(self):
        with EchoServer() as echo:
            first = pack_udpgw(0, 10, echo.address, b"wp2-concat-one")
            second = pack_udpgw(0, 11, echo.address, b"wp2-concat-two")
            self.client.sock.sendall(pack_frame(first) + pack_frame(second))
            responses = [self.client.recv_packet(), self.client.recv_packet()]
            received = {item["conid"]: item["payload"] for item in responses}
            self.assertEqual(received, {10: b"wp2-concat-one", 11: b"wp2-concat-two"})
            echo.wait_for(b"wp2-concat-one")
            echo.wait_for(b"wp2-concat-two")

    def test_keepalive_does_not_create_a_udp_mapping(self):
        with EchoServer() as echo:
            self.client.send_packet(FLAG_KEEPALIVE, 20)
            self.assertFalse(self.client.has_data(timeout=0.25))
            self.assertEqual(echo.records, [])
            self._round_trip(echo, 20, b"wp2-after-keepalive")

    def test_rebind_replaces_existing_udp_socket(self):
        with EchoServer() as echo:
            first_source = self._round_trip(echo, 30, b"wp2-before-rebind")
            second_source = self._round_trip(
                echo, 30, b"wp2-after-rebind", FLAG_REBIND
            )
            self.assertNotEqual(
                first_source[1],
                second_source[1],
                "REBIND should replace the mapping UDP socket",
            )

    def test_address_change_replaces_existing_mapping(self):
        with EchoServer() as first_echo, EchoServer() as second_echo:
            self._round_trip(first_echo, 40, b"wp2-old-target")
            self.client.send_packet(0, 40, second_echo.address, b"wp2-new-target")
            response = self.client.recv_packet()
            self.assertEqual(response["conid"], 40)
            self.assertEqual(response["payload"], b"wp2-new-target")
            self.assertEqual(
                response["target"],
                (second_echo.address[0], second_echo.address[1]),
            )
            second_echo.wait_for(b"wp2-new-target")

    def test_lru_evicts_least_recently_used_mapping(self):
        with EchoServer() as echo:
            source_one = self._round_trip(echo, 51, b"wp2-lru-one-first")
            source_two = self._round_trip(echo, 52, b"wp2-lru-two-first")

            self._round_trip(echo, 51, b"wp2-lru-one-touch")
            self._round_trip(echo, 53, b"wp2-lru-three-create")

            source_two_after_eviction = self._round_trip(
                echo, 52, b"wp2-lru-two-recreated"
            )
            self.assertNotEqual(
                source_two[1],
                source_two_after_eviction[1],
                "the least-recently-used mapping should have been recreated",
            )
            self.assertNotEqual(source_one[1], source_two[1])


if __name__ == "__main__":
    unittest.main()
