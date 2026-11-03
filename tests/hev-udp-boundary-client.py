#!/usr/bin/env python3
import argparse
import os
import selectors
import socket
import time

PROXY_HOST = os.environ.get("HEV_BOUNDARY_PROXY_HOST", "10.200.0.1")
CONTROL_PORT = 1082
DEST_HOST = os.environ.get("HEV_BOUNDARY_DEST_HOST", "198.51.100.10")
DEST_PORT_BASE = 20000
PAYLOAD_PREFIX = b"RV6B"


def control_command(command):
    with socket.create_connection((PROXY_HOST, CONTROL_PORT), timeout=15) as conn:
        conn.settimeout(60)
        conn.sendall(command.encode("ascii") + b"\n")
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = conn.recv(128)
            if not chunk:
                break
            response.extend(chunk)
        return response.decode("ascii", "replace").strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flows", type=int, required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--wait-target", type=int, required=True)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    parser.add_argument("--settle-ms", type=int, default=2000)
    parser.add_argument("--response-seconds", type=float, default=6.0)
    parser.add_argument("--packets-per-flow", type=int, default=1)
    parser.add_argument("--payload-size", type=int, default=12)
    parser.add_argument("--expected-proxy-records", type=int)
    parser.add_argument("--send-batch-size", type=int, default=64)
    parser.add_argument("--send-batch-delay-ms", type=float, default=25.0)
    args = parser.parse_args()

    if args.flows < 1 or args.expected < 0:
        raise SystemExit("invalid flow or expected count")
    if args.send_batch_size < 1 or args.send_batch_delay_ms < 0:
        raise SystemExit("invalid send pacing settings")
    if args.packets_per_flow < 1 or args.expected > args.flows * args.packets_per_flow:
        raise SystemExit("invalid packet count")
    if args.payload_size < 12 or args.expected_proxy_records is not None and args.expected_proxy_records < 0:
        raise SystemExit("invalid payload or proxy record count")

    reset = control_command("RESET")
    if reset != "OK RESET":
        raise SystemExit(f"proxy reset failed: {reset}")

    selector = selectors.DefaultSelector()
    sockets = []
    send_errors = []
    responses = set()
    response_length_errors = []
    total_packets = args.flows * args.packets_per_flow
    expected_proxy_records = (
        args.expected if args.expected_proxy_records is None
        else args.expected_proxy_records
    )

    try:
        for index in range(args.flows):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.bind(("0.0.0.0", 0))
            selector.register(sock, selectors.EVENT_READ, index)
            sockets.append(sock)

        sent = 0
        for index, sock in enumerate(sockets):
            for sequence in range(args.packets_per_flow):
                payload = (
                    PAYLOAD_PREFIX
                    + index.to_bytes(4, "big")
                    + sequence.to_bytes(4, "big")
                    + (b"X" * (args.payload_size - 12))
                )
                try:
                    sock.sendto(payload, (DEST_HOST, DEST_PORT_BASE + index))
                except OSError as exc:
                    send_errors.append((index, sequence, str(exc)))
                sent += 1
                if (
                    sent % args.send_batch_size == 0
                    and sent < total_packets
                    and args.send_batch_delay_ms > 0
                ):
                    time.sleep(args.send_batch_delay_ms / 1000)

        result = control_command(
            f"WAIT {args.wait_target} {args.timeout_ms} {args.settle_ms}"
        )
        if not result.startswith("RECORDED "):
            raise SystemExit(f"proxy wait failed: {result}")
        recorded = int(result.split()[1])

        deadline = time.monotonic() + args.response_seconds
        while time.monotonic() < deadline:
            events = selector.select(min(0.25, max(0, deadline - time.monotonic())))
            for key, _ in events:
                try:
                    data, _ = key.fileobj.recvfrom(4096)
                except BlockingIOError:
                    continue
                if len(data) >= 12 and data[:4] == PAYLOAD_PREFIX:
                    index = int.from_bytes(data[4:8], "big")
                    sequence = int.from_bytes(data[8:12], "big")
                    if 0 <= index < args.flows and sequence < args.packets_per_flow:
                        if len(data) != args.payload_size:
                            response_length_errors.append(
                                (index, sequence, len(data), args.payload_size)
                            )
                        else:
                            responses.add((index, sequence))

        if args.expected == total_packets:
            expected_ids = {
                (index, sequence)
                for index in range(args.flows)
                for sequence in range(args.packets_per_flow)
            }
        elif args.flows == 1:
            expected_ids = {(0, sequence) for sequence in range(args.expected)}
        else:
            expected_ids = set()
        missing = sorted(expected_ids - responses)
        print(
            "BOUNDARY_RESULT "
            f"flows={args.flows} packets_per_flow={args.packets_per_flow} "
            f"sent={sent} payload_size={args.payload_size} "
            f"proxy_records={recorded} expected_proxy_records={expected_proxy_records} "
            f"responses={len(responses)} expected={args.expected} "
            f"length_errors={response_length_errors[:10]} "
            f"send_errors={len(send_errors)} missing={missing[:20]}"
        )
        if recorded != expected_proxy_records:
            raise SystemExit(
                f"proxy record mismatch: expected {expected_proxy_records}, got {recorded}"
            )
        if send_errors:
            print(f"SEND_ERRORS {send_errors[:10]}")
        if len(responses) != args.expected:
            raise SystemExit(
                f"boundary mismatch: expected {args.expected} responses, got {len(responses)}"
            )
        if expected_ids and missing:
            raise SystemExit(f"missing expected responses: {missing[:20]}")
        if response_length_errors:
            raise SystemExit(f"response length mismatch: {response_length_errors[:10]}")
        print("PASS: measured responder count matches the pinned baseline")
    finally:
        for sock in sockets:
            try:
                selector.unregister(sock)
            except Exception:
                pass
            sock.close()
        selector.close()


if __name__ == "__main__":
    main()
