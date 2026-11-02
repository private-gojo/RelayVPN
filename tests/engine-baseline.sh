#!/usr/bin/env bash
set -euo pipefail

ROOT="$RUNNER_TEMP/phase2a"
ARTIFACT_DIR="$GITHUB_WORKSPACE/phase2a-artifacts"
TUN_BIN="$RUNNER_TEMP/relayvpn-phase2a-build/tun2socks/badvpn-tun2socks"
HEV_BIN="$GITHUB_WORKSPACE/third_party/hev-socks5-tunnel/bin/hev-socks5-tunnel"
ENGINE_PID=""
SAMPLE_PID=""
SSH_PID=""
HTTP_PID=""

mkdir -p "$ROOT/www" "$ROOT/logs" "$ARTIFACT_DIR"
head -c 52428800 /dev/urandom > "$ROOT/www/large.bin"
printf 'RelayVPN engine baseline probe\n' > "$ROOT/www/small.txt"
printf 'engine,round,bytes,elapsed_ms,throughput_mbps\n' > "$ARTIFACT_DIR/throughput.csv"
printf 'engine,elapsed_s,cpu_percent,rss_kib\n' > "$ARTIFACT_DIR/process-samples.csv"
sha256sum "$ROOT/www/large.bin" | awk '{print $1}' > "$ROOT/expected.sha256"

cleanup() {
    set +e
    for pid in "$ENGINE_PID" "$SAMPLE_PID" "$SSH_PID" "$HTTP_PID"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            sudo kill -TERM "$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
        fi
    done
    if [[ -s "$ROOT/sshd.pid" ]]; then sudo kill "$(cat "$ROOT/sshd.pid")" 2>/dev/null; fi
    sudo ip -n cli addr > "$ARTIFACT_DIR/cli-ip-addr.txt" 2>&1
    sudo ip -n cli route > "$ARTIFACT_DIR/cli-ip-route.txt" 2>&1
    cp -a "$ROOT/logs/." "$ARTIFACT_DIR/" 2>/dev/null
    cp "$ROOT/expected.sha256" "$ARTIFACT_DIR/" 2>/dev/null
    sudo ip link del veth-root 2>/dev/null
    sudo ip addr del 198.51.100.10/32 dev lo 2>/dev/null
    sudo ip netns del cli 2>/dev/null
}
trap cleanup EXIT

sudo ip netns add cli
sudo ip link add veth-root type veth peer name veth-cli
sudo ip link set veth-cli netns cli
sudo ip addr add 10.200.0.1/30 dev veth-root
sudo ip link set veth-root up
sudo ip -n cli addr add 10.200.0.2/30 dev veth-cli
sudo ip -n cli link set lo up
sudo ip -n cli link set veth-cli up
sudo ip addr add 198.51.100.10/32 dev lo

sudo useradd --create-home --shell /bin/bash relayvpn-test
sudo usermod --password '*' relayvpn-test
sudo install -d -m 700 -o relayvpn-test -g relayvpn-test /home/relayvpn-test/.ssh
ssh-keygen -q -t ed25519 -N '' -f "$ROOT/id_ed25519"
sudo install -m 600 -o relayvpn-test -g relayvpn-test "$ROOT/id_ed25519.pub" /home/relayvpn-test/.ssh/authorized_keys
sudo ssh-keygen -A
cat > "$ROOT/sshd_config" <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey /etc/ssh/ssh_host_ed25519_key
PidFile $ROOT/sshd.pid
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers relayvpn-test
AllowTcpForwarding yes
PermitOpen any
UsePAM no
StrictModes yes
LogLevel ERROR
EOF
sudo mkdir -p /run/sshd
sudo /usr/sbin/sshd -f "$ROOT/sshd_config" -E "$ROOT/logs/sshd.log"
for i in $(seq 1 30); do
    if ssh-keyscan -p 2222 127.0.0.1 > "$ROOT/known_hosts" 2>/dev/null && [[ -s "$ROOT/known_hosts" ]]; then break; fi
    sleep 1
done
test -s "$ROOT/known_hosts"

ssh -N -D 10.200.0.1:1080 -p 2222 \
    -i "$ROOT/id_ed25519" \
    -o UserKnownHostsFile="$ROOT/known_hosts" \
    -o StrictHostKeyChecking=yes \
    -o ExitOnForwardFailure=yes \
    relayvpn-test@127.0.0.1 > "$ROOT/logs/ssh-client.log" 2>&1 &
SSH_PID=$!
for i in $(seq 1 30); do
    if timeout 1 bash -c '</dev/tcp/10.200.0.1/1080' 2>/dev/null; then break; fi
    sleep 1
done
kill -0 "$SSH_PID"

