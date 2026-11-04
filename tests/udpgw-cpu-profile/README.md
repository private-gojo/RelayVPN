# WP5 — BadVPN UDPGW single-instance CPU profile

This work package measures the existing standalone \`badvpn-udpgw\` process without changing production C code. It determines whether one event-loop instance becomes CPU-bound before considering any multi-instance architecture.

## Method

- Build the baseline UDPGW as an optimized Release binary and compile a small C UDP echo helper.
- Open one TCP client and establish 64, 256, then 1,024 independent UDP mappings to the local UDP echo helper.
- For each mapping level, run offered-rate steps of 2k, 5k, 10k, 20k, 40k, 60k, 80k, and 100k datagrams/second for three seconds each, using 64-packet batches.
- Record UDPGW process CPU time from \`/proc/<pid>/stat\`, normalized to one CPU core. Active-load CPU is sampled when the sender finishes; total CPU is sampled after the echo drain window.
- Also record the C echo helper CPU, Python harness CPU, RSS, descriptor count, thread count, sent datagrams, TCP echoes, achieved packet rates, and exact per-trial \`out of client buffer\` / \`out of UDP buffer\` log counts.

The test uses the existing PacketProto framing and UDPGW wire header. It does not use or modify TUN, Hev, tun2socks, protocol headers, or the production UDPGW implementation.

## Interpretation

A likely single-instance CPU bottleneck requires UDPGW to approach one full core while achieved throughput stops scaling, with the echo helper and Python harness not saturated. Buffer log counts distinguish packet queue exhaustion from CPU saturation. Active-load CPU and post-drain total CPU are reported separately so queued work is not silently omitted from the measurement.

This is a loopback microbenchmark, not a carrier-network benchmark. It is intended for relative CPU/packet-cost evidence on the CI runner. Repeat on the target VPS/container before making a deployment capacity claim.

## Limits

- One UDPGW instance and one TCP client are measured at a time.
- Mapping levels top out at 1,024; this work package profiles packet-processing cost, not 10k–100k mapping capacity.
- The 16-bit connection ID and existing wire contract remain unchanged.
- No multi-instance implementation or production C modification is included.


## Initial CI evidence

Run #9 (commit \`9e7d3ff906caf6e779081afe876a5bb3d12d903a\`, GitHub Actions run \`41832095201\`) completed successfully on the Ubuntu CI runner. It measured 64, 256, and 1,024 mappings at eight offered rates from 2k to 100k pps, for three seconds per rate, with 64-packet batches.

| Active mappings | Offered rate | UDPGW active CPU | Echo completion | Client-buffer errors | UDP-buffer errors |
|---:|---:|---:|---:|---:|---:|
| 64 | 80k pps | 71.00% | 98.964% | 2,483 | 0 |
| 64 | 100k pps | 87.99% | 99.486% | 1,512 | 0 |
| 256 | 100k pps | 89.32% | 100% | 0 | 0 |
| 1,024 | 80k pps | 82.00% | 100% | 0 | 0 |
| 1,024 | 100k pps | 97.65% | 99.456% | 1,648 | 0 |

Across all 24 trials, the log parser counted 5,643 \`out of client buffer\` events and zero \`out of UDP buffer\` events. At 1,024 mappings and 100k pps, UDPGW reached 97.65% of one CPU core while the C echo helper used 51.33% and the Python harness used 12.00%. This indicates that the UDPGW process itself is the limiting CPU component at that point; its client-side packet buffer also rejected some replies.

At 1,024 mappings and 80k pps, the same run completed every echo with no buffer errors at 82% UDPGW CPU. Treat 80k pps as a provisional clean CI point, not a production guarantee. A separate 256-packet-batch trial produced substantially more client-buffer errors, so burst shape affects results and should remain part of capacity testing.

This profile measures packets per second, not 10k–100k simultaneous mappings. The mapping count in this test never exceeds 1,024. It does not establish a 100k-mapping capacity figure.


## Repeat confirmation

Run #10 (commit \`2ab2c342bd1209c0e99b838e30f55975927713e6\`, GitHub Actions run \`41832884103\`) repeated the same 64-packet-batch profile and also completed successfully.

| Active mappings | Offered rate | UDPGW active CPU | Echo completion | Client-buffer errors |
|---:|---:|---:|---:|---:|
| 64 | 100k pps | 88.99% | 99.957% | 106 |
| 256 | 100k pps | 88.32% | 100% | 0 |
| 1,024 | 80k pps | 74.00% | 100% | 0 |
| 1,024 | 100k pps | 95.32% | 100% | 0 |

The 1,024-mapping / 100k-pps case used 95.32% of one UDPGW CPU core in the repeat; the C echo helper used 51.33% and the Python harness 11.00%. The two 64-packet-batch runs therefore agree that this workload approaches a single-core ceiling around 100k pps at 1,024 active mappings. Client-buffer loss varied between runs (1,648 events in run #9 versus none in run #10 at that point), so the precise loss rate is not stable enough to promote as a fixed capacity number.
