#!/usr/bin/env python3
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("198.51.100.10", 9999))
print("BadVPN UDPGW echo target ready", flush=True)
while True:
    payload, address = sock.recvfrom(65535)
    sock.sendto(payload, address)
