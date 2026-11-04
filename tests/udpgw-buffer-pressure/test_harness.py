#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import run_stress


class BufferPressureHarnessUnitTests(unittest.TestCase):
    def test_packetproto_little_endian_frame(self):
        self.assertEqual(run_stress.frame(b"abc"), b"\x03\x00abc")

    def test_ipv4_udpgw_wire_vector(self):
        packet = run_stress.udpgw_packet(0x1234, ("192.0.2.1", 53), b"x")
        self.assertEqual(
            packet,
            b"\x00\x34\x12\xC0\x00\x02\x01\x00\x35x",
        )

    def test_ipv6_udpgw_wire_vector(self):
        packet = run_stress.udpgw_packet(7, ("2001:db8::1", 5353), b"v6")
        self.assertEqual(packet[:3], b"\x08\x07\x00")
        self.assertEqual(len(packet), 3 + 16 + 2 + 2)

    def test_exact_error_log_counters(self):
        result = run_stress.parse_errors(
            "out of UDP buffer\nout of client buffer\nout of UDP buffer\n"
        )
        self.assertEqual(result, {
            "out_of_udp_buffer": 2,
            "out_of_client_buffer": 1,
        })

    def test_burst_parser(self):
        self.assertEqual(run_stress.parse_levels("128, 512,4096"), [128, 512, 4096])
        with self.assertRaises(Exception):
            run_stress.parse_levels("0,32")


if __name__ == "__main__":
    unittest.main(verbosity=2)
