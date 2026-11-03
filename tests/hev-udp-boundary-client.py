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
    args = parser.parse_args()

    if args.flows < 1 or args.expected < 0 or args.expected > args.flows:
        raise SystemExit("invalid flow or expected count")

    reset = control_command("RESET")
    if reset != "OK RESET":
        raise SystemExit(f"proxy reset failed: {reset}")

    selector = selectors.DefaultSelector()
    sockets = []
    send_errors = []
    responses = set()

    try:
        for index in range(args.flows):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", 0))
            selector.register(sock, selectors.EVENT_READ, index)
            sockets.append(sock)

        for index, sock in enumerate(sockets):
            payload = PAYLOAD_PREFIX + index.to_bytes(4, "big")
            try:
                sock.sendto(payload, (DEST_HOST, DEST_PORT_BASE + index))
            except OSError as exc:
                send_errors.append((index, str(exc)))

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
                if len(data) >= 8 and data[:4] == PAYLOAD_PREFIX:
                    index = int.from_bytes(data[4:8], "big")
                    if 0 <= index < args.flows:
                        responses.add(index)

        missing = sorted(set(range(args.flows)) - responses)
        print(
            "BOUNDARY_RESULT "
            f"flows={args.flows} proxy_records={recorded} "
            f"responses={len(responses)} expected={args.expected} "
            f"send_errors={len(send_errors)} missing={missing[:20]}"
        )
        if send_errors:
            print(f"SEND_ERRORS {send_errors[:10]}")
        if len(responses) != args.expected:
            raise SystemExit(
                f"boundary mismatch: expected {args.expected} responders, got {len(responses)}"
            )
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
