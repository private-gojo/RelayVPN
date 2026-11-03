#!/usr/bin/env python3
import fcntl
import os
import signal
import struct
import subprocess
import sys
import time

TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
TUN_NAME = b"rvfd-smoke%d"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: tunfd-runtime-smoke.py <badvpn-tun2socks>")

    tun_bin = sys.argv[1]
    tun_fd = os.open("/dev/net/tun", os.O_RDWR | os.O_CLOEXEC)
    process = None

    try:
        request = struct.pack("16sH22x", TUN_NAME, IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(tun_fd, TUNSETIFF, request)

        command = [
            tun_bin,
            "--tunfd", str(tun_fd),
            "--tunmtu", "1500",
            "--netif-ipaddr", "10.0.0.2",
            "--netif-netmask", "255.255.255.0",
            "--socks-server-addr", "127.0.0.1:1",
            "--loglevel", "error",
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            pass_fds=(tun_fd,),
        )

        time.sleep(0.5)
        if process.poll() is not None:
            output, _ = process.communicate()
            raise RuntimeError(
                "tun2socks exited during TUN-FD startup "
                f"(status={process.returncode}):\n{output}"
            )

        flags = fcntl.fcntl(tun_fd, fcntl.F_GETFL)
        if not flags & os.O_NONBLOCK:
            raise RuntimeError("tun2socks did not enable O_NONBLOCK on the TUN FD")

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
        return_code = process.returncode
        process = None
        if return_code != 0:
            raise RuntimeError(f"tun2socks graceful shutdown returned {return_code}:\\n{output}")

        os.fstat(tun_fd)
        print("PASS: inherited TUN FD startup, nonblocking mode, and caller ownership")
    finally:
        if process is not None:
            process.kill()
            process.communicate()
        os.close(tun_fd)


if __name__ == "__main__":
    main()
