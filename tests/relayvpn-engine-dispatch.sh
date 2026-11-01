#!/usr/bin/env bash
set -euo pipefail

DISPATCHER="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/scripts/relayvpn}"
if [[ ! -x "$DISPATCHER" ]]; then
    printf 'dispatcher is not executable: %s\n' "$DISPATCHER" >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

make_fake_engine() {
    local path="$1"
    cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$RELAYVPN_TEST_CAPTURE"
exit "${RELAYVPN_TEST_EXIT:-0}"
EOF
    chmod +x "$path"
}

expect_status() {
    local expected="$1"
    shift
    local actual
    set +e
    "$@"
    actual=$?
    set -e
    if [[ "$actual" -ne "$expected" ]]; then
        printf 'expected exit %s, got %s\n' "$expected" "$actual" >&2
        exit 1
    fi
}

make_fake_engine "$tmp/badvpn"
make_fake_engine "$tmp/hev"

expect_status 23 env \
    RELAYVPN_BADVPN_BIN="$tmp/badvpn" \
    RELAYVPN_TEST_CAPTURE="$tmp/capture" \
    RELAYVPN_TEST_EXIT=23 \
    "$DISPATCHER" badvpn --netif-ipaddr 10.0.0.2 --socks-server-addr 127.0.0.1:1080

printf '%s\n' \
    --netif-ipaddr \
    10.0.0.2 \
    --socks-server-addr \
    127.0.0.1:1080 > "$tmp/expected"
cmp "$tmp/expected" "$tmp/capture"

expect_status 19 env \
    RELAYVPN_HEV_BIN="$tmp/hev" \
    RELAYVPN_TEST_CAPTURE="$tmp/capture" \
    RELAYVPN_TEST_EXIT=19 \
    "$DISPATCHER" hev /tmp/hev-test.yml

printf '%s\n' /tmp/hev-test.yml > "$tmp/expected"
cmp "$tmp/expected" "$tmp/capture"

expect_status 2 "$DISPATCHER"
expect_status 2 "$DISPATCHER" invalid-engine
expect_status 2 "$DISPATCHER" hev

printf 'PASS: engine selection, native argument forwarding, exit propagation, and usage validation\n'
