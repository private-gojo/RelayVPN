# Phase 2e measurement gate

## Scope

Phase 2e measures the existing BadVPN TCP ring-buffer/window variants against the baseline and records memory costs. It does not change the tuning or transport implementation. The benchmark runs on a Linux GitHub Actions runner using a local SSH SOCKS tunnel, network namespaces, a veth pair, and a local HTTP endpoint. It is not a measurement of an Android device, a Philippine mobile carrier, or a production Internet path.

## Accepted WP1/WP2 evidence

Primary run: [Run #24](https://github.com/private-gojo/RelayVPN/actions/runs/41502191114)  
Artifact: `relayvpn-phase2e-recovered`

- 768 throughput rows: 96 combinations × 8 measured rounds; all recorded errors were zero.
- 9,600 TTFB samples; no recorded errors.
- 55 memory measurements; all requested connection counts were reached, including 1,100 idle clients on each BadVPN arm.
- Ten 50 MiB calibration runs; median 1,954.099 Mbps and coefficient of variation 10.0455%. The conservative 2× noise floor is 20.10%.
- Path telemetry showed no qdisc, TUN RX/TX, or IFB TX drops in the validated run. IFB RX metadata increments were excluded because they did not represent redirected benchmark-path loss.
- The runner was an Ubuntu 24.04.5 x86-64 Azure VM with four logical CPUs.

## Interpretation

P2D showed the clearest throughput improvement at non-zero RTT, but also the largest per-client memory cost. Its TCP client structure was 263,648 bytes versus 15,416 bytes for BASE, an increase of 242.41 KiB per client. The measured RSS deltas are committed-page observations, not a guarantee that every client immediately consumes the full structure.

At RTT 0, the P2D C256 paired delta was +19.1%, below the calibrated 20.10% noise floor. The remaining P2D paired profiles were above the floor, with RTT 0 C8 only marginally above it. Small P2B0/P2B/P2C differences should not be described as proven gains when they remain within calibration noise.

The Hev TTFB outlier remains unexplained. Report it as an observed anomaly, not a confirmed engine defect or a confirmed network cause.

## Re-running the measurement

The full measurement workflow can take substantially longer than the normal CI gate. It is triggered only for relevant benchmark/harness paths or by manual dispatch. Report-only edits are validated by the short report-validation workflow. Avoid re-running the full benchmark to validate documentation or CI workflow changes.

Before changing tuning, review the paired-round results, the 20.10% calibration floor, memory scaling, and the local-runner limitations together.
