#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="$ROOT_DIR/third_party/hev-socks5-tunnel"

readonly HEV_URL="https://github.com/heiher/hev-socks5-tunnel.git"
readonly HEV_COMMIT="1711a443d72d89679c0a2044f36085358a101bc4"

if [[ -d "$DEST_DIR" ]]; then
    if [[ -f "$DEST_DIR/.rev-id" ]] && [[ "$(cat "$DEST_DIR/.rev-id")" == "$HEV_COMMIT" ]]; then
        echo "Hev source already vendored at the expected commit: $HEV_COMMIT"
        exit 0
    fi
    echo "Refusing to overwrite an existing vendor directory: $DEST_DIR" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
STAGE_DIR="$ROOT_DIR/third_party/.hev-socks5-tunnel-import.$$"

cleanup() {
    rm -rf "$WORK_DIR" "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$ROOT_DIR/third_party"

echo "Cloning Hev source: $HEV_COMMIT"
git clone "$HEV_URL" "$WORK_DIR/hev"
git -C "$WORK_DIR/hev" checkout --detach "$HEV_COMMIT"
git -C "$WORK_DIR/hev" submodule sync --recursive
git -C "$WORK_DIR/hev" submodule update --init --recursive

check_submodule() {
    local path="$1"
    local expected="$2"
    local actual

    actual="$(git -C "$WORK_DIR/hev/$path" rev-parse HEAD)"
    if [[ "$actual" != "$expected" ]]; then
        echo "Submodule revision mismatch for $path: expected $expected, got $actual" >&2
        exit 1
    fi
    printf '%s %s\n' "$path" "$actual"
}

echo "Verifying pinned dependency revisions"
check_submodule "src/core" "d12ec5962cbb777e087015db6aa73374014dd82e"
check_submodule "third-part/hev-task-system" "83196e8286c07e9eb174505eea4d076a6503ab48"
check_submodule "third-part/lwip" "e274b3bea0877611773b5869a63c737ef231e841"
check_submodule "third-part/yaml" "7e2ecb0e67995d424b35e436f846078d37904b4e"

mkdir -p "$STAGE_DIR"
python3 - "$WORK_DIR/hev" "$STAGE_DIR" <<'PY'
import shutil
import sys

source, destination = sys.argv[1:3]
shutil.copytree(
    source,
    destination,
    symlinks=True,
    ignore=shutil.ignore_patterns(".git", ".gitmodules"),
)
PY

cp "$WORK_DIR/hev/.gitmodules" "$STAGE_DIR/UPSTREAM.gitmodules"
printf '%s\n' "$HEV_COMMIT" > "$STAGE_DIR/.rev-id"

required_licenses=(
    "LICENSE"
    "src/core/LICENSE"
    "third-part/hev-task-system/LICENSE"
    "third-part/lwip/LICENSE"
    "third-part/yaml/LICENSE"
    "third-part/wintun/LICENSE.txt"
)

for license_path in "${required_licenses[@]}"; do
    if [[ ! -s "$STAGE_DIR/$license_path" ]]; then
        echo "Required license file missing from vendored source: $license_path" >&2
        exit 1
    fi
done

if find "$STAGE_DIR" -name .git -print -quit | grep -q .; then
    echo "Git metadata remains in the vendor copy" >&2
    exit 1
fi

mv "$STAGE_DIR" "$DEST_DIR"
echo "Vendored Hev and all four pinned dependencies into $DEST_DIR"
