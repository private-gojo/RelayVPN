#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import socket
import time


def open_connection(index, args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(args.connect_timeout)
    try:
        sock.connect((args.host, args.port))
        if args.mode == "stalled":
            request = (
                b"GET /large.bin HTTP/1.1\r\n"
                b"Host: 198.51.100.10\r\n"
                b"Connection: close\r\n\r\n"
            )
            sock.sendall(request)
        return sock, None
    except OSError as exc:
        sock.close()
        return None, str(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("idle", "stalled"), required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--hold-seconds", type=float, default=10)
    parser.add_argument("--host", default="198.51.100.10")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--connect-timeout", type=float, default=15)
    args = parser.parse_args()

    started = time.monotonic()
    sockets = []
    errors = []
    workers = min(128, max(1, args.count))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(open_connection, i, args) for i in range(args.count)]
        for future in concurrent.futures.as_completed(futures):
            sock, error = future.result()
            if sock is not None:
                sockets.append(sock)
            elif error:
                errors.append(error)

    print(json.dumps({
        "ready": len(sockets),
        "requested": args.count,
        "errors": len(errors),
        "connect_elapsed_s": round(time.monotonic() - started, 3),
        "first_errors": errors[:5],
    }), flush=True)

    try:
        time.sleep(args.hold_seconds)
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
