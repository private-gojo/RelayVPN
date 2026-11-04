#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import socket
import struct
import threading
import time


KEEPALIVE_FRAME = struct.pack("<H", 3) + b"\x01\x00\x00"
MAX_IDS_PER_STREAM = 65536
DESTINATION_PORT = 39091
DESTINATION_IPS = [f"127.0.0.{i}" for i in range(1, 17)]


def read_effective_capacity(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    matches = re.findall(r"effective_mappings=(\d+)", text)
    if not matches:
        raise RuntimeError("Server log does not contain effective_mappings")
    return int(matches[-1])


def fd_count(pid):
    return len(os.listdir(f"/proc/{pid}/fd"))


def process_memory(pid):
    result = {"rss_bytes": None, "hwm_bytes": None}
    with open(f"/proc/{pid}/status", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                result["rss_bytes"] = int(line.split()[1]) * 1024
            elif line.startswith("VmHWM:"):
                result["hwm_bytes"] = int(line.split()[1]) * 1024
    return result


def cgroup_memory_current():
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value.isdigit():
                return int(value)
        except OSError:
            continue
    return None


def start_sink(stop_event, counter):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DESTINATION_PORT))
    sock.settimeout(0.2)

    def receive():
        try:
            while not stop_event.is_set():
                try:
                    data, _ = sock.recvfrom(2048)
                    counter[0] += 1
                except socket.timeout:
                    continue
        finally:
            sock.close()

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    return thread


def make_stream_payload(mapping_count, start_index):
    payload = bytearray()
    for conid in range(mapping_count):
        index = start_index + conid
        destination_ip = DESTINATION_IPS[index % len(DESTINATION_IPS)]
        packet = (
            b"\x00"
            + struct.pack("<H", conid)
            + socket.inet_aton(destination_ip)
            + struct.pack("!H", DESTINATION_PORT)
            + b"R"
        )
        payload += struct.pack("<H", len(packet))
        payload += packet
    return payload


def main():
    parser = argparse.ArgumentParser(description="Measure live UDPGW mapping admission and FD scaling.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument("--target", type=int, default=100000)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--sample-interval", type=float, default=2.0)
    parser.add_argument("--output", default="udpgw-capacity-result.json")
    args = parser.parse_args()

    if not 1000 <= args.target <= 100000:
        parser.error("--target must be between 1000 and 100000")
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    if not 0.25 <= args.sample_interval <= 30:
        parser.error("--sample-interval must be between 0.25 and 30 seconds")

    server_capacity = read_effective_capacity(args.server_log)
    target = min(args.target, server_capacity)
    if target < 1000:
        raise RuntimeError(f"Detected runtime capacity is below 1000 mappings: {target}")

    stream_count = math.ceil(target / MAX_IDS_PER_STREAM)
    stream_sizes = [target // stream_count] * stream_count
    for index in range(target % stream_count):
        stream_sizes[index] += 1
    if max(stream_sizes) > MAX_IDS_PER_STREAM:
        raise RuntimeError("The target exceeds the 16-bit per-stream ID capacity")

    stop_event = threading.Event()
    datagram_count = [0]
    sink_thread = start_sink(stop_event, datagram_count)
    streams = []
    try:
        for _ in stream_sizes:
            sock = socket.create_connection((args.host, args.port), timeout=10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(30)
            streams.append(sock)

        time.sleep(0.5)
        baseline_fds = fd_count(args.server_pid)
        baseline_process_memory = process_memory(args.server_pid)
        baseline_cgroup_memory = cgroup_memory_current()
        payloads = [
            make_stream_payload(count, stream_index * MAX_IDS_PER_STREAM)
            for stream_index, count in enumerate(stream_sizes)
        ]

        start = time.monotonic()
        chunk_size = 512 * 12
        max_length = max(map(len, payloads))
        for offset in range(0, max_length, chunk_size):
            for index, sock in enumerate(streams):
                chunk = payloads[index][offset:offset + chunk_size]
                if chunk:
                    sock.sendall(chunk)

        keepalive_at = time.monotonic() + 5
        next_progress_at = start + 30
        deadline = start + args.timeout
        observed_fds = baseline_fds
        while True:
            observed_fds = fd_count(args.server_pid)
            now = time.monotonic()
            fd_delta = max(0, observed_fds - baseline_fds)
            if fd_delta >= target or now >= deadline:
                break
            if now >= keepalive_at:
                for sock in streams:
                    sock.sendall(KEEPALIVE_FRAME)
                keepalive_at = now + 5
            if now >= next_progress_at:
                print(
                    f"progress elapsed={now - start:.0f}s active_mappings={fd_delta}/{target}",
                    flush=True,
                )
                next_progress_at = now + 30
            time.sleep(min(args.sample_interval, max(0.0, deadline - now)))

        elapsed = time.monotonic() - start
        fd_delta = max(0, observed_fds - baseline_fds)
        memory = process_memory(args.server_pid)
        cgroup_memory_after = cgroup_memory_current()
        rss_delta = None
        if memory["rss_bytes"] is not None and baseline_process_memory["rss_bytes"] is not None:
            rss_delta = max(0, memory["rss_bytes"] - baseline_process_memory["rss_bytes"])
        cgroup_memory_delta = None
        if cgroup_memory_after is not None and baseline_cgroup_memory is not None:
            cgroup_memory_delta = max(0, cgroup_memory_after - baseline_cgroup_memory)
        result = {
            "requested_mappings": args.target,
            "runtime_capacity": server_capacity,
            "attempted_mappings": target,
            "stream_count": stream_count,
            "stream_sizes": stream_sizes,
            "baseline_server_fds": baseline_fds,
            "observed_server_fd_delta": fd_delta,
            "mapping_fd_target_reached": fd_delta >= target,
            "elapsed_seconds": round(elapsed, 3),
            "fd_sample_interval_seconds": args.sample_interval,
            "baseline_rss_bytes": baseline_process_memory["rss_bytes"],
            "rss_delta_bytes": rss_delta,
            "rss_delta_bytes_per_mapping": round(rss_delta / fd_delta, 2) if rss_delta is not None and fd_delta else None,
            "baseline_cgroup_memory_bytes": baseline_cgroup_memory,
            "cgroup_memory_delta_bytes": cgroup_memory_delta,
            "cgroup_memory_delta_bytes_per_mapping": round(cgroup_memory_delta / fd_delta, 2) if cgroup_memory_delta is not None and fd_delta else None,
            "datagrams_received_by_sink": datagram_count[0],
            **memory,
        }

        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")

        print(json.dumps(result, indent=2))
        if fd_delta < target:
            raise RuntimeError(
                f"Only {fd_delta}/{target} mapping FDs became active before timeout"
            )
    finally:
        for sock in streams:
            try:
                sock.close()
            except OSError:
                pass
        stop_event.set()
        sink_thread.join(timeout=2)


if __name__ == "__main__":
    main()
