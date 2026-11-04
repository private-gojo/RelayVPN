#!/usr/bin/env python3
import importlib.util
import os
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "memory_ramp.py")
SPEC = importlib.util.spec_from_file_location("memory_ramp", SCRIPT)
memory_ramp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(memory_ramp)


class MemoryRampUnitTests(unittest.TestCase):
    def test_ipv4_header_and_conid_little_endian(self):
        wire = memory_ramp.packet(0x1234, ("127.0.0.1", 53), b"x")
        self.assertEqual(wire[:3], b"\x00\x34\x12")
        self.assertEqual(wire[3:7], b"\x7f\x00\x00\x01")
        self.assertEqual(wire[7:9], b"\x00\x35")
        self.assertEqual(wire[9:], b"x")

    def test_packetproto_little_endian_length(self):
        wire = memory_ramp.framed(b"abc")
        self.assertEqual(wire, b"\x03\x00abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
