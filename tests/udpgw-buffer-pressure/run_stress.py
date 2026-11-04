#!/usr/bin/env python3
"""Measure BadVPN UDPGW buffer pressure without modifying its implementation."""

import argparse
import collections
import ipaddress
import json
import os
import platform
import re
import select
import socket
import struct
import subprocess
import threading
import time

FLAG_IPV6 = 1 << 3
PACKETPROTO = struct.Struct("<H")
UDPGW_HEADER = struct.Struct("<BH")
UDP_MTU = 1200


def frame(payload):
    if len(payload) > 0xFFFF:
        raise ValueError("PacketProto payload exceeds uint16")
    return PACKETPROTO.pack(len(payload)) + payload


def udpgw_packet(conid, target, payload):
    host, port = target[:2]
    address = ipaddress.ip_address(host)
    flags = FLAG_IPV6 if address.version == 6 else 0
    return (
        UDPGW_HEADER.pack(flags, conid)
        + address.packed
        + struct.pack("!H", port)
        + payload
    )


def choose_tcp_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


class UDPGWProcess:
    def __init__(self, binary, host, max_clients, max_connections, sndbuf, udp_mtu, log_path):
        self.binary = binary
        self.host = host
        self.port = choose_tcp_port(host)
        self.log_path = log_path
        self.log_file = open(self.log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                binary,
                "--listen-addr", "%s:%d" % (host, self.port),
                "--udp-mtu", str(udp_mtu),
                "--max-clients", str(max_clients),
                "--max-connections-for-client", str(max_connections),
                "--client-socket-sndbuf", str(sndbuf),
                "--loglevel", "debug",
            ],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready()

    def _wait_ready(self):
        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            status = self.process.poll()
            if status is not None:
                raise RuntimeError(
                    "badvpn-udpgw exited during startup (%s):\n%s"
                    % (status, self.logs())
                )
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    return
            except OSError as error:
                last_error = error
                time.sleep(0.05)
        raise RuntimeError(
            "badvpn-udpgw did not become ready: %s\n%s"
            % (last_error, self.logs())
        )

    def connect(self, receive_buffer=4096, timeout=30):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout)
        sock.connect((self.host, self.port))
        return sock

    def logs(self):
        try:
            if not self.log_file.closed:
                self.log_file.flush()
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as stream:
                return stream.read()
        except (OSError, ValueError):
            return ""

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        try:
            self.log_file.close()
        except OSError:
            pass
        return self.logs()


class UDPCollector:
    def __init__(self, receive_buffer=4 * 1024 * 1024):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.address = self.sock.getsockname()
        self.effective_receive_buffer = self.sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF
        )
        self.counts = collections.Counter()
        self.total = 0
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stop_event.is_set():
            try:
                payload, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            with self.condition:
                self.counts[payload] += 1
                self.total += 1
                self.condition.notify_all()

    def wait_for(self, payload, count, timeout=8):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.counts[payload] < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.counts[payload]

    def close(self):
        self.stop_event.set()
        self.sock.close()
        self.thread.join(timeout=1)


class UDPFloodResponder:
    def __init__(self, count, payload_size, batch_size, batch_delay):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.address = self.sock.getsockname()
        self.count = count
        self.payload_size = payload_size
        self.batch_size = batch_size
        self.batch_delay = batch_delay
        self.stop_event = threading.Event()
        self.triggered = threading.Event()
        self.finished = threading.Event()
        self.sent = 0
        self.send_errors = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        target = None
        while not self.stop_event.is_set() and target is None:
            try:
                payload, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if payload.startswith(b"WP3-TRIGGER"):
                target = address
                self.triggered.set()

        if target is None:
            return

        base = b"WP3R!"
        sequence_size = 4
        padding = b"R" * max(0, self.payload_size - len(base) - sequence_size)
        for index in range(self.count):
            payload = base + struct.pack("!I", index) + padding
            try:
                self.sock.sendto(payload, target)
                self.sent += 1
            except OSError:
                self.send_errors += 1
            if self.batch_delay and (index + 1) % self.batch_size == 0:
                time.sleep(self.batch_delay)
        self.finished.set()

    def close(self):
        self.stop_event.set()
        self.sock.close()
        self.thread.join(timeout=2)


