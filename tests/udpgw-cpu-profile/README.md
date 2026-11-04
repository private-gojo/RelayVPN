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
