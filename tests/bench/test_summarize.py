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
ARMS = [
    {"label": "BASE", "kind": "badvpn", "sha": "a" * 40},
    {"label": "P2D", "kind": "badvpn", "sha": "b" * 40},
]


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_artifacts(root):
    artifacts = root / "artifacts"
    artifacts.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"arms": ARMS}), encoding="utf-8")

    result_fields = ["arm", "commit", "rtt_ms", "concurrency", "round", "warmup",
                     "bytes", "elapsed_ms", "throughput_mbps", "cpu_seconds",
                     "cpu_seconds_per_gib", "rss_kib", "hwm_kib", "errors"]
    results = []
    for arm in ARMS:
        for rtt in (0, 10, 25, 50):
            for concurrency in (1, 8, 64, 256):
                throughput = 100.0 if arm["label"] == "BASE" else (
                    120.0 if concurrency == 1 else 140.0)
                for round_no in range(1, 9):
                    results.append({
                        "arm": arm["label"], "commit": arm["sha"], "rtt_ms": rtt,
                        "concurrency": concurrency, "round": round_no,
                        "warmup": int(round_no == 1), "bytes": 1, "elapsed_ms": 1,
                        "throughput_mbps": throughput, "cpu_seconds": 0,
                        "cpu_seconds_per_gib": 0, "rss_kib": 1, "hwm_kib": 1,
                        "errors": 0,
                    })
    write_csv(artifacts / "results.csv", result_fields, results)

    ttfb_fields = ["arm", "commit", "rtt_ms", "mode", "sample",
                   "time_starttransfer_ms", "errors"]
    ttfb = []
    for arm in ARMS:
        for rtt in (0, 10, 25, 50):
            for mode in ("idle", "loaded"):
                for sample in range(1, 201):
                    ttfb.append({
                        "arm": arm["label"], "commit": arm["sha"], "rtt_ms": rtt,
                        "mode": mode, "sample": sample,
                        "time_starttransfer_ms": 1.0, "errors": 0,
                    })
    write_csv(artifacts / "ttfb.csv", ttfb_fields, ttfb)

    memory_fields = ["arm", "commit", "mode", "requested", "connected", "client_errors",
                     "peak_delta_bytes_per_connection", "hwm_delta_bytes_per_connection",
                     "post_delta_bytes_per_connection"]
    memory = []
    for arm in ARMS:
        for count in (16, 64, 256, 512, 1024):
            for mode in ("idle", "stalled"):
                memory.append({"arm": arm["label"], "commit": arm["sha"], "mode": mode,
                               "requested": count, "connected": count, "client_errors": 0})
        memory.append({"arm": arm["label"], "commit": arm["sha"], "mode": "idle",
                       "requested": 1100, "connected": 1100, "client_errors": 0})
    write_csv(artifacts / "memory.csv", memory_fields, memory)

    calibration_fields = ["arm", "commit", "round", "bytes", "elapsed_ms",
                          "throughput_mbps", "cpu_seconds", "cpu_seconds_per_gib",
                          "rss_kib", "hwm_kib", "errors"]
    calibration = []
    for i, value in enumerate((90, 110, 90, 110), 1):
        calibration.append({"arm": "P2D", "commit": ARMS[1]["sha"], "round": i,
                            "bytes": 1, "elapsed_ms": 1, "throughput_mbps": value,
                            "cpu_seconds": 0, "cpu_seconds_per_gib": 0,
                            "rss_kib": 1, "hwm_kib": 1, "errors": 0})
    write_csv(artifacts / "calibration.csv", calibration_fields, calibration)
    return artifacts, manifest


class SummaryReportTests(unittest.TestCase):
    def run_summarizer(self, artifacts, manifest):
        return subprocess.run(
            [sys.executable, str(SUMMARIZER), "--manifest", str(manifest),
             "--artifact-dir", str(artifacts)],
            check=False, capture_output=True, text=True,
        )

    def test_complete_report_and_calibrated_noise_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            result = self.run_summarizer(artifacts, manifest)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = (artifacts / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Status: COMPLETE", report)
            self.assertIn("conservative calibrated floor of 23.10%", report)
            paired = report.split("## Paired throughput deltas", 1)[1].split(
                "## TTFB percentiles", 1)[0]
            self.assertIn("| 0 | 1 | P2D | 20.0% |", paired)
            self.assertIn("within calibrated noise", paired)
            self.assertIn("| 0 | 8 | P2D | 40.0% |", paired)
            self.assertIn("above calibrated noise floor", paired)

    def test_commit_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            rows = list(csv.DictReader((artifacts / "results.csv").open(
                newline="", encoding="utf-8")))
            rows[0]["commit"] = "c" * 40
            write_csv(artifacts / "results.csv", list(rows[0]), rows)
            result = self.run_summarizer(artifacts, manifest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("commit SHA mismatch", result.stderr)

    def test_missing_ttfb_sample_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            rows = list(csv.DictReader((artifacts / "ttfb.csv").open(
                newline="", encoding="utf-8")))
            write_csv(artifacts / "ttfb.csv", list(rows[0]), rows[1:])
            result = self.run_summarizer(artifacts, manifest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected 200 TTFB rows", result.stderr)

    def test_memory_commit_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            rows = list(csv.DictReader((artifacts / "memory.csv").open(
                newline="", encoding="utf-8")))
            rows[0]["commit"] = "c" * 40
            write_csv(artifacts / "memory.csv", list(rows[0]), rows)
            result = self.run_summarizer(artifacts, manifest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("commit SHA mismatch", result.stderr)

    def test_missing_memory_profile_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            rows = list(csv.DictReader((artifacts / "memory.csv").open(
                newline="", encoding="utf-8")))
            write_csv(artifacts / "memory.csv", list(rows[0]), rows[1:])
            result = self.run_summarizer(artifacts, manifest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected exactly one memory record", result.stderr)


    def test_incomplete_memory_connections_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts, manifest = make_artifacts(Path(temp))
            rows = list(csv.DictReader((artifacts / "memory.csv").open(
                newline="", encoding="utf-8")))
            rows[0]["connected"] = 15
            rows[0]["client_errors"] = 1
            write_csv(artifacts / "memory.csv", list(rows[0]), rows)
            result = self.run_summarizer(artifacts, manifest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("incomplete client establishment", result.stderr)


if __name__ == "__main__":
    unittest.main()