def parse_errors(log_text):
    return {
        "out_of_udp_buffer": len(re.findall(r"out of UDP buffer", log_text)),
        "out_of_client_buffer": len(re.findall(r"out of client buffer", log_text)),
    }


def run_udp_send_pressure(args):
    server = UDPGWProcess(
        args.binary, args.host, args.max_clients, args.max_connections,
        args.server_sndbuf, args.udp_mtu,
        os.path.join(args.artifact_dir, "wp3-client-to-udp.log")
    )
    collector = UDPCollector()
    result = {
        "mode": "client_to_udp",
        "udp_target": collector.address,
        "collector_rcvbuf_effective": collector.effective_receive_buffer,
        "levels": [],
    }
    client = None
    try:
        client = server.connect(args.client_rcvbuf)
        seed = b"WP3-UDP-SEED"
        client.sendall(frame(udpgw_packet(101, collector.address, seed)))
        if collector.wait_for(seed, 1) != 1:
            raise RuntimeError("UDPGW did not forward the initial UDP seed packet")

        for index, count in enumerate(args.udp_bursts):
            prefix = ("WP3-U%02d" % index).encode("ascii")
            payload = prefix + b"P" * (args.udp_payload_size - len(prefix))
            wire_frame = frame(udpgw_packet(101, collector.address, payload))
            started = time.monotonic()
            wire = wire_frame * count
            send_completed = True
            send_error = None
            try:
                client.sendall(wire)
            except OSError as error:
                send_completed = False
                send_error = "%s: %s" % (type(error).__name__, error)
            received = collector.wait_for(payload, count, timeout=args.phase_timeout)
            elapsed = time.monotonic() - started
            result["levels"].append({
                "requested": count,
                "send_completed": send_completed,
                "send_error": send_error,
                "udp_received": received,
                "missing": max(0, count - received),
                "send_bytes": len(wire),
                "elapsed_seconds": round(elapsed, 4),
            })
            if not send_completed:
                break
            time.sleep(0.1)
    finally:
        if client is not None:
            client.close()
        collector.close()
        log = server.stop()
        result["buffer_errors"] = parse_errors(log)
        result["process_exit_code"] = server.process.returncode
        result["log_path"] = server.log_path
    return result


def drain_tcp_responses(sock, duration):
    deadline = time.monotonic() + duration
    buffer = bytearray()
    frames = 0
    payload_bytes = 0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([sock], [], [], min(0.1, remaining))
        if not readable:
            continue
        try:
            data = sock.recv(65536)
        except (BlockingIOError, TimeoutError):
            continue
        if not data:
            break
        buffer.extend(data)
        offset = 0
        while len(buffer) - offset >= 2:
            (length,) = PACKETPROTO.unpack_from(buffer, offset)
            end = offset + 2 + length
            if end > len(buffer):
                break
            frames += 1
            payload_bytes += length
            offset = end
        if offset:
            del buffer[:offset]
    return {
        "complete_frames_read": frames,
        "framed_payload_bytes_read": payload_bytes,
        "trailing_incomplete_bytes": len(buffer),
    }


