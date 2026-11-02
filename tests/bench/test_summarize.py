#!/usr/bin/env python3
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARIZER = ROOT / "summarize.py"


class SummaryReportTests(unittest.TestCase):
    def test_markdown_table_and_calibrated_noise_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"arms": [
                {"label": "BASE", "kind": "badvpn"},
                {"label": "P2D", "kind": "badvpn"},
            ]}), encoding="utf-8")
            fields = ["arm", "commit", "rtt_ms", "concurrency", "round", "warmup",
                      "bytes", "elapsed_ms", "throughput_mbps", "cpu_seconds",
                      "cpu_seconds_per_gib", "rss_kib", "hwm_kib", "errors"]
            with (artifacts / "results.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for concurrency, candidate in ((1, 120.0), (8, 140.0)):
                    for arm, value in (("BASE", 100.0), ("P2D", candidate)):
                        for round_no in range(2, 9):
                            writer.writerow(dict(arm=arm, commit="test", rtt_ms=0,
                                concurrency=concurrency, round=round_no, warmup=0,
                                bytes=1, elapsed_ms=1, throughput_mbps=value,
                                cpu_seconds=0, cpu_seconds_per_gib=0, rss_kib=1,
                                hwm_kib=1, errors=0))
            (artifacts / "ttfb.csv").write_text(
                "arm,rtt_ms,mode,time_starttransfer_ms\n", encoding="utf-8")
            (artifacts / "memory.csv").write_text(
                "arm,mode,requested,connected,peak_delta_bytes_per_connection,"
                "hwm_delta_bytes_per_connection,post_delta_bytes_per_connection\n",
                encoding="utf-8")
            calibration_fields = ["arm", "commit", "round", "bytes", "elapsed_ms",
                "throughput_mbps", "cpu_seconds", "cpu_seconds_per_gib",
                "rss_kib", "hwm_kib", "errors"]
            with (artifacts / "calibration.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=calibration_fields)
                writer.writeheader()
                for i, value in enumerate((90, 110, 90, 110), 1):
                    writer.writerow(dict(arm="P2D", commit="test", round=i, bytes=1,
                        elapsed_ms=1, throughput_mbps=value, cpu_seconds=0,
                        cpu_seconds_per_gib=0, rss_kib=1, hwm_kib=1, errors=0))
            (artifacts / "resume-plan.json").write_text(json.dumps({
                "source_run_id": "123", "reused_profiles": [], "rerun_profiles": [],
                "reused_ttfb_profiles": [], "rerun_ttfb_profiles": []
            }), encoding="utf-8")
            subprocess.run([sys.executable, str(SUMMARIZER), "--manifest", str(manifest),
                "--artifact-dir", str(artifacts)], check=True, capture_output=True, text=True)
            report = (artifacts / "summary.md").read_text(encoding="utf-8")
            lines = report.splitlines()
            i = lines.index("| RTT | Flows | Arm | Median Mbps | p25–p75 | Min–max | CV | CPU s/GiB | RSS KiB | HWM KiB | Errors |")
            self.assertTrue(lines[i + 1].startswith("|---:"))
            self.assertTrue(lines[i + 2].startswith("| 0 | 1 | BASE |"))
            self.assertIn("conservative calibrated floor of 23.10%", report)
            paired = report.split("## Paired throughput deltas", 1)[1].split("## TTFB percentiles", 1)[0]
            self.assertIn("| 0 | 1 | P2D | 20.0% |", paired)
            self.assertIn("within calibrated noise", paired)
            self.assertIn("| 0 | 8 | P2D | 40.0% |", paired)
            self.assertIn("above calibrated noise floor", paired)


if __name__ == "__main__":
    unittest.main()
