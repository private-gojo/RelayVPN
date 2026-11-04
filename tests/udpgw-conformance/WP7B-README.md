# WP7-B — C/Rust/Go UDPGW differential conformance

## Scope

Runs the independent WP2 suite and WP6 exact-wire vectors against three
pinned candidates:

- RelayVPN C baseline.
- RustBadVpnUdpgw at `bfd488a62618fc9dd026ac221b2f274a90ffe2b8`.
- Go `mukswilly/udpgw` at `5eecaa61b366d3e7185dc1a8c4b7afe8d6c7ef35`.

The Rust and Go repositories are checked out only by CI. Neither source tree
is copied into RelayVPN or modified. No production C, protocol header,
tun2socks, lwIP, or Hev files are changed.

## Go candidate facts

The Go project is an existing BadVPN UDPGW server implementation, but it is
not a feature-equivalent drop-in replacement:

- Wire framing and field endianness align with the BadVPN format.
- UDP sockets are created with `net.DialUDP`, so they are connected sockets,
  not full-cone sockets. Replies from an unexpected source are filtered.
- No per-client mapping cap, LRU eviction, or per-mapping idle timeout is
  implemented. The mapping map can grow until client disconnect or socket
  errors.
- The listener is fixed to `127.0.0.1`; only the port is configurable.
- The implementation has a 32 KiB downstream UDP payload ceiling. The
  uplink path does not enforce the same ceiling, so an oversized datagram can
  be forwarded upstream and then discarded on its return path.
- IPv6 downlink frames copy the 16-byte address but omit the required
  `FLAG_IPV6` bit, so conforming clients misparse the response.
- Malformed UDPGW blocks terminate the client stream instead of being ignored
  while keeping the client session active.
- DNS-flagged traffic is parsed as a DNS message before forwarding. The DNS
  resolver refresh interval is five seconds, unlike BadVPN's two seconds.

The repository is GPL-3.0 and its last source commit is from December 2023.
It has no GitHub Actions workflows or checked-in Go test files at the pinned
revision. CI builds and runs the external source for evaluation only. No
third-party Go code is being copied into RelayVPN. Any later product bundling
or code reuse requires a separate license review.

## Differential expectations

The test harness keeps common expectations for C and Rust, and explicitly
selects the Go candidate's current behavior through environment variables.
These are documented behavioral differences, not compatibility passes:

| Behavior | C | Rust | Go |
|---|---|---|---|
| Malformed block | Ignore, keep TCP | Ignore, keep TCP | Close TCP |
| Oversized PacketProto frame | Close TCP | Accept frame, reject body | Close TCP |
| Uplink UDP above candidate MTU | Drop | Drop | Forward upstream |
| Downlink UDP above 32 KiB | MTU-limited by configured buffers | MTU-limited by configured buffers | Drop |
| LRU at per-client cap | Yes | Yes | No cap / no LRU |
| Reply from unexpected UDP source | Accept | Accept | Filter |
| IPv6 downlink `FLAG_IPV6` bit | Set | Set | Missing |
| IPv4/IPv6 wire vectors | Expected pass | Expected pass | IPv4 pass; IPv6 flag mismatch |

Go's UDP test uses its source-defined 32,768-byte payload limit and a fixed
loopback listener on TCP port 7300. C and Rust continue to use the WP2
4,096-byte test MTU and dynamically selected listener ports.

## Run locally

For C and Rust, use the WP2/WP6 instructions. For Go, build the pinned
external repository and provide a wrapper that starts it with a JSON config
containing `UdpgwPort: 7300` and `DNSResolverIPAddress: 127.0.0.1`.
Set `UDPGW_TEST_PORT=7300`, `UDPGW_UDP_MTU_BYTES=32768`, and the Go
expectation variables shown in the CI workflow.

## Guardrails

- No Go or Rust implementation is vendored.
- No production C or wire-protocol edits.
- No main-branch commits or merges.
- Conformance results do not establish capacity or performance.
- 10k–100k mapping capacity remains a separate resource-ramp target.