def run_client_send_pressure(args):
    server = UDPGWProcess(
        args.binary, args.host, args.max_clients, args.max_connections,
        args.server_sndbuf, args.udp_mtu,
        os.path.join(args.artifact_dir, "wp3-udp-to-client.log")
    )
    responder = UDPFloodResponder(
        args.client_replies, args.reply_payload_size,
        args.reply_batch_size, args.reply_batch_delay
    )
    result = {
        "mode": "udp_to_client",
        "udp_target": responder.address,
        "requested_replies": args.client_replies,
        "reply_payload_size": args.reply_payload_size,
        "reply_batch_size": args.reply_batch_size,
        "reply_batch_delay_seconds": args.reply_batch_delay,
    }
    client = None
    try:
        client = server.connect(args.client_rcvbuf)
        client.sendall(frame(udpgw_packet(202, responder.address, b"WP3-TRIGGER")))
        if not responder.finished.wait(args.phase_timeout + 10):
            raise RuntimeError(
                "UDP flood responder timed out; sent=%d/%d"
                % (responder.sent, args.client_replies)
            )
        time.sleep(args.hold_seconds)
        client.setblocking(False)
        result["client_readback"] = drain_tcp_responses(client, args.readback_seconds)
        result["replies_sent"] = responder.sent
        result["responder_send_errors"] = responder.send_errors
    finally:
        if client is not None:
            client.close()
        responder.close()
        log = server.stop()
        result["buffer_errors"] = parse_errors(log)
        result["process_exit_code"] = server.process.returncode
        result["log_path"] = server.log_path
    return result


def parse_levels(value):
    levels = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not levels or any(item <= 0 for item in levels):
        raise argparse.ArgumentTypeError("levels must be positive comma-separated integers")
    return levels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=os.environ.get("UDPGW_BIN"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mode", choices=("all", "udp", "client"), default="all")
    parser.add_argument("--udp-bursts", type=parse_levels, default=parse_levels("256,1024,4096,16384"))
    parser.add_argument("--udp-payload-size", type=int, default=256)
    parser.add_argument("--client-replies", type=int, default=16384)
    parser.add_argument("--reply-payload-size", type=int, default=UDP_MTU)
    parser.add_argument("--reply-batch-size", type=int, default=32)
    parser.add_argument("--reply-batch-delay", type=float, default=0.001)
    parser.add_argument("--hold-seconds", type=float, default=1.0)
    parser.add_argument("--readback-seconds", type=float, default=2.0)
    parser.add_argument("--phase-timeout", type=float, default=30.0)
    parser.add_argument("--client-rcvbuf", type=int, default=4096)
    parser.add_argument("--server-sndbuf", type=int, default=4096)
    parser.add_argument("--max-clients", type=int, default=4092)
    parser.add_argument("--max-connections", type=int, default=4092)
    parser.add_argument("--udp-mtu", type=int, default=UDP_MTU)
    parser.add_argument("--artifact-dir", default="wp3-artifacts")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if not args.binary:
        parser.error("--binary or UDPGW_BIN is required")
    if args.udp_payload_size < 6 or args.udp_payload_size > args.udp_mtu:
        parser.error("--udp-payload-size must be in 6..udp-mtu")
    if args.reply_payload_size < 9 or args.reply_payload_size > args.udp_mtu:
        parser.error("--reply-payload-size must be in 9..udp-mtu")
    if min(args.client_replies, args.reply_batch_size, args.phase_timeout) <= 0:
        parser.error("reply count, batch size, and phase timeout must be positive")

    os.makedirs(args.artifact_dir, exist_ok=True)
    if args.output is None:
        args.output = os.path.join(args.artifact_dir, "wp3-buffer-pressure.json")

    results = {
        "binary": os.path.abspath(args.binary),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "configuration": {
            "udp_mtu": args.udp_mtu,
            "max_clients": args.max_clients,
            "max_connections_for_client": args.max_connections,
            "client_socket_sndbuf_requested": args.server_sndbuf,
            "client_socket_rcvbuf_requested": args.client_rcvbuf,
        },
        "measurements": [],
    }

    if args.mode in ("all", "udp"):
        results["measurements"].append(run_udp_send_pressure(args))
    if args.mode in ("all", "client"):
        results["measurements"].append(run_client_send_pressure(args))

    for measurement in results["measurements"]:
        errors = measurement["buffer_errors"]
        measurement["reproduction"] = {
            name: ("REPRODUCED" if count else "NOT_REPRODUCED")
            for name, count in errors.items()
        }

    serialized = json.dumps(results, indent=2, sort_keys=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        stream.write(serialized + "\n")
    print(serialized)
    print("WP3 result written to %s" % os.path.abspath(args.output))


if __name__ == "__main__":
    main()
