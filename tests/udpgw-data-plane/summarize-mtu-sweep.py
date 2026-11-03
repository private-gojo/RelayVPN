#!/usr/bin/env python3

import json
import sys
from pathlib import Path


def load_reports(directory: Path) -> list[dict]:
    reports = []
    for path in sorted(directory.glob("mtu-*.json"), key=lambda item: int(item.stem.split("-")[1])):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reports.append({"mtu": path.stem, "failure": str(exc)})
            continue

        stages = data.get("stages") or []
        latest = stages[-1] if stages else {}
        server = latest.get("server") or {}
        reports.append({
            "mtu": data.get("udp_mtu", path.stem),
            "payload": data.get("payload_size_bytes", "-"),
            "requested": data.get("requested_mappings", "-"),
            "stable": data.get("last_stable_mappings", 0),
            "rss_mib": round(server.get("rss_kib", 0) / 1024, 2),
            "hwm_mib": round(server.get("hwm_kib", 0) / 1024, 2),
            "fds": server.get("fd_count", 0),
            "p50_us": latest.get("p50_latency_us", 0),
            "p95_us": latest.get("p95_latency_us", 0),
            "p99_us": latest.get("p99_latency_us", 0),
            "pps": round(latest.get("completion_pps", 0), 1),
            "echo_mbps": round(latest.get("echo_response_mbps", 0), 2),
            "failure": data.get("failure", ""),
        })
    return reports


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: summarize-mtu-sweep.py <report-dir>", file=sys.stderr)
        return 2

    directory = Path(sys.argv[1])
    reports = load_reports(directory)
    if not reports:
        print("No MTU sweep reports found.", file=sys.stderr)
        return 1

    headings = ["MTU", "Payload", "Stable maps", "RSS MiB", "HWM MiB", "FDs",
                "p50 us", "p95 us", "p99 us", "PPS", "Echo Mbps", "Status"]
    rows = []
    for item in reports:
        status = item.get("failure") or (
            "PASS" if item.get("stable") == item.get("requested") else "PARTIAL/NO REPORT"
        )
        rows.append([
            item.get("mtu", "-"), item.get("payload", "-"),
            item.get("stable", 0), item.get("rss_mib", "-"),
            item.get("hwm_mib", "-"), item.get("fds", "-"),
            item.get("p50_us", "-"), item.get("p95_us", "-"),
            item.get("p99_us", "-"), item.get("pps", "-"),
            item.get("echo_mbps", "-"), status,
        ])

    widths = [max(len(str(headings[i])), *(len(str(row[i])) for row in rows))
              for i in range(len(headings))]
    render = lambda row: "| " + " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)) + " |"
    lines = [
        "# UDPGW MTU Memory Sweep",
        "",
        "Server RSS/HWM include process and mapping allocations; they are measurements, not marginal per-mapping costs.",
        "Latency includes load-generator scheduling and echo processing. Echo Mbps is response-payload goodput, not total wire throughput.",
        "",
        render(headings),
        "|-" + "-|-".join("-" * width for width in widths) + "-|",
        *(render(row) for row in rows),
        "",
    ]
    summary = "\n".join(lines)
    (directory / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
