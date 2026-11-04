#!/usr/bin/env python3
import socket


LOCAL_IP = "10.200.0.2"
REMOTE_IP = "198.51.100.10"
REMOTE_PORT = 9999
STREAM_COUNT = 2


def stream_index(local_port):
    value = 2166136261
    addresses = (
        b"\x01" + socket.inet_aton(LOCAL_IP) + local_port.to_bytes(2, "big") + b"\xff",
        b"\x01" + socket.inet_aton(REMOTE_IP) + REMOTE_PORT.to_bytes(2, "big") + b"\xff",
    )
    for address in addresses:
        for byte in address:
            value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value % STREAM_COUNT


flows = {}
for port in range(20000, 21000):
    index = stream_index(port)
    flows.setdefault(index, port)
    if len(flows) == STREAM_COUNT:
        break

if len(flows) != STREAM_COUNT:
    raise SystemExit("Could not find source ports covering both UDPGW streams")

for expected_stream, port in sorted((index, port) for index, port in flows.items()):
    payload = f"RelayVPN UDPGW stream {expected_stream} source port {port}".encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((LOCAL_IP, port))
        sock.settimeout(15)
        sock.sendto(payload, (REMOTE_IP, REMOTE_PORT))
        response, address = sock.recvfrom(4096)
        if response != payload:
            raise SystemExit(f"UDP payload mismatch on stream {expected_stream}: {response!r}")
        print(
            f"PASS: UDPGW round trip stream {expected_stream}/{STREAM_COUNT} "
            f"from {address[0]}:{address[1]} ({len(response)} bytes)"
        )
    finally:
        sock.close()

print("PASS: UDPGW traffic exercised both independent client streams")
