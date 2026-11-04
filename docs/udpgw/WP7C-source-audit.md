# WP7-C UDPGW source audit

Date: 2026-11-04

## Scope and immutable references

- RelayVPN production baseline: `388151994d791a78cc371244e05b181dedab6bb9`.
- WP7-C parent inspected: `191e9a7fcb53fc306d6e524e58c2e2b8a8a2a775`.
- Rust upstream: `bfd488a62618fc9dd026ac221b2f274a90ffe2b8` (MIT).
- The protected `main` branch remains at the frozen baseline. This work is confined to WP7-C.
- No C, protocol-header, Hev, tun2socks, or production-default changes are part of this audit.

## C reference implementation

**VERIFIED — source:** `udpgw/udpgw.c`, `udpgw/udpgw.h`.

- `main()` initializes one `BReactor` and enters one `BReactor_Exec(&ss)` loop (`udpgw.c:236-317`). The C UDPGW process therefore has one reactor loop; multi-core scaling is not implemented in this source.
- The default client disconnect timeout is 20 seconds; defaults are 3 TCP clients and 256 UDP connections per client (`udpgw/udpgw.h:43-49`).
- Per-client connection lookup uses a `BAVL`; LRU order is a linked list. Uplink and downlink both move the connection to the list tail (`udpgw.c:1319`, `1355`). This gives constant-time recency updates.
- Each UDP mapping owns a UDP datagram interface and send/receive buffering (`udpgw.c:1000-1150`). Client downlink frames are submitted through a per-client `PacketPassFairQueue` (`udpgw.c:676`).

## Rust pinned upstream

**VERIFIED — source:** `impl/rust/upstream/src/main.rs`, `impl/rust/upstream/src/udpgw.rs`.

### Runtime and concurrency

- Cargo enables Tokio `rt-multi-thread`; `Cargo.lock` pins Tokio 1.53.1. Release settings are opt-level 3, fat LTO, one codegen unit, and stripping. No allocator crate or `panic = "abort"` is configured.
- `main.rs:114` uses `#[tokio::main]`; accept loops are spawned at `main.rs:176`. Each accepted TCP client is served in its own task (`main.rs:227-235`); each UDP mapping has its own downlink task (`udpgw.rs:656-682`).
- This is task-per-client plus task-per-UDP-mapping, not thread-per-flow. Tokio's multi-thread runtime schedules runnable tasks over a worker pool. The worker count is not explicitly configured or logged by this program. Pinned Tokio derives its default from Rust's `std::thread::available_parallelism()`; this is an OS estimate, not a verified Cloud Run/cgroup quota measurement.
- C is one reactor loop; Rust has a multi-worker scheduler. Actual per-core utilization and throughput remain **NOT MEASURED**. Runtime capability alone is not a benchmark result.

### Allocation, memory, and queues

- `UPLINK_BUF` is sized to a maximum PacketProto frame (`udpgw.rs:74`), but `FrameReader::next_msg()` allocates a new body `Vec` for each frame (`udpgw.rs:244-252`).
- Every UDP mapping allocates a receive buffer of `udp_mtu` bytes (`udpgw.rs:656`). Default MTU is 65,520 bytes; raw receive-buffer capacity is approximately 655 MB at 10,000 mappings and 6.55 GB at 100,000 mappings, before task/socket/map/queue overhead. These are arithmetic capacity estimates, not measured RSS.
- Every received downlink datagram allocates a new frame `Vec` (`udpgw.rs:197-223`, `678`).
- Each client has one bounded downlink mpsc channel with capacity 256 messages (`udpgw.rs:71`, `379-380`) and one serialized TCP writer (`udpgw.rs:687-692`). The channel is bounded by message count, not bytes. A blocked producer can retain its already-built frame while awaiting channel capacity, so channel capacity alone is not a total per-client memory bound.
- TCP frames are serialized by one writer. Each mapping has one sequential UDP receive task; uplink frames are handled sequentially by its client task. Any batching or queue redesign must preserve per-client TCP order and per-conid datagram order.

### Mapping management and behavioral parity

