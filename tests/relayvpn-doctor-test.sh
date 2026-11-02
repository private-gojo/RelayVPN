#!/usr/bin/env bash
set -euo pipefail

DOCTOR="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/scripts/relayvpn-doctor}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
: > "$TMP/badvpn"
: > "$TMP/hev"
chmod +x "$TMP/badvpn" "$TMP/hev"

cat > "$TMP/bin/ldd" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${RELAYVPN_TEST_LDD_OUTPUT:-libc.so.6 => /lib/libc.so.6 (0x0)}"
EOF
chmod +x "$TMP/bin/ldd"

COMMON_ENV=(
    "PATH=$TMP/bin:$PATH"
    "RELAYVPN_BADVPN_BIN=$TMP/badvpn"
    "RELAYVPN_HEV_BIN=$TMP/hev"
    "RELAYVPN_TUN_DEVICE=/dev/null"
)

env "${COMMON_ENV[@]}" "$DOCTOR" all

if env "${COMMON_ENV[@]}" \
    RELAYVPN_TEST_LDD_OUTPUT='libyaml.so => /usr/lib/libyaml.so (0x0)' \
    "$DOCTOR" hev; then
    echo "FAIL: Hev shared-library dependency was not rejected" >&2
    exit 1
fi

if env "${COMMON_ENV[@]}" \
    RELAYVPN_TEST_LDD_OUTPUT='libc.so.6 => not found' \
    "$DOCTOR" badvpn; then
    echo "FAIL: unresolved dependency was not rejected" >&2
    exit 1
fi

if env "${COMMON_ENV[@]}" RELAYVPN_BADVPN_BIN="$TMP/missing" "$DOCTOR" badvpn; then
    echo "FAIL: missing engine binary was not rejected" >&2
    exit 1
fi

printf 'PASS: doctor success, standalone-Hev dependency rejection, unresolved dependency rejection, and missing-binary rejection\n'
