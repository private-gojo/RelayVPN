#!/usr/bin/env python3
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

METRICS = ("up_pps", "down_pps", "total_pps", "up_mbps", "down_mbps",
           "server_cpu_seconds_per_million_packets", "server_rss_delta_bytes_per_flow",
           "p50_added_us", "p99_added_us")


def run_one(base, case, repeat, outdir):
    output = outdir / f"run-{repeat}.json"
    command = base + ["--flows", str(case["flows"]), "--clients", str(case["clients"]),
                      "--payload-size", str(case["payload_size"]), "--direction", case["direction"],
                      "--duration", str(case["duration"]), "--output", str(output)]
    completed = subprocess.run(command, text=True, capture_output=True)
    record = None
    if output.exists():
        try:
            record = json.loads(output.read_text())
        except json.JSONDecodeError:
            pass
    if record is None:
        record = {"status": "runner_error", "case": case, "runner_exit": completed.returncode,
                  "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:]}
    record["repeat"] = repeat
    record["runner_exit"] = completed.returncode
    return record


def summarize(records):
    summary = {}
    for metric in METRICS:
        values = [x.get("metrics", {}).get(metric) for x in records
                  if isinstance(x.get("metrics", {}).get(metric), (int, float))]
        summary[metric] = ({"median": statistics.median(values), "min": min(values),
                            "max": max(values), "spread": max(values) - min(values)}
                           if values else None)
    return summary


def main():
    p = argparse.ArgumentParser(description="Run the sequential C/Rust UDPGW benchmark matrix")
    p.add_argument("--engine", choices=("c", "rust"), required=True)
    p.add_argument("--engine-bin", required=True)
    p.add_argument("--echo-bin", required=True)
    p.add_argument("--flows", default="1,100,10000")
    p.add_argument("--clients", default="1,8,64")
    p.add_argument("--payload-sizes", default="64,512,1200")
    p.add_argument("--directions", default="up,down,bidi")
    p.add_argument("--duration", type=float, default=5.0)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    base = [sys.executable, str(Path(__file__).with_name("load.py")), "--engine", a.engine,
            "--engine-bin", a.engine_bin, "--echo-bin", a.echo_bin, "--workers", str(a.workers)]
    work = Path(tempfile.mkdtemp(prefix="udpgw-matrix-"))
    results, cases = [], 0
    for flows in map(int, a.flows.split(",")):
        for clients in map(int, a.clients.split(",")):
            if flows > clients * 65536:
                continue
            for size in map(int, a.payload_sizes.split(",")):
                for direction in a.directions.split(","):
                    case = {"flows": flows, "clients": clients, "payload_size": size,
                            "direction": direction, "duration": a.duration}
                    records = []
                    for repeat in range(1, a.repeats + 1):
                        item = run_one(base, case, repeat, work)
                        records.append(item)
                        results.append(item)
                    results.append({"status": "summary", "engine": a.engine, "case": case,
                                    "repeats": a.repeats, "metrics": summarize(records)})
                    cases += 1
    report = {"engine": a.engine, "matrix_cases": cases, "repeats_per_case": a.repeats,
              "results": results}
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    failed = [x for x in results if x.get("status") not in ("ok", "summary")]
    print(json.dumps({"engine": a.engine, "cases": cases,
                      "runs": sum("repeat" in x for x in results),
                      "failed_runs": len(failed), "report": a.output}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
