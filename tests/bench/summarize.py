#!/usr/bin/env python3
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def number(row, key):
    try:
        value = float(row.get(key, ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * p
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    mean = statistics.mean(values)
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p25": percentile(values, 0.25),
        "p75": percentile(values, 0.75),
        "min": min(values),
        "max": max(values),
        "cv": statistics.stdev(values) / mean if len(values) > 1 and mean else 0.0,
    }


def fmt(value, digits=2):
    return "—" if value is None else ("%%.%df" % digits) % value


def delta_pct(new, old):
    if old is None or old == 0 or new is None:
        return None
    return (new / old - 1.0) * 100.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    arms = manifest["arms"]
    badvpn_arms = [arm for arm in arms if arm["kind"] == "badvpn"]
    order = [arm["label"] for arm in badvpn_arms]
    by_label = {arm["label"]: arm for arm in arms}

    results = read_csv(artifact_dir / "results.csv")
    measured = [row for row in results if row["warmup"] == "0"]
    grouped = defaultdict(list)
    all_grouped = defaultdict(list)
    for row in measured:
        key = (row["arm"], int(row["rtt_ms"]), int(row["concurrency"]))
        all_grouped[key].append(row)
        if int(row["errors"]) == 0:
            grouped[key].append(row)

    lines = [
        "# Phase 2e measurement report",
        "",
        "Status: report-only; no performance threshold is enforced.",
        "",
        "## Runner and method",
        "",
        "See system-info.txt for kernel, OS image, CPU model, and logical CPU count.",
        "The harness uses 8 rounds per arm/profile; round 1 is discarded as warm-up, leaving 7 measured rounds.",
        "RTT shaping is attached to the tun0 path: netem (32,768-packet queue limit) on TUN egress plus ingress redirection to IFB with the same one-way delay. Configured RTT equals twice the per-side delay.",
        "Path validity counts TUN RX/TX drops, IFB TX drops, and qdisc drops. IFB RX rx_dropped is excluded because the IFB driver increments it for packets lacking redirect metadata.",
        "TUN and IFB netdevice transmit queues are set to 65,536 packets to keep the virtual TUN read ring from becoming the benchmark bottleneck.",
        "Outer SSH transport uses the veth link without injected delay. The HTTP endpoint is reached through the SSH dynamic SOCKS tunnel.",
        "",
        "## Throughput, CPU, and RSS",
        "",
        "| RTT | Flows | Arm | Median Mbps | p25–p75 | Min–max | CV | CPU s/GiB | RSS KiB | HWM KiB | Errors |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    resume_plan_path = artifact_dir / "resume-plan.json"
    if resume_plan_path.is_file():
        resume_plan = json.loads(resume_plan_path.read_text(encoding="utf-8"))
        lines.extend([
            "",
            "## Measurement integrity and resume",
            "",
            "Source workflow run: %s." % resume_plan["source_run_id"],
            "Complete throughput profiles reused: %d; targeted profiles rerun: %d."
            % (len(resume_plan["reused_profiles"]), len(resume_plan["rerun_profiles"])),
            "TTFB arm/RTT profiles reused: %d; rerun: %d."
            % (len(resume_plan["reused_ttfb_profiles"]), len(resume_plan["rerun_ttfb_profiles"])),
        ])
        if resume_plan["rerun_profiles"]:
            lines.append("Rerun throughput profiles: " + ", ".join(resume_plan["rerun_profiles"]) + ".")
        if resume_plan["rerun_ttfb_profiles"]:
            lines.append("Rerun TTFB profiles: " + ", ".join(resume_plan["rerun_ttfb_profiles"]) + ".")
        lines.append("source-system-info.txt records the runner used for retained measurements; system-info.txt records the runner used for reruns, calibration, and memory tests.")

    profile_stats = {}
    for rtt in (0, 10, 25, 50):
        for concurrency in (1, 8, 64, 256):
            for arm in arms:
                rows = grouped.get((arm["label"], rtt, concurrency), [])
                all_rows = all_grouped.get((arm["label"], rtt, concurrency), [])
                throughput = stats([number(row, "throughput_mbps") for row in rows])
                cpu = stats([number(row, "cpu_seconds_per_gib") for row in rows])
                rss = stats([number(row, "rss_kib") for row in rows])
                hwm = stats([number(row, "hwm_kib") for row in rows])
                errors = sum(int(row["errors"]) for row in all_rows)
                profile_stats[(arm["label"], rtt, concurrency)] = throughput
                lines.append(
                    "| %d | %d | %s | %s | %s–%s | %s–%s | %s%% | %s | %s | %s | %d |" % (
                        rtt, concurrency, arm["label"],
                        fmt(throughput["median"] if throughput else None),
                        fmt(throughput["p25"] if throughput else None),
                        fmt(throughput["p75"] if throughput else None),
                        fmt(throughput["min"] if throughput else None),
                        fmt(throughput["max"] if throughput else None),
                        fmt(throughput["cv"] * 100, 1) if throughput else "—",
                        fmt(cpu["median"] if cpu else None, 4),
                        fmt(rss["median"] if rss else None, 0),
                        fmt(hwm["median"] if hwm else None, 0),
                        errors,
                    )
                )

    lines.extend([
        "",
        "## Paired throughput deltas",
        "",
        "Deltas compare the same measured round number against BASE. Positive values mean higher throughput.",
        "Noise flag uses the larger of BASE and candidate CV; a delta smaller than 2× that CV is within noise.",
        "",
        "| RTT | Flows | Arm | vs BASE | vs Previous | Noise assessment |",
        "|---:|---:|---|---:|---:|---|",
    ])

    for rtt in (0, 10, 25, 50):
        for concurrency in (1, 8, 64, 256):
            base_rows = {
                int(row["round"]): row
                for row in grouped.get(("BASE", rtt, concurrency), [])
            }
            if not base_rows:
                continue
            previous_rows = base_rows
            for label in order[1:] + ["HEV"]:
                candidate_rows = {
                    int(row["round"]): row
                    for row in grouped.get((label, rtt, concurrency), [])
                }
                candidate_stats = profile_stats.get((label, rtt, concurrency))
                base_stats = profile_stats.get(("BASE", rtt, concurrency))
                base_deltas = []
                previous_deltas = []
                for round_number, candidate in candidate_rows.items():
                    base = base_rows.get(round_number)
                    prior = previous_rows.get(round_number)
                    if base:
                        base_deltas.append(delta_pct(
                            number(candidate, "throughput_mbps"),
                            number(base, "throughput_mbps")))
                    if prior:
                        previous_deltas.append(delta_pct(
                            number(candidate, "throughput_mbps"),
                            number(prior, "throughput_mbps")))
                base_delta = statistics.median([x for x in base_deltas if x is not None]) if base_deltas else None
                prev_delta = statistics.median([x for x in previous_deltas if x is not None]) if previous_deltas else None
                noise_cv = max(
                    base_stats["cv"] if base_stats else 0.0,
                    candidate_stats["cv"] if candidate_stats else 0.0,
                )
                assessment = (
                    "within noise (<2×CV)"
                    if base_delta is not None and abs(base_delta) < 2 * noise_cv * 100
                    else "above noise" if base_delta is not None
                    else "insufficient paired data"
                )
                lines.append("| %d | %d | %s | %s%% | %s%% | %s |" % (
                    rtt, concurrency, label,
                    fmt(base_delta, 1), fmt(prev_delta, 1), assessment
                ))
                previous_rows = candidate_rows

    ttfb_rows = read_csv(artifact_dir / "ttfb.csv")
    ttfb_groups = defaultdict(list)
    for row in ttfb_rows:
        value = number(row, "time_starttransfer_ms")
        if value is not None:
            ttfb_groups[(row["arm"], int(row["rtt_ms"]), row["mode"])].append(value)

    lines.extend([
        "",
        "## TTFB percentiles",
        "",
        "| RTT | Arm | State | Valid samples | p50 ms | p95 ms | p99 ms |",
        "|---:|---|---|---:|---:|---:|---:|",
    ])
    for rtt in (0, 10, 25, 50):
        for arm in arms:
            for mode in ("idle", "loaded"):
                values = ttfb_groups.get((arm["label"], rtt, mode), [])
                lines.append("| %d | %s | %s | %d | %s | %s | %s |" % (
                    rtt, arm["label"], mode, len(values),
                    fmt(percentile(values, 0.50)),
                    fmt(percentile(values, 0.95)),
                    fmt(percentile(values, 0.99)),
                ))

    lines.extend([
        "",
        "## Memory and connection scaling",
        "",
        "Array storage ceiling is TCP_WND + CLIENT_SOCKS_RECV_BUF_SIZE; sizeof(struct tcp_client) includes those arrays plus struct fields and alignment.",
        "MEMP_MEM_MALLOC=1 means MEMP_NUM_TCP_PCB is not a fixed pool-size limit in this build; the 1,100-connection row is an observed stress point, not a guaranteed PCB cap.",
        "",
        "| Arm | Mode | Requested | Connected | Struct bytes | Array ceiling B/conn | Peak Δ B/conn | HWM Δ B/conn | Post Δ B/conn |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    memory_rows = read_csv(artifact_dir / "memory.csv")
    for row in memory_rows:
        arm = by_label[row["arm"]]
        array_ceiling = int(arm.get("tcp_wnd", 0)) + int(arm.get("socks_recv_buf", 0))
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["arm"], row["mode"], row["requested"], row["connected"],
            arm.get("tcp_client_size", "n/a"), array_ceiling,
            row["peak_delta_bytes_per_connection"] or "—",
            row["hwm_delta_bytes_per_connection"] or "—",
            row["post_delta_bytes_per_connection"] or "—",
        ))

    lines.extend([
        "",
        "## Runner noise calibration",
        "",
    ])
    calibration = read_csv(artifact_dir / "calibration.csv")
    calibration_stats = stats([number(row, "throughput_mbps") for row in calibration if row["errors"] == "0"])
    if calibration_stats:
        lines.append(
            "Ten consecutive P2D runs on the same runner: median %.2f Mbps, p25–p75 %.2f–%.2f Mbps, CV %.2f%%, min–max %.2f–%.2f Mbps." % (
                calibration_stats["median"], calibration_stats["p25"],
                calibration_stats["p75"], calibration_stats["cv"] * 100,
                calibration_stats["min"], calibration_stats["max"],
            )
        )
        lines.append(
            "Provisional interpretation: do not claim gains smaller than 2×%.2f%% CV. This is a noise interpretation only, not a CI pass/fail threshold." % (
                calibration_stats["cv"] * 100
            )
        )
    else:
        lines.append("Calibration incomplete or all runs failed; no runner noise floor can be claimed.")

    lines.extend([
        "",
        "## Limitations and unverified items",
        "",
        "- Measurements are from the GitHub-hosted x86-64 Linux runner only; they do not represent Android arm64, Termux, Cloud Run, or real mobile networks.",
        "- The SSH transport is a local test tunnel. Carrier NAT, DPI, congestion, and Internet path behavior are not modeled.",
        "- The benchmark is a controlled comparative measurement, not a production capacity guarantee.",
        "- The actual Hev platform behavior on IPv6-disabled kernels remains unverified.",
        "- A 1,100-connection outcome is recorded as observed behavior; it does not establish a universal connection limit.",
        "- Any incomplete profile, error count, or missing artifact must be treated as an incomplete measurement, not silently excluded.",
        "",
    ])

    (artifact_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote summary.md", flush=True)


if __name__ == "__main__":
    main()
