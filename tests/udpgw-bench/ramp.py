#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Ramp active UDPGW mappings until the first failure")
    p.add_argument("--engine", choices=("c", "rust"), required=True)
    p.add_argument("--engine-bin", required=True)
    p.add_argument("--echo-bin", required=True)
    p.add_argument("--clients", type=int, default=64)
    p.add_argument("--payload-size", type=int, default=64)
    p.add_argument("--udp-mtu", type=int, default=1200)
    p.add_argument("--duration", type=float, default=2.0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--up-rate-pps", type=int, default=10000)
    p.add_argument("--setup-timeout", type=float, default=90.0)
    p.add_argument("--steps", default="1000,5000,10000,25000,50000,60000,64000,65000,75000,100000")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    steps = [int(value) for value in a.steps.split(",")]
    if a.clients < 1 or a.workers < 1 or a.duration <= 0 or a.setup_timeout <= 0 or a.up_rate_pps < 0:
        p.error("clients/workers must be positive, duration positive, and up-rate-pps non-negative")
    if not steps or any(value < 1 for value in steps) or steps != sorted(set(steps)):
        p.error("steps must be positive, unique, and strictly increasing")
    work = Path(tempfile.mkdtemp(prefix="udpgw-ramp-"))
    base = [sys.executable, str(Path(__file__).with_name("load.py")), "--engine", a.engine,
            "--engine-bin", a.engine_bin, "--echo-bin", a.echo_bin, "--clients", str(a.clients),
            "--payload-size", str(a.payload_size), "--udp-mtu", str(a.udp_mtu),
            "--direction", "up", "--duration", str(a.duration),
            "--workers", str(a.workers), "--up-rate-pps", str(a.up_rate_pps),
            "--setup-timeout", str(a.setup_timeout)]
    results, last_ok, first_error = [], 0, None
    for requested in steps:
        output = work / f"ramp-{requested}.json"
        proc = subprocess.run(base + ["--flows", str(requested), "--output", str(output)],
                              text=True, capture_output=True)
        if output.exists():
            try:
                record = json.loads(output.read_text())
            except json.JSONDecodeError:
                record = {"status": "invalid_json"}
        else:
            record = {"status": "runner_error"}
        setup = record.get("setup", [])
        confirmed = record.get("metrics", {}).get("active_flows_confirmed")
        if confirmed is None:
            confirmed = sum(int(x.get("acked_flows", 0)) for x in setup)
        item = {"requested_flows": requested, "confirmed_flows": int(confirmed or 0),
                "status": record.get("status"), "runner_exit": proc.returncode,
                "metrics": record.get("metrics"), "setup": setup,
                "environment": record.get("environment"),
                "setup_timeout_seconds": record.get("case", {}).get("setup_timeout_seconds"),
                "setup_elapsed_seconds": max((float(x.get("setup_elapsed_seconds", 0)) for x in setup), default=0),
                "server_log_tail": record.get("server_log_tail")}
        if proc.returncode != 0:
            item["stdout_tail"] = proc.stdout[-2000:]
            item["stderr_tail"] = proc.stderr[-2000:]
        results.append(item)
        if record.get("status") == "ok" and proc.returncode == 0 and int(confirmed or 0) == requested:
            last_ok = int(confirmed)
            continue
        first_error = item
        break
    report = {"engine": a.engine, "clients": a.clients, "payload_size": a.payload_size,
              "udp_mtu": a.udp_mtu, "up_rate_pps": a.up_rate_pps,
              "setup_timeout_seconds": a.setup_timeout,
              "last_successful_active_flows": last_ok, "first_error": first_error, "results": results}
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"engine": a.engine, "last_successful_active_flows": last_ok,
                      "first_error": first_error, "report": a.output}, indent=2))
    if first_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
