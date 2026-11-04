#!/usr/bin/env python3
"""Profile standalone BadVPN UDPGW CPU cost under sustained UDP echo load."""

import argparse
import collections
import json
import os
import platform
import select
import socket
import struct
import subprocess
import threading
import time

FRAME = struct.Struct("<H")
HEADER = struct.Struct("<BH")
MAGIC = b"WP5"


def make_packet(conid, target, payload):
    host, port = target
    body = HEADER.pack(0, conid) + socket.inet_aton(host)
    body += struct.pack("!H", port) + payload
    return FRAME.pack(len(body)) + body


class FrameCounter:
    def __init__(self):
        self.buffer = bytearray()
        self.counts = collections.Counter()
        self.malformed = 0
        self.lock = threading.Lock()

    def feed(self, data):
        with self.lock:
            self.buffer.extend(data)
            offset = 0
            while len(self.buffer) - offset >= FRAME.size:
                length = FRAME.unpack_from(self.buffer, offset)[0]
                end = offset + FRAME.size + length
                if end > len(self.buffer):
                    break
                body_start = offset + FRAME.size
                body = self.buffer[body_start:end]
                if len(body) < 9:
                    self.malformed += 1
                else:
                    payload = body[9:]
                    if len(payload) >= 4 and payload[:3] == MAGIC:
                        self.counts[payload[3]] += 1
                offset = end
            if offset:
                del self.buffer[:offset]

    def count(self, trial):
        with self.lock:
            return self.counts[trial]

    def snapshot(self):
        with self.lock:
            return dict(self.counts), self.malformed, len(self.buffer)


class UdpEchoServer:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        line = self.process.stdout.readline().strip()
        if not line or self.process.poll() is not None:
            raise RuntimeError("UDP echo helper failed to start")
        self.address = ("127.0.0.1", int(line))

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


def read_cpu_ticks(pid):
    with open("/proc/%d/stat" % pid, encoding="ascii") as stream:
        raw = stream.read()
    fields = raw[raw.rfind(")") + 2:].split()
    return int(fields[11]), int(fields[12])


def process_metrics(pid):
    result = {"rss_bytes": None, "fd_count": None, "threads": None}
    try:
        with open("/proc/%d/status" % pid, encoding="ascii") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    result["rss_bytes"] = int(line.split()[1]) * 1024
                elif line.startswith("Threads:"):
                    result["threads"] = int(line.split()[1])
        result["fd_count"] = len(os.listdir("/proc/%d/fd" % pid))
    except (OSError, ValueError, IndexError):
        pass
    return result


