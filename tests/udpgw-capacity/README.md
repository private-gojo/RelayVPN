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
  --udp-mtu 1500 \
  --report ./udpgw-data-plane.json
~~~

This benchmark creates real UDPGW mappings against eight local UDP echo endpoints, verifies the echoed payload for every mapping, then sends a second datagram through every mapping to confirm persistence. It uses up to two TCP client sessions because each client's mapping ID is 16-bit. The benchmark MTU is explicitly set to 1500; this does not change the production default MTU.

## Measurement boundary

The TCP-session harness measures connected client sessions and the `--max-clients` guard. The separate active-mapping harness measures UDP mapping creation, request/response delivery, persistence, RSS, and file descriptors. Neither is a sustained throughput benchmark; throughput and latency distribution require a separate traffic-rate test.

Hev requires a SOCKS5-aware driver and its own runtime/configuration; a raw TCP session test is not a valid Hev capacity comparison.
