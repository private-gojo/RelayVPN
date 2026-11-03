#!/usr/bin/env bash
set -euo pipefail

TUN_BIN="${1:?tun2socks binary path required}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

"$TUN_BIN" --help > "$TMP"
grep -Fq -- "[--tunfd <fd> --tunmtu <mtu>]" "$TMP"

expect_failure() {
    local expected="$1"
    shift
    if "$TUN_BIN" "$@" > "$TMP" 2>&1; then
        echo "Expected tun2socks argument validation to fail: $*" >&2
        exit 1
    fi
    if ! grep -Fq -- "$expected" "$TMP"; then
        cat "$TMP" >&2
        echo "Missing expected validation message: $expected" >&2
        exit 1
    fi
}

expect_failure "--tunmtu is required with --tunfd" --tunfd 3
expect_failure "--tunmtu requires --tunfd" --tunmtu 1400
expect_failure "--tunfd and --tundev cannot both be used" --tunfd 3 --tunmtu 1400 --tundev tun0
expect_failure "--tunfd: wrong argument" --tunfd invalid

echo "PASS: TUN file-descriptor CLI validation"
