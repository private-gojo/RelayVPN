# Phase 2e measurement gate

## Scope

Phase 2e compares the existing BadVPN TCP ring-buffer/window variants against the baseline and records throughput, CPU, latency, and memory costs. It is report-only: it does not change tuning or transport implementation, and it does not enforce a performance threshold.

The benchmark runs on a Linux GitHub Actions runner using a local SSH SOCKS tunnel, network namespaces, a veth pair, and a local HTTP endpoint. It is not a measurement of an Android device, a Philippine mobile carrier, or a production Internet path.

## Accepted WP1/WP2 evidence

Primary clean run: [Run #38](https://github.com/private-gojo/RelayVPN/actions/runs/41596621371)  
Artifact: `relayvpn-phase2e-clean`  
Measured commit: `66a068fdb58a4afc96f9bab2ff1f379c093065f8`

- **Throughput:** 768 rows across 6 arms × 4 RTTs (0/10/25/50 ms) × 4 concurrency levels (1/8/64/256) × 8 rounds. Round 1 is warm-up; 672 measured rows remain. All 768 rows report zero transfer errors.
- **TTFB:** 9,600 samples, comprising 200 idle and 200 loaded samples for each arm/RTT combination. No sample reports an error.
- **Memory:** 55 records across five BadVPN arms. All requested connection counts were reached for idle/stalled N=16/64/256/512/1024 tests and idle N=1,100 tests. No client errors were reported. N=1,100 stalled was not tested.
- **Calibration:** ten consecutive 50 MiB P2D runs on the same runner and P2D commit. Median 1,926.543 Mbps; p25–p75 1,719.74–1,947.11 Mbps; min–max 1,339.22–1,995.80 Mbps; CV 11.704% (reported rounded-up CV 11.71%). The conservative 2× calibration noise floor is **23.42%**. This is an interpretation aid, not a pass/fail threshold.
- **Runner:** Ubuntu 24.04.5 LTS, x86-64, AMD EPYC 7763, four logical CPUs. All arms, calibration, and WP2 measurements ran within this one workflow job/runner.
- **Network shaping:** RTT profiles use symmetric netem delay on TUN egress and IFB reverse path, with a 32,768-packet qdisc limit. TUN and IFB transmit queue lengths are 65,536. The recorded path-stat files show no qdisc, TUN RX/TX, or IFB TX drops. IFB RX `rx_dropped` increments are excluded from the path-loss check because the IFB driver increments that counter for packets without redirect metadata.

The manifest records the exact source commit for each BadVPN arm and Hev. Run #38 is a fresh full run; no measurements were reused from earlier workflow artifacts.

## Throughput interpretation

P2D is the only BadVPN variant with clear measured throughput gains over BASE across all non-zero RTT/concurrency profiles. Its paired deltas range from **+28.9% to +688.8%** at RTT 10/25/50 ms, all above the 23.42% calibrated noise floor.

At RTT 0, P2D is +31.1% at C=1 and +29.2% at C=64 (above the floor), while +15.3% at C=8 and +22.9% at C=256 remain within calibrated noise. P2B0, P2B, and P2C differences are within the noise floor and should not be presented as proven throughput gains.

These are controlled-runner results, not a promise of equivalent performance on Android, mobile networks, or other machines. The paired deltas compare matching measured round numbers within each profile.

## Memory and connection scaling

| Arm | `sizeof(struct tcp_client)` | Array storage ceiling |
|---|---:|---:|
| BASE | 15,416 B | 14,032 B |
| P2B0 | 15,416 B | 14,032 B |
| P2B | 15,480 B | 14,032 B |
| P2C | 23,728 B | 22,224 B |
| P2D | 263,648 B | 262,144 B |

P2D's TCP client structure is 17.1× the BASE size (242.41 KiB larger per client). This structure size is not the same as immediate RSS consumption: observed RSS/HWM deltas depend on which memory pages the test workload touches. Treat the recorded memory samples as workload-specific committed-memory observations, not as a direct guarantee that every client immediately consumes the full structure.

All five arms reached 1,100 idle clients. This is an observed stress result, not a universal capacity limit. `MEMP_MEM_MALLOC=1` means `MEMP_NUM_TCP_PCB=1024` is not a fixed preallocated PCB-pool cap in this build.

## TTFB observation

Hev shows a persistent TTFB offset: idle p50 is 208.37/224.01/256.28/304.31 ms at configured RTT 0/10/25/50 ms. The BadVPN arms track close to the expected RTT baseline. The cause of the Hev offset is unresolved; record it as an observed anomaly, not a confirmed engine defect or network cause.

## Limitations

- Measurements use a GitHub-hosted x86-64 Linux VM; Android arm64, Termux, Cloud Run, and Philippine carrier paths were not tested.
- The SSH transport is a local test tunnel. Carrier NAT, DPI, congestion, and real Internet-path behavior are not modeled.
- The benchmark is comparative evidence, not a production capacity guarantee.
- Actual Hev behavior on IPv6-disabled kernels remains unverified.
- N=1,100 stalled-client behavior was not measured.
- Any incomplete profile, non-zero transfer error, or missing artifact must be treated as an incomplete measurement, not silently excluded.

## Re-running the measurement

The full benchmark is intentionally separate from the normal CI gate and can take several hours. Run it on the dedicated `phase2e/measurement-gate` branch or by manual dispatch. Documentation-only and report-only changes should use the short CI/report validation path; do not rerun the full benchmark for those changes.

Before changing tuning, review paired throughput deltas, the 23.42% calibration floor, memory scaling, the Hev TTFB anomaly, and the local-runner limitations together.
