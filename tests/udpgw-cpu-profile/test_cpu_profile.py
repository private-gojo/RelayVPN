import struct
import unittest

from cpu_profile import FRAME, FrameCounter, make_packet


class FrameCounterTests(unittest.TestCase):
    def test_fragmented_packetproto_frame(self):
        body = b"\x00\x07\x00\x7f\x00\x00\x01\x1c\x84WP5\x01\x00\x00\x00\x01"
        frame = FRAME.pack(len(body)) + body
        counter = FrameCounter()
        counter.feed(frame[:5])
        self.assertEqual(counter.count(1), 0)
        counter.feed(frame[5:])
        self.assertEqual(counter.count(1), 1)
        self.assertEqual(counter.snapshot()[2], 0)

    def test_concatenated_frames(self):
        body = b"\x00\x01\x00\x7f\x00\x00\x01\x1c\x84WP5\x02\x00\x00\x00\x01"
        frame = FRAME.pack(len(body)) + body
        counter = FrameCounter()
        counter.feed(frame + frame)
        self.assertEqual(counter.count(2), 2)

    def test_malformed_short_body_is_counted(self):
        counter = FrameCounter()
        counter.feed(FRAME.pack(3) + b"bad")
        self.assertEqual(counter.snapshot()[1], 1)

    def test_make_packet_preserves_little_endian_conid_and_network_port(self):
        frame = make_packet(0x1234, ("127.0.0.1", 7300), b"payload")
        length = FRAME.unpack_from(frame)[0]
        self.assertEqual(length, len(frame) - FRAME.size)
        self.assertEqual(frame[2:5], struct.pack("<BH", 0, 0x1234))
        self.assertEqual(frame[5:9], b"\x7f\x00\x00\x01")
        self.assertEqual(frame[9:11], struct.pack("!H", 7300))


if __name__ == "__main__":
    unittest.main()