def wait_for_ready(process, port, log_path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("UDPGW exited during startup; see %s" % log_path)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("UDPGW listener did not become ready; see %s" % log_path)


def reserve_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def warm_mappings(client, mappings, target, payload_size, timeout):
    counter = FrameCounter()
    payload = MAGIC + b"\x00" + struct.pack("!I", 0) + bytes(max(0, payload_size - 8))
    for start in range(0, mappings, 32):
        batch = bytearray()
        for conid in range(start, min(start + 32, mappings)):
            batch.extend(make_packet(conid, target, payload))
        client.sendall(batch)

    deadline = time.monotonic() + timeout
    while counter.count(0) < mappings and time.monotonic() < deadline:
        readable, _, _ = select.select([client], [], [], 0.1)
        if readable:
            data = client.recv(65536)
            if not data:
                break
            counter.feed(data)
    if counter.count(0) != mappings:
        raise RuntimeError("mapping warmup incomplete: %d/%d echoes" %
                           (counter.count(0), mappings))


def read_log_delta(log_path, offset):
    counts = collections.Counter()
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as stream:
            stream.seek(offset)
            content = stream.read()
            new_offset = stream.tell()
        counts["out_of_client_buffer"] = content.count("out of client buffer")
        counts["out_of_udp_buffer"] = content.count("out of UDP buffer")
    except OSError:
        new_offset = offset
    return new_offset, dict(counts)


def run_rate(process, client, echo, counter, mappings, rate, trial,
             duration, payload_size, batch_size, settle_timeout, log_path, log_offset):
    stop = threading.Event()
    sent = [0]
    send_error = [None]
    client.settimeout(2)
    filler_size = max(0, payload_size - 8)
    payload = MAGIC + bytes([trial]) + bytes(4) + bytes(filler_size)
    frames = [make_packet(conid, echo.address, payload) for conid in range(mappings)]
    start_wall = time.monotonic()
    server_start = read_cpu_ticks(process.pid)
    echo_start = read_cpu_ticks(echo.process.pid)
    load_start = read_cpu_ticks(os.getpid())

    def sender():
        sequence = 0
        next_deadline = start_wall
        try:
            while not stop.is_set():
                if time.monotonic() - start_wall >= duration:
                    break
                count = min(batch_size, max(1, int(rate * 0.01)))
                batch = b"".join(frames[(sequence + offset) % mappings]
                                 for offset in range(count))
                client.sendall(batch)
                sequence += count
                sent[0] += count
                next_deadline += count / rate
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    stop.wait(delay)
        except (OSError, RuntimeError) as exc:
            send_error[0] = str(exc)

    worker = threading.Thread(target=sender, daemon=True)
    worker.start()
    while worker.is_alive():
        if process.poll() is not None:
            stop.set()
            break
        worker.join(timeout=0.05)
    stop.set()
    worker.join(timeout=2)
    active_end_wall = time.monotonic()
    server_active_end = read_cpu_ticks(process.pid)
    echo_active_end = read_cpu_ticks(echo.process.pid)
    load_active_end = read_cpu_ticks(os.getpid())
    active_elapsed = max(active_end_wall - start_wall, 0.001)

    drain_deadline = time.monotonic() + settle_timeout
    while counter.count(trial) < sent[0] and time.monotonic() < drain_deadline:
        time.sleep(0.02)

    total_end_wall = time.monotonic()
    server_total_end = read_cpu_ticks(process.pid)
    echo_total_end = read_cpu_ticks(echo.process.pid)
    load_total_end = read_cpu_ticks(os.getpid())
    total_elapsed = max(total_end_wall - start_wall, 0.001)

    hz = os.sysconf("SC_CLK_TCK")
    def cpu_delta(start, end):
        return ((end[0] + end[1]) - (start[0] + start[1])) / hz

    server_active_cpu = cpu_delta(server_start, server_active_end)
    server_total_cpu = cpu_delta(server_start, server_total_end)
    echo_active_cpu = cpu_delta(echo_start, echo_active_end)
    echo_total_cpu = cpu_delta(echo_start, echo_total_end)
    harness_active_cpu = cpu_delta(load_start, load_active_end)
    harness_total_cpu = cpu_delta(load_start, load_total_end)
    echoed = counter.count(trial)
    malformed, trailing = counter.snapshot()[1:]
    log_offset, log_errors = read_log_delta(log_path, log_offset)
    metrics = process_metrics(process.pid)
    result = {
        "trial": trial,
        "offered_rate_pps": rate,
        "active_load_elapsed_seconds": round(active_elapsed, 4),
        "completion_elapsed_seconds": round(total_elapsed, 4),
        "drain_elapsed_seconds": round(max(0.0, total_elapsed - active_elapsed), 4),
        "mappings": mappings,
        "payload_bytes": payload_size,
        "sent_datagrams": sent[0],
        "tcp_echoes_received": echoed,
        "sent_pps": round(sent[0] / active_elapsed, 2),
        "echoed_pps_during_load_window": round(echoed / active_elapsed, 2),
        "echo_completion_ratio": round(echoed / sent[0], 5) if sent[0] else 0,
        "server_active_cpu_seconds": round(server_active_cpu, 4),
        "server_active_one_core_cpu_percent": round(100 * server_active_cpu / active_elapsed, 2),
        "server_total_cpu_seconds": round(server_total_cpu, 4),
        "server_total_one_core_cpu_percent": round(100 * server_total_cpu / total_elapsed, 2),
        "server_cpu_microseconds_per_echo": (
            round(server_total_cpu * 1_000_000 / echoed, 3) if echoed else None
        ),
        "echo_server_active_cpu_seconds": round(echo_active_cpu, 4),
        "echo_server_active_one_core_cpu_percent": round(100 * echo_active_cpu / active_elapsed, 2),
        "echo_server_total_cpu_seconds": round(echo_total_cpu, 4),
        "echo_server_total_one_core_cpu_percent": round(100 * echo_total_cpu / total_elapsed, 2),
        "harness_active_cpu_seconds": round(harness_active_cpu, 4),
        "harness_active_one_core_cpu_percent": round(100 * harness_active_cpu / active_elapsed, 2),
        "harness_total_cpu_seconds": round(harness_total_cpu, 4),
        "harness_total_one_core_cpu_percent": round(100 * harness_total_cpu / total_elapsed, 2),
        "server_process": metrics,
        "buffer_errors": log_errors,
        "send_error": send_error[0],
        "malformed_frames": malformed,
        "trailing_frame_bytes": trailing,
    }
    return result, log_offset


def run_profile(args):
    os.makedirs(args.artifact_dir, exist_ok=True)
    log_path = os.path.join(args.artifact_dir, "udpgw-cpu-profile.log")
    port = reserve_tcp_port()
    log_file = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [args.binary,
         "--listen-addr", "127.0.0.1:%d" % port,
         "--max-clients", "1",
         "--max-connections-for-client", str(args.max_connections),
         "--udp-mtu", "65520",
         "--loglevel", "notice"],
        stdout=log_file, stderr=subprocess.STDOUT
    )
    echo = UdpEchoServer(args.echo_binary)
    results = []
    try:
        wait_for_ready(process, port, log_path)
        baseline = process_metrics(process.pid)
        trial = 1
        for mappings in args.mapping_levels:
            if mappings > args.max_connections or mappings > 65535:
                raise ValueError("mapping level exceeds configured or wire conid limit")
            client = socket.create_connection(("127.0.0.1", port), timeout=10)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(10)
            warm_mappings(client, mappings, echo.address, args.payload_bytes, args.reply_timeout)
            counter = FrameCounter()
            reader_stop = threading.Event()

            def reader():
                while not reader_stop.is_set():
                    try:
                        readable, _, _ = select.select([client], [], [], 0.1)
                        if not readable:
                            continue
                        data = client.recv(65536)
                    except OSError:
                        break
                    if not data:
                        break
                    counter.feed(data)

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()
            log_offset = os.path.getsize(log_path)
            for rate in args.rates:
                result, log_offset = run_rate(
                    process, client, echo, counter, mappings, rate, trial,
                    args.duration, args.payload_bytes, args.batch_size,
                    args.settle_timeout, log_path, log_offset
                )
                results.append(result)
                if process.poll() is not None:
                    raise RuntimeError("UDPGW exited during CPU profile")
                trial += 1
            reader_stop.set()
            client.close()
            reader_thread.join(timeout=2)
            time.sleep(args.cleanup_wait)

        return {
            "binary": os.path.abspath(args.binary),
            "environment": {"platform": platform.platform(),
                            "python": platform.python_version()},
            "configuration": {
                "mapping_levels": args.mapping_levels,
                "rates_pps": args.rates,
                "duration_seconds_per_rate": args.duration,
                "payload_bytes": args.payload_bytes,
                "batch_size": args.batch_size,
                "max_clients": 1,
                "max_connections_for_client": args.max_connections,
            },
            "baseline_process": baseline,
            "results": results,
            "udpgw_log": log_path,
        }
    finally:
        echo.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log_file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=os.environ.get("UDPGW_BIN"))
    parser.add_argument("--echo-binary", default=os.environ.get("UDP_ECHO_BIN"))
    parser.add_argument("--mapping-levels", default="64,256,1024")
    parser.add_argument("--rates", default="2000,5000,10000,20000,40000,60000,80000,100000")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--payload-bytes", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-connections", type=int, default=4096)
    parser.add_argument("--reply-timeout", type=float, default=15.0)
    parser.add_argument("--settle-timeout", type=float, default=2.0)
    parser.add_argument("--cleanup-wait", type=float, default=0.5)
    parser.add_argument("--artifact-dir", default="wp5-artifacts")
    parser.add_argument("--output", default="wp5-artifacts/cpu-profile.json")
    args = parser.parse_args()
    if not args.binary:
        parser.error("--binary or UDPGW_BIN is required")
    if not args.echo_binary:
        parser.error("--echo-binary or UDP_ECHO_BIN is required")
    args.mapping_levels = [int(value) for value in args.mapping_levels.split(",")]
    args.rates = [int(value) for value in args.rates.split(",")]
    if not args.mapping_levels or any(value <= 0 for value in args.mapping_levels):
        parser.error("--mapping-levels must contain positive integers")
    if args.mapping_levels != sorted(set(args.mapping_levels)):
        parser.error("--mapping-levels must be unique and ascending")
    if not args.rates or any(value <= 0 for value in args.rates):
        parser.error("--rates must contain positive integers")
    if args.rates != sorted(set(args.rates)):
        parser.error("--rates must be unique and ascending")
    if args.duration <= 0 or args.payload_bytes < 8 or args.batch_size <= 0:
        parser.error("duration, payload size, and batch size must be positive")
    if max(args.mapping_levels) > min(args.max_connections, 65535):
        parser.error("highest mapping level exceeds max-connections or conid range")
    if len(args.mapping_levels) * len(args.rates) > 255:
        parser.error("too many profiling trials for the one-byte trial marker")
    result = run_profile(args)
    os.makedirs(args.artifact_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("WP5 CPU profile written to %s" % os.path.abspath(args.output))


if __name__ == "__main__":
    main()
