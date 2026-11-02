#!/usr/bin/env python3
import socket
import struct
import threading
import time

PROXY_HOST = "10.200.0.1"
PROXY_PORT = 1080
RELAY_PORT = 1081
ECHO_HOST = "198.51.100.10"
ECHO_PORT = 9999

def recv_exact(conn, size):
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("unexpected EOF")
        data.extend(chunk)
    return bytes(data)

def socks_control():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PROXY_HOST, PROXY_PORT))
    server.listen(16)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_control, args=(conn,), daemon=True).start()

def handle_control(conn):
    try:
        with conn:
            version, count = recv_exact(conn, 2)
            if version != 5:
                return
            recv_exact(conn, count)
            conn.sendall(b"\x05\x00")

            version, command, reserved, atyp = recv_exact(conn, 4)
            if version != 5 or command != 3 or reserved != 0:
                conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            if atyp == 1:
                recv_exact(conn, 4)
            elif atyp == 3:
                recv_exact(conn, recv_exact(conn, 1)[0])
            elif atyp == 4:
                recv_exact(conn, 16)
            else:
                return
            recv_exact(conn, 2)
            response = (
                b"\x05\x00\x00\x01"
                + socket.inet_aton(PROXY_HOST)
                + struct.pack("!H", RELAY_PORT)
            )
            conn.sendall(response)
            while conn.recv(1024):
                pass
    except (ConnectionError, OSError):
        pass

def udp_relay():
    relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    relay.bind((PROXY_HOST, RELAY_PORT))
    echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    echo.bind((ECHO_HOST, ECHO_PORT))
    relay.settimeout(1)
    while True:
        try:
            packet, client = relay.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            if len(packet) < 10 or packet[:3] != b"\x00\x00\x00":
                continue
            atyp = packet[3]
            offset = 4
            if atyp == 1:
                destination = socket.inet_ntoa(packet[offset:offset + 4])
                offset += 4
            elif atyp == 3:
                length = packet[offset]
                offset += 1
                destination = packet[offset:offset + length].decode()
                offset += length
            elif atyp == 4:
                destination = socket.inet_ntop(socket.AF_INET6, packet[offset:offset + 16])
                offset += 16
            else:
                continue
            port = struct.unpack("!H", packet[offset:offset + 2])[0]
            payload = packet[offset + 2:]
            echo.sendto(payload, (destination, port))
            echo.settimeout(5)
            response, source = echo.recvfrom(65535)
            if ":" in source[0]:
                header = b"\x00\x00\x00\x04" + socket.inet_pton(socket.AF_INET6, source[0])
            else:
                header = b"\x00\x00\x00\x01" + socket.inet_aton(source[0])
            relay.sendto(header + struct.pack("!H", source[1]) + response, client)
            print(f"UDP relay {client[0]} -> {destination}:{port} ({len(payload)} bytes)", flush=True)
        except (OSError, ValueError, IndexError, struct.error):
            continue

threading.Thread(target=socks_control, daemon=True).start()
threading.Thread(target=udp_relay, daemon=True).start()
print("SOCKS5 UDP test proxy ready", flush=True)
while True:
    time.sleep(60)
