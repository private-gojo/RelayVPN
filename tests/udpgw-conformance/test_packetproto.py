import unittest

from protocol import (
    FLAG_IPV6,
    pack_frame,
    pack_udpgw,
    drain_frames,
    unpack_udpgw,
)


class PacketProtoGoldenVectorTests(unittest.TestCase):
    def test_packetproto_little_endian_length(self):
        self.assertEqual(pack_frame(b"ABC"), b"\x03\x00ABC")

    def test_packetproto_zero_and_maximum_lengths(self):
        self.assertEqual(pack_frame(b""), b"\x00\x00")
        self.assertEqual(len(pack_frame(b"x" * 0xFFFF)), 0x10001)
        with self.assertRaises(ValueError):
            pack_frame(b"x" * 0x10000)

    def test_fragmented_and_concatenated_frame_decoding(self):
        wire = pack_frame(b"first") + pack_frame(b"second")
        frames, remainder = drain_frames(wire[:3])
        self.assertEqual(frames, [])
        self.assertEqual(remainder, wire[:3])

        frames, remainder = drain_frames(remainder + wire[3:])
        self.assertEqual(frames, [b"first", b"second"])
        self.assertEqual(remainder, b"")

    def test_ipv4_udpgw_golden_vector(self):
        packet = pack_udpgw(0, 0x1234, ("192.0.2.1", 53), b"\xAA")
        self.assertEqual(
            packet,
            b"\x00\x34\x12\xC0\x00\x02\x01\x00\x35\xAA",
        )
        decoded = unpack_udpgw(packet)
        self.assertEqual(decoded["conid"], 0x1234)
        self.assertEqual(decoded["target"], ("192.0.2.1", 53))
        self.assertEqual(decoded["payload"], b"\xAA")

    def test_ipv6_flag_and_address_vector(self):
        packet = pack_udpgw(0, 7, ("2001:db8::1", 5353), b"v6")
        self.assertTrue(packet[0] & FLAG_IPV6)
        decoded = unpack_udpgw(packet)
        self.assertEqual(decoded["target"], ("2001:db8::1", 5353))
        self.assertEqual(decoded["payload"], b"v6")


if __name__ == "__main__":
    unittest.main()