WEB_ROOT="$ROOT/www" python3 -c 'import http.server,os; os.chdir(os.environ["WEB_ROOT"]); http.server.ThreadingHTTPServer(("198.51.100.10",8080),http.server.SimpleHTTPRequestHandler).serve_forever()' > "$ROOT/logs/http.log" 2>&1 &
HTTP_PID=$!
sleep 1

cat > "$ROOT/hev.yml" <<EOF
tunnel:
  name: tun0
  mtu: 1500
  ipv4: 198.18.0.1
  icmp: 'off'
socks5:
  address: 10.200.0.1
  port: 1080
  udp: 'udp'
misc:
  log-file: stderr
  log-level: warn
EOF

start_engine() {
    local engine="$1"
    if [[ "$engine" == "badvpn" ]]; then
        sudo bash -c 'exec ip netns exec cli "$@"' _ "$TUN_BIN" \
            --tundev tun0 --netif-ipaddr 198.18.0.1 \
            --netif-netmask 255.255.255.0 \
            --socks-server-addr 10.200.0.1:1080 \
            --loglevel warning > "$ROOT/logs/badvpn.log" 2>&1 &
    else
        sudo bash -c 'exec ip netns exec cli "$@"' _ "$HEV_BIN" "$ROOT/hev.yml" > "$ROOT/logs/hev.log" 2>&1 &
    fi
    ENGINE_PID=$!
    for i in $(seq 1 30); do
        if sudo ip -n cli link show tun0 >/dev/null 2>&1; then break; fi
        if ! sudo kill -0 "$ENGINE_PID" 2>/dev/null; then cat "$ROOT/logs/$engine.log"; return 1; fi
        sleep 1
    done
    sudo ip -n cli link set tun0 mtu 1500 up
    sudo ip -n cli route add default dev tun0
}

sample_process() {
    local engine="$1"
    local start_time
    start_time="$(date +%s)"
    while kill -0 "$ENGINE_PID" 2>/dev/null; do
        local sample
        sample="$(ps -p "$ENGINE_PID" -o %cpu=,rss= 2>/dev/null | awk 'NF {print $1 "," $2}')"
        if [[ -n "$sample" ]]; then
            printf '%s,%s,%s\n' "$engine" "$(( $(date +%s) - start_time ))" "$sample" >> "$ARTIFACT_DIR/process-samples.csv"
        fi
        sleep 0.25
    done
}

stop_engine() {
    local engine="$1"
    sudo kill -TERM "$ENGINE_PID"
    set +e
    wait "$ENGINE_PID"
    local status=$?
    set -e
    echo "$engine exit status: $status"
    for i in $(seq 1 20); do
        if ! sudo ip -n cli link show tun0 >/dev/null 2>&1; then break; fi
        sleep 1
    done
    if sudo ip -n cli link show tun0 >/dev/null 2>&1; then echo "$engine did not release tun0" >&2; return 1; fi
    wait "$SAMPLE_PID" 2>/dev/null || true
    SAMPLE_PID=""
    ENGINE_PID=""
}

benchmark_engine() {
    local engine="$1"
    echo "=== $engine baseline ==="
    start_engine "$engine"
    sample_process "$engine" &
    SAMPLE_PID=$!
    for round in 1 2 3; do
        local start end elapsed bytes actual expected mbps
        start="$(date +%s%N)"
        sudo ip netns exec cli curl --noproxy '*' --fail --silent --show-error \
            http://198.51.100.10:8080/large.bin -o "$ROOT/$engine-$round.bin"
        end="$(date +%s%N)"
        elapsed=$(( (end - start) / 1000000 ))
        bytes="$(stat -c '%s' "$ROOT/$engine-$round.bin")"
        actual="$(sha256sum "$ROOT/$engine-$round.bin" | awk '{print $1}')"
        expected="$(cat "$ROOT/expected.sha256")"
        test "$actual" = "$expected"
        mbps="$(awk -v b="$bytes" -v ms="$elapsed" 'BEGIN { if (ms > 0) printf "%.2f", (b * 8 / 1000000) / (ms / 1000); else print "0" }')"
        printf '%s,%s,%s,%s,%s\n' "$engine" "$round" "$bytes" "$elapsed" "$mbps" | tee -a "$ARTIFACT_DIR/throughput.csv"
    done
    seq 1 50 | xargs -P 50 -I{} sh -c '
        sudo ip netns exec cli curl --noproxy "*" --fail --silent --show-error \
          http://198.51.100.10:8080/small.txt -o "$1/$2-parallel-{}.txt"
        cmp "$1/www/small.txt" "$1/$2-parallel-{}.txt"
    ' _ "$ROOT" "$engine"
    echo "$engine: 50/50 concurrent TCP requests passed"
    stop_engine "$engine"
}

benchmark_engine badvpn
benchmark_engine hev
cat "$ARTIFACT_DIR/throughput.csv"
echo 'PASS: both engines completed identical TCP throughput and concurrency workloads'