- Each client owns a `HashMap<u16, UdpConn>` (`udpgw.rs:387`); LRU victim selection scans the map with `min_by_key` (`udpgw.rs:526`). The scan occurs on eviction, not every packet. C's linked-list recency update is O(1).
- LRU recency uses atomics on uplink and downlink (`udpgw.rs:568`, `678`). All mappings of one client share a per-client atomic tick; contention under high packet rates is a **HYPOTHESIS**, not a measured bottleneck.
- UDP sockets are unconnected and use `recv_from`, preserving full-cone reply acceptance (`udpgw.rs:600-655`). One socket is created per mapping.
- Downlink socket errors request cleanup with bounded-channel `try_send` (`udpgw.rs:665`); the send result is ignored. A full cleanup channel can defer cleanup until another lifecycle event. Frequency and impact are **NOT MEASURED**.
- Uplink UDP `send_to` errors are ignored (`udpgw.rs:568`). Error behavior versus C needs fault-injection coverage.
- The single writer calls `write_all` once per queued frame (`udpgw.rs:687-692`); no batching/vectored write is implemented. Benefit of batching is **NOT MEASURED**.
- UDP receive/send socket buffer sizes are not explicitly configured in the Rust server. Kernel defaults and actual drops must be measured before changing them.
- Rust's default per-mapping idle timeout is 120 seconds (`src/udpgw.rs:66-69`), while C has no per-mapping idle timeout. WP7A parity passes `--udp-idle-timeout 0`; the default difference remains an upstream behavior difference, not silently normalized here.
- Rust DNS resolver parsing keeps the last known good resolver on parse failure (`udpgw.rs:723`), while C clears its DNS address on resolver initialization failure (`udpgw.c:1440-1474`). This is a behavioral difference requiring a dedicated DNS test if strict resolver parity is required.

## Frame-reader edge case to add to conformance

**VERIFIED — source:** `udpgw.rs:244-252`.

The reader treats any `UnexpectedEof` while reading the 2-byte PacketProto prefix as clean EOF. A stream ending after only the first prefix byte is therefore indistinguishable from clean message-boundary EOF in this code path. Add a one-byte-prefix-then-close test and compare against the C decoder. Do not patch until expected C behavior and the WP6 regression suite are established.

## Go candidate state

**VERIFIED — RelayVPN WP7-C tree at parent SHA:**

- `engines/udpgw-go/go.mod` and `engines/udpgw-go/internal/udpgw/protocol.go` exist.
- This package contains wire codec helpers only. There is no server, listener CLI, production UDP forwarding loop, or native Go test file in this tree.
- The external `mukswilly/udpgw` implementation is GPL-3.0 and is not vendored. Do not copy it.
- Go production-engine work is deferred until C/Rust baseline measurements justify a third candidate.

## Benchmark gates before optimization

- Use a direct local TCP client-to-UDPGW-to-UDP-target path for the UDPGW-only ceiling. Run SSH-hop measurements separately; do not attribute SSH/transport limits to UDPGW.
- Define total flows across all client sessions, not flows per session. A legacy `conid` is 16-bit and scoped to one TCP client; multiple client sessions are required to exceed 65,536 aggregate flow identifiers.
- Default Rust limits are 3 TCP clients and 256 UDP mappings per client. Ramp tests must set explicit test-only limits and record them.
- Each mapping consumes one UDP socket/FD and one ephemeral source port in this implementation. Record RLIMIT_NOFILE and the ephemeral port range. A failed 100k ramp under an FD/port ceiling is a resource-limit result, not proof of scheduler capacity.
- Test generator and UDP target CPU/RSS separately. A saturated generator/echo target invalidates the server throughput ceiling.
- Report pps and Mbps separately from active-flow capacity. Record runner image/type, kernel, architecture, `nproc`, CPU quota/affinity when available, RLIMIT_NOFILE, port range, exact commits, 3-run median and spread, packet loss/errors, p50/p99 latency, CPU seconds per million packets, RSS per flow, and cleanup/drain time.

## Optimization decision

No optimization hypothesis has been measured yet. R1-R7 remain experimental. No production default has been changed.
