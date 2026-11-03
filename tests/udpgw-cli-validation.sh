#!/usr/bin/env bash
set -euo pipefail

TUN_BIN="${1:?tun2socks binary path required}"
UDPGW_BIN="${2:?udpgw binary path required}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

expect_failure() {
    local binary="$1"
    local expected="$2"
    shift 2
    if "$binary" "$@" --help > "$TMP" 2>&1; then
        echo "Expected argument validation to fail: $*" >&2
        exit 1
    fi
    if ! grep -Fq -- "$expected" "$TMP"; then
        cat "$TMP" >&2
        echo "Missing expected validation message: $expected" >&2
        exit 1
    fi
}

expect_success_help() {
    local binary="$1"
    shift
    "$binary" "$@" --help > /dev/null
}

expect_success_help "$TUN_BIN" --udpgw-max-connections 65536
expect_failure "$TUN_BIN" "--udpgw-max-connections: wrong argument" --udpgw-max-connections 65537
expect_failure "$TUN_BIN" "--udpgw-max-connections: wrong argument" --udpgw-max-connections 12junk
expect_failure "$TUN_BIN" "--udpgw-connection-buffer-size: wrong argument" --udpgw-connection-buffer-size 2147483648

expect_success_help "$UDPGW_BIN" --max-connections-for-client 65536
expect_failure "$UDPGW_BIN" "--max-connections-for-client: wrong argument" --max-connections-for-client 65537
expect_failure "$UDPGW_BIN" "--max-connections-for-client: wrong argument" --max-connections-for-client 256junk
expect_failure "$UDPGW_BIN" "--max-clients: wrong argument" --max-clients 0
expect_failure "$UDPGW_BIN" "--max-clients: wrong argument" --max-clients invalid
expect_success_help "$UDPGW_BIN" --udp-mtu 65514
expect_failure "$UDPGW_BIN" "--udp-mtu: wrong argument" --udp-mtu 65515
expect_failure "$UDPGW_BIN" "--udp-mtu: wrong argument" --udp-mtu 2147483648
expect_failure "$UDPGW_BIN" "--local-udp-addrs: wrong argument" --local-udp-addrs 127.0.0.1 65537

echo "PASS: BadVPN UDPGW and tun2socks numeric CLI validation"
