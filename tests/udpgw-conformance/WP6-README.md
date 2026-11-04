# WP6 — Live UDPGW wire golden vectors

WP2 already supplied independent Python golden vectors for PacketProto length encoding, IPv4/IPv6 UDPGW headers, and stream fragmentation/concatenation. WP6 adds a stronger live-server assertion: compare the exact bytes emitted by the unchanged C UDPGW process against the expected wire representation.

## Exact checks

- Send a known IPv4 UDPGW request through the TCP PacketProto stream.
- Read the two-byte PacketProto length prefix and response payload directly from the live TCP socket.
- Compare the complete frame byte-for-byte, including little-endian frame length, flags, little-endian connection ID, IPv4 address bytes, network-order UDP port, and binary payload.
- Repeat for IPv6, including the IPV6 flag and 16-byte address.

The tests reuse the existing WP2 black-box client and local UDP echo servers. They do not modify production C, PacketProto, UDPGW protocol headers, or runtime behavior.

## CI evidence

GitHub Actions run #1 (\`41833482635\`) completed successfully. All 21 conformance tests passed, including both live IPv4 and live IPv6 exact-response checks. Both address families were available on the runner; neither live golden-vector test was skipped.
