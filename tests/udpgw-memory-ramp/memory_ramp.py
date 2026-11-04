#!/usr/bin/env python3
"""Measure live UDPGW mapping memory, descriptors, and UDP echo completion."""

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
UDP_MTU = 1200


def packet(conid, target, payload):
    host, port = target
    address = socket.inet_aton(host)
    return HEADER.pack(0, conid) + address + struct.pack("!H", port) + payload


def framed(payload):
    return FRAME.pack(len(payload)) + payload


class EchoServer:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address = self.sock.getsockname()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.received = 0

    def run(self):
        self.sock.settimeout(0.1)
        while not self.stop_event.is_set():
            try:
                data, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self.received += 1
            try:
                self.sock.sendto(data, address)
            except OSError:
                pass

    def close(self):
        self.stop_event.set()
        self.sock.close()
        self.thread.join(timeout=1)


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


def read_replies(sock, expected, timeout):
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    seen = set()
    malformed = 0
    while len(seen) < expected and time.monotonic() < deadline:
        readable, _, _ = select.select([sock], [], [], min(0.1, deadline - time.monotonic()))
        if not readable:
            continue
        try:
            data = sock.recv(65536)
        except OSError:
            break
        if not data:
            break
        buffer.extend(data)
        offset = 0
        while len(buffer) - offset >= FRAME.size:
            length = FRAME.unpack_from(buffer, offset)[0]
            end = offset + FRAME.size + length
            if end > len(buffer):
                break
            payload = buffer[offset + FRAME.size:end]
            offset = end
            if len(payload) < 3 + 6:
                malformed += 1
                continue
            conid = HEADER.unpack_from(payload)[1]
            if payload[9:].startswith(b"WP4:"):
                seen.add(conid)
        if offset:
            del buffer[:offset]
    return {"echoed_mappings": len(seen), "malformed_frames": malformed,
            "trailing_bytes": len(buffer)}


def wait_for_fd_cleanup(pid, baseline, timeout=5):
    deadline = time.monotonic() + timeout
    latest = process_metrics(pid)
    while time.monotonic() < deadline:
        latest = process_metrics(pid)
        if latest["fd_count"] is None or latest["fd_count"] <= baseline + 2:
            break
        time.sleep(0.1)
    return latest


def run(args):
    os.makedirs(args.artifact_dir, exist_ok=True)
    log_path = os.path.join(args.artifact_dir, "udpgw-memory-ramp.log")
    log_file = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [args.binary,
         "--listen-addr", "127.0.0.1:0",
         "--max-clients", "1",
         "--max-connections-for-client", str(args.max_connections),
         "--udp-mtu", str(args.udp_mtu),
         "--loglevel", "notice"],
        stdout=log_file, stderr=subprocess.STDOUT
    )
    # Port 0 is intentionally not used: BadVPN does not publish its selected
    # ephemeral listener port. Select a free port before starting the process.
    process.terminate()
    process.wait(timeout=5)
    log_file.close()
    raise RuntimeError("internal listener allocation error")


def run_test(args):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        listen_port = probe.getsockname()[1]

    os.makedirs(args.artifact_dir, exist_ok=True)
    log_path = os.path.join(args.artifact_dir, "udpgw-memory-ramp.log")
    log_file = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [args.binary,
         "--listen-addr", "127.0.0.1:%d" % listen_port,
         "--max-clients", "1",
         "--max-connections-for-client", str(args.max_connections),
         "--udp-mtu", str(args.udp_mtu),
         "--loglevel", "notice"],
        stdout=log_file, stderr=subprocess.STDOUT
    )
    echo = EchoServer()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("UDPGW exited at startup: %s" % process.returncode)
            try:
                with socket.create_connection(("127.0.0.1", listen_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("UDPGW listener did not become ready")

        baseline = process_metrics(process.pid)
        results = []
        for cycle in range(1, args.cycles + 1):
            client = socket.create_connection(("127.0.0.1", listen_port), timeout=10)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(10)
            levels = []
            for count in args.levels:
                if count > args.max_connections or count > 65535:
                    raise ValueError("mapping level exceeds configured/per-client conid limits")
                started = time.monotonic()
                for first in range(0, count, args.batch_size):
                    batch = bytearray()
                    for conid in range(first, min(first + args.batch_size, count)):
                        payload = b"WP4:%05d" % conid
                        batch.extend(framed(packet(conid, echo.address, payload)))
                    client.sendall(batch)
                replies = read_replies(client, count, args.reply_timeout)
                elapsed = time.monotonic() - started
                metrics = process_metrics(process.pid)
                levels.append({
                    "active_mapping_target": count,
                    "echo_results": replies,
                    "elapsed_seconds": round(elapsed, 4),
                    "process": metrics,
                    "rss_delta_from_baseline_bytes": (
                        metrics["rss_bytes"] - baseline["rss_bytes"]
                        if metrics["rss_bytes"] is not None and baseline["rss_bytes"] is not None
                        else None
                    ),
                })
                if replies["echoed_mappings"] != count:
                    break

            client.close()
            time.sleep(args.cleanup_wait)
            cleanup = wait_for_fd_cleanup(process.pid, baseline["fd_count"] or 0)
            results.append({
                "cycle": cycle,
                "levels": levels,
                "after_client_disconnect": cleanup,
            })
            if process.poll() is not None:
                raise RuntimeError("UDPGW exited during cycle %d" % cycle)

        return {
            "binary": os.path.abspath(args.binary),
            "environment": {"platform": platform.platform(),
                            "python": platform.python_version()},
            "configuration": {
                "udp_mtu": args.udp_mtu,
                "max_clients": 1,
                "max_connections_for_client": args.max_connections,
                "cycles": args.cycles,
                "levels": args.levels,
                "batch_size": args.batch_size,
            },
            "baseline_process": baseline,
            "echo_server_datagrams_received": echo.received,
            "cycles": results,
            "sanitizer_log": log_path,
            "process_exit_code_before_shutdown": process.poll(),
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
    parser.add_argument("--levels", default="16,64,256,512,1024")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-connections", type=int, default=16384)
    parser.add_argument("--udp-mtu", type=int, default=UDP_MTU)
    parser.add_argument("--reply-timeout", type=float, default=30)
    parser.add_argument("--cleanup-wait", type=float, default=1)
    parser.add_argument("--artifact-dir", default="wp4-artifacts")
    parser.add_argument("--output", default="wp4-artifacts/memory-ramp.json")
    args = parser.parse_args()
    if not args.binary:
        parser.error("--binary or UDPGW_BIN is required")
    args.levels = [int(value) for value in args.levels.split(",")]
    if not args.levels or any(value <= 0 for value in args.levels):
        parser.error("--levels must contain positive integers")
    if args.cycles <= 0 or args.batch_size <= 0:
        parser.error("--cycles and --batch-size must be positive")
    if args.levels != sorted(set(args.levels)):
        parser.error("--levels must be unique and ascending")
    if max(args.levels) > min(args.max_connections, 65535):
        parser.error("highest level exceeds max-connections or 16-bit conid range")
    result = run_test(args)
    os.makedirs(args.artifact_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("WP4 measurements written to %s" % os.path.abspath(args.output))


if __name__ == "__main__":
    main()
