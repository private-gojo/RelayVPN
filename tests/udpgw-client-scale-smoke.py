#!/usr/bin/env python3
import argparse
import errno
import os
import select
import resource
import signal
import socket
import subprocess
import time


def process_metrics(pid):
    metrics = {"fd_count": None, "rss_kib": None}
    try:
        metrics["fd_count"] = len(os.listdir(f"/proc/{pid}/fd"))
        with open(f"/proc/{pid}/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    metrics["rss_kib"] = int(line.split()[1])
                    break
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return metrics


def main():
    parser = argparse.ArgumentParser(description="UDPGW concurrent TCP client capacity smoke test")
    parser.add_argument("binary", help="Path to badvpn-udpgw")
    parser.add_argument("--clients", type=int, default=1000)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--reject-timeout", type=float, default=5.0)
    args = parser.parse_args()

    if args.clients < 1:
        raise SystemExit("--clients must be at least 1")

    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft_limit != resource.RLIM_INFINITY and soft_limit < args.clients + 128:
        raise SystemExit(
            f"insufficient RLIMIT_NOFILE: soft={soft_limit}, "
            f"need at least {args.clients + 128}"
        )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    command = [
        args.binary,
        "--listen-addr", f"127.0.0.1:{port}",
        "--max-clients", str(args.clients),
        "--max-connections-for-client", "1",
        "--client-socket-sndbuf", "0",
        "--loglevel", "error",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    clients = []

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.stderr.read()
                raise RuntimeError(f"UDPGW exited during startup: {stderr}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
                break
            except OSError:
                time.sleep(0.02)
        else:
            raise RuntimeError("UDPGW listener did not become ready")

        time.sleep(0.1)
        for index in range(args.clients):
            sock = socket.create_connection(
                ("127.0.0.1", port), timeout=args.connect_timeout
            )
            sock.setblocking(False)
            clients.append(sock)
            if (index + 1) % 256 == 0 and process.poll() is not None:
                raise RuntimeError(
                    f"UDPGW exited while opening client {index + 1}: "
                    f"{process.stderr.read()}"
                )

        time.sleep(0.2)
        poller = select.poll()
        for sock in clients:
            poller.register(
                sock,
                select.POLLIN | select.POLLHUP | select.POLLERR,
            )
        unexpected = []
        for fd, events in poller.poll(0):
            try:
                probe_sock = socket.socket(fileno=fd)
                try:
                    if probe_sock.recv(1, socket.MSG_PEEK) == b"":
                        unexpected.append(fd)
                finally:
                    probe_sock.detach()
            except OSError:
                unexpected.append(fd)
        if unexpected:
            raise RuntimeError(
                f"{len(unexpected)} of {args.clients} configured clients were closed"
            )

        extra = None
        try:
            extra = socket.create_connection(
                ("127.0.0.1", port), timeout=args.connect_timeout
            )
            extra.setblocking(False)
            reject_poller = select.poll()
            reject_poller.register(
                extra,
                select.POLLIN | select.POLLHUP | select.POLLERR,
            )
            deadline = time.monotonic() + args.reject_timeout
            rejected = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("UDPGW exited while checking client limit")
                events = reject_poller.poll(50)
                if events:
                    try:
                        rejected = extra.recv(1, socket.MSG_PEEK) == b""
                    except OSError as error:
                        rejected = error.errno in (
                            errno.ECONNRESET, errno.ENOTCONN, errno.EPIPE
                        )
                    if rejected:
                        break
            if not rejected:
                raise RuntimeError(
                    "UDPGW did not reject the client above --max-clients"
                )
        except OSError as error:
            if error.errno not in (
                errno.ECONNREFUSED, errno.ECONNRESET, errno.EPIPE
            ):
                raise
        finally:
            if extra is not None:
                extra.close()

        metrics = process_metrics(process.pid)
        print(
            f"PASS: {args.clients} concurrent UDPGW client sessions accepted; "
            f"client limit enforced; server_fd_count={metrics['fd_count']}; "
            f"server_rss_kib={metrics['rss_kib']}; "
            f"client_rlimit_nofile={soft_limit}"
        )
    finally:
        for sock in clients:
            sock.close()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        else:
            process.communicate()


if __name__ == "__main__":
    main()
