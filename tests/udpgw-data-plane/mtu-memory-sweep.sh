#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "Usage: $0 <badvpn-udpgw> <go-runner> <mapping-count> <report-dir>" >&2
    exit 2
fi

UDPGW_BINARY=$1
RUNNER=$2
MAPPINGS=$3
REPORT_DIR=$4

mkdir -p "$REPORT_DIR"

profiles=(
    "576:576"
    "1200:1200"
    "1500:1500"
    "4096:4096"
    "8192:8192"
    "16384:16384"
    "32768:32768"
    "65514:65507"
)

failures=0
for profile in "${profiles[@]}"; do
    IFS=: read -r mtu payload <<< "$profile"
    report="$REPORT_DIR/mtu-$mtu.json"
    echo "=== UDPGW MTU sweep: mtu=$mtu payload=$payload mappings=$MAPPINGS ==="
    if "$RUNNER" \
        --binary "$UDPGW_BINARY" \
        --mappings "$MAPPINGS" \
        --udp-mtu "$mtu" \
        --payload-size "$payload" \
        --report "$report"; then
        echo "PASS mtu=$mtu"
    else
        status=$?
        echo "FAIL mtu=$mtu exit=$status report=$report" >&2
        failures=$((failures + 1))
    fi
done

python3 tests/udpgw-data-plane/summarize-mtu-sweep.py "$REPORT_DIR"

if [[ $failures -ne 0 ]]; then
    echo "MTU sweep completed with $failures failed profile(s)." >&2
    exit 1
fi
