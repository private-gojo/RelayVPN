# WP7-C UDPGW benchmark harness

This harness measures unchanged C BadVPN and the pinned Rust daemon over the same UDPGW v1
wire contract. It does not modify production code or protocol headers.

## Build the UDP target

```sh
cc -O3 -march=native -Wall -Wextra -Werror \
  tests/udpgw-bench/echo_target.c -o /tmp/udpgw-echo
```

## Single case

```sh
python3 tests/udpgw-bench/load.py \
  --engine rust --engine-bin impl/rust/upstream/target/release/badvpn-udpgw \
  --echo-bin /tmp/udpgw-echo --flows 1000 --clients 8 \
  --payload-size 512 --direction bidi --duration 10 --output result.json
```

Use the C binary with `--engine c` for the reference run. The harness uses the same listener,
client limits, UDP MTU, and client send-buffer settings. Rust additionally receives
`--udp-idle-timeout 0` to match C's no-per-mapping-idle-timeout behavior.

## Full matrix and capacity ramp

```sh
python3 tests/udpgw-bench/matrix.py --engine c \
  --engine-bin /path/to/badvpn-udpgw --echo-bin /tmp/udpgw-echo \
  --duration 5 --repeats 3 --output c-matrix.json

python3 tests/udpgw-bench/ramp.py --engine rust \
  --engine-bin /path/to/badvpn-udpgw --echo-bin /tmp/udpgw-echo \
  --clients 64 --steps 1000,5000,10000,25000,50000,75000,100000 \
  --output rust-ramp.json
```

Default matrix: payload 64/512/1200 B × flows 1/100/10k × TCP clients 1/8/64 ×
up/down/bidirectional × three runs. Cases execute serially to avoid cross-case CPU contention.

## Metrics and validity

- Uplink packets/Mbps are counted at the UDP target after forwarding; downlink packets/Mbps are
  counted by load clients after forwarding.
- Added latency is paired tunneled UDPGW RTT minus direct UDP target RTT using timestamped control
  probes. Control probes are a small extra load and are excluded from data pps.
- Server CPU seconds per million forwarded packets, average cores used, active CPU threads, peak
  per-thread CPU, and RSS delta per confirmed active flow are sampled from `/proc`. Generator and
  echo-target CPU are reported separately; saturation marks the throughput result invalid.
- Mapping setup is confirmed by one target acknowledgement per flow. Missing acknowledgements
  make a case partial or failed.
- Use `ramp.py` for the highest successful active-flow step. A single matrix case reports its
  confirmed flow count, not a universal capacity limit.
- Record the same runner/kernel, RLIMIT_NOFILE, and ephemeral port range for C/Rust comparisons.
  SSH-hop tests are separate from this direct UDPGW-only ceiling.
- Initial load generation is IPv4-only. IPv6 remains covered by WP2/WP6 conformance, not this
  first throughput harness.

Both implementations allocate one UDP socket per active mapping. FD and ephemeral-port ceilings
can stop a ramp before 100k; report those as host resource limits, not scheduler limits.
Do not change kernel limits silently.
