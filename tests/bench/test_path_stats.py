#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("measure.py")
SPEC = importlib.util.spec_from_file_location("phase2e_measure", MODULE_PATH)
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


class PathDropAccountingTests(unittest.TestCase):
    def test_legacy_tun_stats_include_device_drops(self):
        stats = """=== qdisc tun0 ===
qdisc fq_codel 0: root limit 10240p
 Sent 1000 bytes 10 pkt (dropped 2, overlimits 0 requeues 0)

=== qdisc ifb0 ===

=== tun0 ingress filters ===

=== tun0 link stats ===
20: tun0: <UP> mtu 1500 qdisc fq_codel qlen 500
    link/none
    RX:  bytes packets errors dropped missed mcast
        1000      10      0       1      0      0
    TX:  bytes packets errors dropped carrier collsns
        1000      10      0       3       0       0
"""
        self.assertEqual(measure.Lab.count_path_drops(stats), 6)

    def test_ifb_device_drops_are_counted(self):
        stats = """=== qdisc tun0 ===
qdisc netem 1: root limit 32768
 Sent 1000 bytes 10 pkt (dropped 0, overlimits 0 requeues 0)

=== qdisc ifb0 ===
qdisc netem 2: root limit 32768
 Sent 1000 bytes 10 pkt (dropped 4, overlimits 0 requeues 0)

=== tun0 ingress filters ===

=== tun0 link stats ===
20: tun0: <UP> mtu 1500 qdisc netem qlen 8192
    RX:  bytes packets errors dropped missed mcast
        1000 10 0 0 0 0
    TX:  bytes packets errors dropped carrier collsns
        1000 10 0 0 0 0

=== ifb0 link stats ===
21: ifb0: <UP> mtu 1500 qdisc netem qlen 8192
    RX:  bytes packets errors dropped missed mcast
        1000 10 0 5 0 0
    TX:  bytes packets errors dropped carrier collsns
        1000 10 0 0 0 0
"""
        self.assertEqual(measure.Lab.count_path_drops(stats), 4)

    def test_missing_tun_link_counters_are_invalid(self):
        stats = """=== qdisc tun0 ===
qdisc fq_codel 0: root
 Sent 1000 bytes 10 pkt (dropped 0, overlimits 0 requeues 0)

=== qdisc ifb0 ===
"""
        self.assertIsNone(measure.Lab.count_path_drops(stats))

    def test_missing_tun_qdisc_is_invalid(self):
        stats = """=== qdisc tun0 ===

=== tun0 link stats ===
20: tun0: <UP> mtu 1500 qdisc fq_codel qlen 8192
    RX:  bytes packets errors dropped missed mcast
        1000 10 0 0 0 0
    TX:  bytes packets errors dropped carrier collsns
        1000 10 0 0 0 0
"""
        self.assertIsNone(measure.Lab.count_path_drops(stats))


if __name__ == "__main__":
    unittest.main()
