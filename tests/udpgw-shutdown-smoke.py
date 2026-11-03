#!/usr/bin/env python3
import signal
import subprocess
import sys
import time


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: udpgw-shutdown-smoke.py <badvpn-udpgw>")

    process = subprocess.Popen(
        [sys.argv[1], "--listen-addr", "127.0.0.1:0", "--loglevel", "error"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        time.sleep(0.5)
        if process.poll() is not None:
            output, _ = process.communicate()
            raise RuntimeError(
                "badvpn-udpgw exited during startup "
                f"(status={process.returncode}):\\n{output}"
            )

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(
                f"badvpn-udpgw graceful shutdown returned {process.returncode}:\\n{output}"
            )

        print("PASS: UDPGW server graceful shutdown exit status")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


if __name__ == "__main__":
    main()
