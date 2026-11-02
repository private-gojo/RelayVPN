#!/usr/bin/env python3
import socket

payload = b"RelayVPN BadVPN UDPGW runtime validation"
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(15)
sock.sendto(payload, ("198.51.100.10", 9999))
response, address = sock.recvfrom(4096)
if response != payload:
    raise SystemExit(f"UDP payload mismatch: {response!r}")
print(f"PASS: UDPGW round trip from {address[0]}:{address[1]} ({len(response)} bytes)")
