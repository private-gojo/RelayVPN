# WP5 — BadVPN UDPGW single-instance CPU profile

This work package measures the existing standalone \`badvpn-udpgw\` process without changing production C code. It determines whether one event-loop instance becomes CPU-bound before considering any multi-instance architecture.

## Method

- Build the baseline UDPGW as an optimized Release binary and compile a small C UDP echo helper.
- Open one TCP client and establish 64, 256, then 1,024 independent UDP mappings to the local UDP echo helper.
- For each mapping level, run offered-rate steps of 2k, 5k, 10k, 20k, 40k, 60k, 80k, and 100k datagrams/second for three seconds each.
- Record UDPGW process CPU time from \`/proc/<pid>/stat\`, normalized to one CPU core, plus RSS, descriptor count, thread count, sent datagrams, TCP echoes, and achieved packet rates.
- Record the C echo helper CPU separately and the Python harness CPU separately. The harness metric covers only packet generation and TCP response parsing; the C echo endpoint is measured as its own process.

The test uses the existing PacketProto framing and UDPGW wire header. It does not use or modify TUN, Hev, tun2socks, protocol headers, or the production UDPGW implementation.

## Interpretation

A likely single-instance CPU bottleneck requires all of the following evidence: UDPGW approaches one full core, achieved throughput stops scaling as offered rate rises, and neither the C echo endpoint nor the Python harness is CPU-saturated. If UDPGW CPU remains low while throughput stalls, investigate socket buffers, the host, or another endpoint limit instead. Packet loss and incomplete echoes are reported as measurements; they are not silently converted into throughput.

This is a loopback microbenchmark, not a carrier-network benchmark. It is intended for relative CPU/packet-cost evidence on the CI runner. Repeat on the target VPS/container before making a deployment capacity claim.

## Limits

- One UDPGW instance and one TCP client are measured at a time.
- Mapping levels top out at 1,024; this work package profiles packet-processing cost, not 10k–100k mapping capacity.
- The 16-bit connection ID and existing wire contract remain unchanged.
- No multi-instance implementation or production C modification is included.
