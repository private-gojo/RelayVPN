#!/usr/bin/env python3
import signal
import socket

stopping = False


def stop_handler(signum, frame):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 53))
sock.settimeout(0.2)
print("WP2 DNS UDP echo stub listening on 127.0.0.1:53", flush=True)

while not stopping:
    try:
        payload, address = sock.recvfrom(65535)
    except socket.timeout:
        continue
    except OSError:
        break
    try:
        sock.sendto(payload, address)
    except OSError:
        if not stopping:
            raise

sock.close()
