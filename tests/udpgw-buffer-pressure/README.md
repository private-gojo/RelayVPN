# BadVPN UDPGW buffer-pressure reproduction

This harness exercises the existing UDPGW binary through its TCP wire protocol.
It does not change the C implementation, protocol headers, tun2socks, lwIP, or
Hev sources.

## Source paths under observation

- `udpgw/udpgw.h`: both per-mapping buffers are configured for one packet.
- `udpgw/udpgw.c`: `connection_send_to_udp()` emits the exact
  `out of UDP buffer` diagnostic when its writer has no packet slot.
- `udpgw/udpgw.c`: `connection_send_to_client()` emits the exact
  `out of client buffer` diagnostic when its writer has no packet slot.
- `flow/BufferWriter.c`: `BufferWriter_StartPacket()` returns false while
  the previous packet is still outstanding.
- `system/BDatagram_unix.c`: UDP sends are non-blocking; EAGAIN waits for
  writable readiness before completing the packet interface.

## Test phases

1. **Client to UDP:** one UDPGW TCP client creates one mapping, then sends
   progressively larger bursts to a local UDP collector. The collector counts
   datagrams by exact payload, allowing send/receive loss and elapsed time to
   be recorded. The UDPGW log is scanned for `out of UDP buffer`.
2. **UDP to client:** a local UDP responder sends 16,384 datagrams to one
   mapping while the TCP client deliberately does not read. The client then
   drains available TCP frames. The UDPGW log is scanned for
   `out of client buffer`.

Both phases run on IPv4 loopback. The test requests a 1,200-byte UDP MTU,
4,096-byte server TCP send buffer, 4,096-byte client TCP receive buffer,
4,092 max clients, and 4,092 max connections per client. Each pressure phase
uses one actual TCP client and one UDP mapping. These settings are a controlled
reproduction profile, not a production deployment recommendation.

## Run locally

    cmake -S . -B build-udpgw -DBUILD_NOTHING_BY_DEFAULT=ON -DBUILD_UDPGW=ON
    cmake --build build-udpgw --parallel 2 --target badvpn-udpgw
    python3 tests/udpgw-buffer-pressure/test_harness.py
    UDPGW_BIN="$PWD/build-udpgw/udpgw/badvpn-udpgw" \
      python3 tests/udpgw-buffer-pressure/run_stress.py

The harness writes JSON measurements and separate UDPGW logs into
`wp3-artifacts/`.

## Interpretation

- `REPRODUCED` means the exact corresponding log diagnostic was observed.
- `NOT_REPRODUCED` means that diagnostic did not appear in this run; it does
  not prove the condition cannot occur under a different workload or host.
- UDP receive counts are observations at the local collector. They do not
  independently distinguish kernel UDP drops from UDPGW buffer drops.
- The requested SO_SNDBUF value is recorded as requested configuration. Linux
  may clamp or internally account for socket buffers differently.
- The UDPGW process exit status is recorded, not treated as a performance
  verdict. The existing main routine returns a non-zero status on its normal
  event-loop shutdown path.

This is a loopback reproduction experiment, not a measurement of Android,
Cloud Run, a Philippine carrier, or a production Internet path.
