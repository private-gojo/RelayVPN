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

The default adaptive ramp is 1k, 2k, 5k, 10k, 25k, 50k, 75k, then the requested maximum. A failed stage stops the run and records the last stable stage.

## Existing TCP listener

~~~sh
go run ./tests/udpgw-capacity/main.go \
  --target 127.0.0.1:7300 \
  --max-clients 10000 \
  --report ./capacity.json
~~~

For non-loopback targets, pass `--source-ips` with source addresses that are already configured and routed on the load-generator host. Loopback tests automatically spread connections over eight `127.0.0.x` source addresses to reduce the single-source ephemeral-port ceiling.

The harness records connection time, live-session count, generator/server RSS, peak RSS, file-descriptor counts, `RLIMIT_NOFILE`, and estimated ephemeral source-port slots. It raises the soft file-descriptor limit when the hard limit permits; otherwise it reports the host limit that blocks the requested test.

## Measurement boundary

This test measures connected TCP client sessions and the `--max-clients` guard only. It does not create UDPGW protocol mappings, exchange UDP payloads, or measure throughput/latency. Those need a separate protocol-aware data-plane test.

Hev requires a SOCKS5-aware driver and its own runtime/configuration; a raw TCP session test is not a valid Hev capacity comparison.
