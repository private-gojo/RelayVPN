# UDPGW Capacity Harness

This Linux-only Go harness measures concurrent TCP sessions against the BadVPN UDPGW listener.

## Local UDPGW run

~~~sh
GO111MODULE=off go test -race ./tests/udpgw-capacity

go run ./tests/udpgw-capacity/main.go \
  --binary ./build/udpgw/badvpn-udpgw \
  --max-clients 100000 \
  --hold 2s \
  --report ./udpgw-capacity.json
~~~

The default adaptive ramp is 1k, 2k, 5k, 10k, 25k, 50k, 75k, then the requested maximum. A failed stage stops the run and records the last stable stage. When the harness starts its own UDPGW process, it sends valid framed UDPGW keepalives every 5 seconds by default so the server's 20-second idle-client timeout does not invalidate long capacity runs.

## Existing TCP listener

~~~sh
go run ./tests/udpgw-capacity/main.go \
  --target 127.0.0.1:7300 \
  --max-clients 10000 \
  --report ./capacity.json
~~~

For non-loopback targets, pass `--source-ips` with source addresses that are already configured and routed on the load-generator host. Loopback tests automatically spread connections over eight `127.0.0.x` source addresses to reduce the single-source ephemeral-port ceiling.

The harness records connection time, live-session count, generator/server RSS, peak RSS, file-descriptor counts, `RLIMIT_NOFILE`, estimated ephemeral source-port slots, and keepalive write counts. For an existing UDPGW listener, add `--udpgw-keepalive` to enable protocol keepalives; use `--keepalive-interval` to change the interval. It raises the soft file-descriptor limit when the hard limit permits; otherwise it reports the host limit that blocks the requested test.

## Active UDP data-plane benchmark

~~~sh
GO111MODULE=off go test -race ./tests/udpgw-data-plane

go run ./tests/udpgw-data-plane/main.go \
  --binary ./build/udpgw/badvpn-udpgw \
  --mappings 100000 \
  --udp-mtu 1500 \n  --payload-size 1200 \
  --report ./udpgw-data-plane.json
~~~

This benchmark creates real UDPGW mappings against eight local UDP echo endpoints, verifies a configurable-size payload for every mapping, then sends a second datagram through every mapping to confirm persistence. The default payload is 1200 bytes at UDP MTU 1500; the report includes per-stage p50/p95/p99/max latency, completion packets per second, and echoed-payload Mbps. Each ramp stage reports only the newly added mapping batch; latency is measured from payload construction through the echoed response, so it includes load-generator scheduling as well as tunnel/UDP processing. Mbps is echoed-response payload goodput, not total bidirectional wire throughput. It uses up to two TCP client sessions because each client's mapping ID is 16-bit. The test binds UDP sockets round-robin across four configured local IPv4 addresses using kernel-assigned ephemeral ports; each address therefore has its own ephemeral-port capacity. The optional `--one-client-per-mapping` profile creates one TCP client session for each UDP mapping and is currently capped at 10,000 sessions to stay within the single-runner source-port budget. The benchmark MTU is explicitly set to 1500; this does not change the production default MTU.

For a combined TCP-client plus UDP-mapping profile, run up to 10,000 clients with one active mapping each:
The automated `[one-client-10k]` push profile runs this combined scenario after the 100,000-session TCP capacity check.


~~~sh
go run ./tests/udpgw-data-plane/main.go \
  --binary ./build/udpgw/badvpn-udpgw \
  --mappings 10000 \
  --one-client-per-mapping \
  --udp-mtu 1500 \
  --payload-size 1200
~~~


The optional `--local-udp-bind-addr <IPv4:0>` option can be repeated up to 16 times. It binds each IPv4 UDP mapping to one configured local address with port 0, letting the kernel allocate a unique ephemeral port per address. It cannot be combined with the fixed IPv4/IPv6 local UDP port-range options; IPv6 mappings continue to use their existing binding path.

## Measurement boundary

The TCP-session harness measures connected client sessions and the `--max-clients` guard. The separate active-mapping harness measures UDP mapping creation, request/response delivery, persistence, RSS, and file descriptors. Neither is a sustained throughput benchmark; throughput and latency distribution require a separate traffic-rate test.

Hev requires a SOCKS5-aware driver and its own runtime/configuration; a raw TCP session test is not a valid Hev capacity comparison.
