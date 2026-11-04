# WP4 - BadVPN UDPGW sanitizer and memory ramp

This is a standalone black-box runtime test of the existing BadVPN UDPGW.
No production C source, wire-format header, tun2socks, lwIP, or Hev code is
modified.

## Coverage

- Builds `badvpn-udpgw` with AddressSanitizer and UndefinedBehaviorSanitizer.
- Verifies that the standalone executable links both sanitizer runtimes.
- Runs the existing `chunkbuffer2_test` under the instrumented build.
- Opens real UDPGW mappings using distinct 16-bit connection IDs.
- Sends UDP requests to a local echo server and waits for the matching replies.
- Measures UDPGW process RSS, FD count, thread count, and elapsed mapping setup
  time at 16, 64, 256, 512, and 1,024 concurrent mappings.
- Repeats mapping creation and TCP-client disconnect cleanup three times.

## Run locally

    cmake -S . -B build-wp4 -DBUILD_NOTHING_BY_DEFAULT=ON \
      -DBUILD_TESTS=ON -DBUILD_UDPGW=ON \
      -DCMAKE_BUILD_TYPE=Debug \
      "-DCMAKE_C_FLAGS=-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined" \
      "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined"
    cmake --build build-wp4 --parallel 2 --target badvpn-udpgw chunkbuffer2_test
    build-wp4/tests/chunkbuffer2_test
    UDPGW_BIN="$PWD/build-wp4/udpgw/badvpn-udpgw" \
      python3 tests/udpgw-memory-ramp/memory_ramp.py

## Interpretation and limits

- RSS and FD values are read from Linux `/proc/<pid>`; they are host/runtime
  measurements, not static allocation estimates.
- Each ramp level represents mappings opened concurrently on one TCP client.
- Each mapping is verified by an echoed UDP datagram before the next level.
- The 16-bit connection ID range caps unique IDs at 65,536 per TCP client;
  this test deliberately measures only through 1,024.
- RSS after client disconnect is reported, but allocator retention may keep
  RSS above the original baseline even when mapping FDs are released.
- A clean sanitizer log only applies to the exercised code paths and test
  workload. It is not a proof of absence of all memory defects.
- This workflow does not measure Android, Cloud Run, carrier-network, or
  10k-100k mapping capacity. Larger ramp stages follow after this baseline
  memory profile is validated.

The CI artifact contains the JSON measurements and the full UDPGW runtime log.
