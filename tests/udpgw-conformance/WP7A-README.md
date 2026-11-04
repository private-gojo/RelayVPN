# WP7-A — C/Rust differential UDPGW conformance

## Scope

Runs the same implementation-independent WP2 tests and WP6 live byte-exact
golden vectors against:

- RelayVPN's unchanged C BadVPN UDPGW baseline.
- RustBadVpnUdpgw pinned to
  `bfd488a62618fc9dd026ac221b2f274a90ffe2b8`.

The Rust source is checked out only by CI. It is not vendored, integrated, or
modified in RelayVPN. The workflow also runs Rust formatting, Clippy, unit
tests, and a release build before the differential suite.

## Test parity

Both implementations receive the same:

- PacketProto little-endian framing vectors, fragmentation, and concatenation.
- IPv4 and IPv6 UDP echo requests.
- KEEPALIVE, REBIND, changed destinations, and LRU eviction checks.
- Malformed UDPGW headers and addresses.
- UDP payload-over-MTU check.
- Transparent DNS request through a workflow-controlled local resolver.
- Live IPv4 and IPv6 exact-wire golden vectors, including length prefix,
  flags, connection ID, address, port, and binary payload.

For this comparison, the Rust runner passes `--udp-idle-timeout 0` so the
C and Rust implementations use the same no-idle-expiry behavior during tests.
This does not validate Rust's separate 120-second default; that needs a
dedicated lifecycle test.

## Known behavioral difference captured by the test suite

The C PacketProto decoder rejects a frame larger than its configured decoder
MTU and disconnects that client. Rust's current `FrameReader` accepts any
u16-sized frame; `parse_msg` then rejects an oversized UDP payload while
keeping the TCP client open.

The malformed-input test asserts each implementation's current behavior
explicitly. The Rust test then sends a valid packet to verify the stream is
still usable. This is recorded as a compatibility difference, not silently
treated as equivalent behavior.

## Run locally

Build both binaries, then run the Python suite separately for each binary:

```sh
UDPGW_BIN=/path/to/badvpn-udpgw \
UDPGW_EXPECT_OVERSIZE_FRAME_DISCONNECT=1 \
python3 -m unittest discover -s tests/udpgw-conformance -p 'test_*.py' -v

UDPGW_BIN=/path/to/rust-badvpn-udpgw \
UDPGW_EXTRA_ARGS='--udp-idle-timeout 0' \
UDPGW_EXPECT_OVERSIZE_FRAME_DISCONNECT=0 \
python3 -m unittest discover -s tests/udpgw-conformance -p 'test_*.py' -v
```

The test harness accepts optional extra CLI arguments through
`UDPGW_EXTRA_ARGS`; this is used to set the Rust idle timeout to zero. The C
run leaves this variable unset.

## Guardrails

- No production C changes.
- No changes to `protocol/udpgw_proto.h` or `protocol/packetproto.h`.
- No tun2socks, lwIP, or Hev changes.
- No main-branch commits or merges.
- No performance or 10k–100k capacity claims are derived from this protocol
  conformance run.

## Next comparisons

Go remains the third implementation candidate alongside C and Rust. It is not
included in this stage; first establish C/Rust compatibility evidence, then
reuse the same vectors for the Go candidate before comparing resource and
throughput measurements.
