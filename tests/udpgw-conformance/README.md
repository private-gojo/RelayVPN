# BadVPN UDPGW conformance tests

This suite is an implementation-independent TCP client for the existing BadVPN
PacketProto and UDPGW wire formats. It does not include or modify the C protocol
headers, UDPGW implementation, tun2socks, lwIP, or Hev sources.

## Run

Build the existing UDPGW target, then run:

    UDPGW_BIN=/absolute/path/to/badvpn-udpgw \
      python3 -m unittest discover \
      -s tests/udpgw-conformance -p 'test_*.py' -v

The harness starts its own UDPGW process with a 4096-byte UDP MTU and a
two-mapping per-client limit. Each integration test opens a fresh TCP client.

## Coverage

- PacketProto little-endian length vectors, fragmented input, concatenated frames.
- UDPGW IPv4 and IPv6 address vectors.
- IPv4 and IPv6 UDP echo, KEEPALIVE, REBIND, remote-address replacement, and LRU.
- Short UDPGW headers, missing addresses, oversized UDP datagrams, and
  oversized PacketProto frames.
- DNS redirection to the first IPv4 nameserver from the UDPGW process resolver
  configuration. This test is enabled only when UDPGW_DNS_TEST=1.

## Deterministic DNS test

The CI workflow temporarily configures the runner's resolver to
127.0.0.1, starts dns_stub.py on UDP port 53, and enables UDPGW_DNS_TEST=1.
The stub echoes UDP payloads; it validates transparent DNS forwarding and
does not validate DNS recursion or DNS record correctness. The workflow restores
the original resolver configuration on exit.

## Notes

- IPv6 is reported as SKIP only when the runner cannot bind an IPv6 loopback
  UDP socket. When IPv6 is available but UDPGW forwarding fails, the test fails.
- LRU and REBIND tests observe the source UDP port to verify that an old mapping
  socket was replaced.
- Parser-level uint16 maximum vectors are unit tests. Runtime PacketProto
  acceptance is bounded by the UDPGW decoder MTU configured for the test.
