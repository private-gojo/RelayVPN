#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_DIR="$GITHUB_WORKSPACE/phase2e-artifacts"
WORKTREE_ROOT="$RUNNER_TEMP/phase2e-worktrees"
BUILD_ROOT="$RUNNER_TEMP/phase2e-builds"
MANIFEST="$ARTIFACT_DIR/manifest.json"

mkdir -p "$ARTIFACT_DIR" "$WORKTREE_ROOT" "$BUILD_ROOT"
git config --global --add safe.directory "$GITHUB_WORKSPACE"
printf '{"arms":[]}\n' > "$MANIFEST"

for spec in BASE:b1c51a6 P2B0:0c0ef9d P2B:f0b247e P2C:95fcf20 P2D:523b38b; do
  label="$(cut -d: -f1 <<< "$spec")"
  revision="$(cut -d: -f2 <<< "$spec")"
  sha="$(git rev-parse "$revision^{commit}")"
  if [[ "$sha" != "$revision"* ]]; then
    echo "Revision mismatch: $revision resolved to $sha" >&2
    exit 1
  fi

  tree="$WORKTREE_ROOT/$label"
  build="$BUILD_ROOT/$label"
  git worktree add --detach "$tree" "$sha"

  cmake -S "$tree" -B "$build" \
    -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_NOTHING_BY_DEFAULT=ON \
    -DBUILD_TUN2SOCKS=ON \
    -DBUILD_UDPGW=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_HEV=OFF

  cmake --build "$build" --target badvpn-tun2socks --parallel 2
  binary="$build/tun2socks/badvpn-tun2socks"
  test -x "$binary"

  probe="$build/relayvpn-config-probe"
  cc -std=gnu99 -DBADVPN_LITTLE_ENDIAN -DBADVPN_THREAD_SAFE=1 \
    -I"$tree" \
    -I"$tree/lwip/custom" \
    -I"$tree/lwip/src/include" \
    "$GITHUB_WORKSPACE/tests/bench/config_probe.c" \
    -o "$probe"
  IFS=, read -r tcp_wnd tcp_snd socks_recv pcb_count memp_malloc < <("$probe")

  layout="$ARTIFACT_DIR/tcp-client-$label.pahole.txt"
  pahole -C tcp_client "$binary" > "$layout"
  struct_size="$(sed -nE 's@.*size: ([0-9]+),.*@\1@p' "$layout" | head -n 1)"
  if [[ -z "$struct_size" ]]; then
    echo "Could not extract sizeof(struct tcp_client) for $label" >&2
    cat "$layout" >&2
    exit 1
  fi

  python3 - "$MANIFEST" "$label" "$sha" "$binary" "$tcp_wnd" "$tcp_snd" \
    "$socks_recv" "$pcb_count" "$memp_malloc" "$struct_size" <<'PY'
import json
import sys
path, label, sha, binary, wnd, snd, recv, pcb, memp, size = sys.argv[1:]
with open(path, encoding="utf-8") as source:
    manifest = json.load(source)
manifest["arms"].append({
    "label": label,
    "sha": sha,
    "kind": "badvpn",
    "binary": binary,
    "tcp_wnd": int(wnd),
    "tcp_snd_buf": int(snd),
    "socks_recv_buf": int(recv),
    "memp_num_tcp_pcb": int(pcb),
    "memp_mem_malloc": int(memp),
    "tcp_client_size": int(size),
})
with open(path, "w", encoding="utf-8") as output:
    json.dump(manifest, output, indent=2)
    output.write("\n")
PY

  printf '%s: sha=%s TCP_WND=%s TCP_SND_BUF=%s SOCKS_RECV=%s tcp_client=%s bytes\n' \
    "$label" "$sha" "$tcp_wnd" "$tcp_snd" "$socks_recv" "$struct_size"
done

HEV_BUILD="$BUILD_ROOT/HEV"
cmake -S "$GITHUB_WORKSPACE" -B "$HEV_BUILD" \
  -G "Unix Makefiles" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DBUILD_HEV=ON \
  -DBUILD_NOTHING_BY_DEFAULT=ON \
  -DBUILD_TUN2SOCKS=OFF \
  -DBUILD_UDPGW=OFF \
  -DBUILD_TESTS=OFF
cmake --build "$HEV_BUILD" --target hev-bin --parallel 2
HEV_BINARY="$GITHUB_WORKSPACE/third_party/hev-socks5-tunnel/bin/hev-socks5-tunnel"
test -x "$HEV_BINARY"
MAIN_SHA="$(git rev-parse 523b38b^{commit})"
python3 - "$MANIFEST" "$MAIN_SHA" "$HEV_BINARY" <<'PY'
import json
import sys
path, sha, binary = sys.argv[1:]
with open(path, encoding="utf-8") as source:
    manifest = json.load(source)
manifest["arms"].append({
    "label": "HEV",
    "sha": sha,
    "kind": "hev",
    "binary": binary,
})
with open(path, "w", encoding="utf-8") as output:
    json.dump(manifest, output, indent=2)
    output.write("\n")
PY

cat "$MANIFEST"
ulimit -n 8192
python3 "$GITHUB_WORKSPACE/tests/bench/measure.py" \
  --manifest "$MANIFEST" \
  --work-dir "$RUNNER_TEMP/phase2e-lab" \
  --artifact-dir "$ARTIFACT_DIR" \
  --mode resume \
  --resume-from "$GITHUB_WORKSPACE/phase2e-previous" \
  --source-run-id "$PHASE2E_SOURCE_RUN_ID"
python3 "$GITHUB_WORKSPACE/tests/bench/summarize.py" \
  --manifest "$MANIFEST" \
  --artifact-dir "$ARTIFACT_DIR"
cat "$ARTIFACT_DIR/summary.md"
