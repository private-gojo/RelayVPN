#!/usr/bin/env python3
import asyncio
import os
import socket
import struct
import time

HOST = os.environ.get("HEV_BOUNDARY_PROXY_HOST", "10.200.0.1")
SOCKS_PORT = 1080
RELAY_PORT = 1081
CONTROL_PORT = 1082
records = {}
released = False
relay_socket = None


def valid_udp_packet(packet):
    if len(packet) < 10 or packet[:3] != b"\x00\x00\x00":
        return False
    atyp = packet[3]
    if atyp == 1:
        offset = 8
    elif atyp == 4:
        offset = 20
    elif atyp == 3:
        if len(packet) < 5:
            return False
        offset = 5 + packet[4]
    else:
        return False
    return len(packet) >= offset + 2


async def read_socks_address(reader, atyp):
    if atyp == 1:
        await reader.readexactly(4)
    elif atyp == 3:
        size = (await reader.readexactly(1))[0]
        await reader.readexactly(size)
    elif atyp == 4:
        await reader.readexactly(16)
    else:
        raise ValueError("invalid SOCKS address type")
    await reader.readexactly(2)


async def handle_socks(reader, writer):
    try:
        version, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        if version != 5 or 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        await writer.drain()

        version, command, reserved, atyp = await reader.readexactly(4)
        if version != 5 or command != 3 or reserved != 0:
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        await read_socks_address(reader, atyp)
        reply = b"\x05\x00\x00\x01" + socket.inet_aton(HOST) + struct.pack("!H", RELAY_PORT)
        writer.write(reply)
        await writer.drain()
        await reader.read()
    except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def udp_relay():
    loop = asyncio.get_running_loop()
    while True:
        packet, client = await loop.sock_recvfrom(relay_socket, 65535)
        if not valid_udp_packet(packet):
            continue
        if released:
            await loop.sock_sendto(relay_socket, packet, client)
        else:
            records.setdefault(client, []).append(packet)


async def handle_control(reader, writer):
    global released
    try:
        line = await reader.readline()
        parts = line.decode("ascii", "strict").strip().split()
        if not parts:
            writer.write(b"ERROR empty command\n")
        elif parts[0] == "RESET":
            records.clear()
            released = False
            writer.write(b"OK RESET\n")
        elif parts[0] == "WAIT" and len(parts) == 4:
            target = max(0, int(parts[1]))
            timeout_ms = max(0, int(parts[2]))
            settle_ms = max(0, int(parts[3]))
            deadline = time.monotonic() + timeout_ms / 1000
            while sum(len(packets) for packets in records.values()) < target and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            await asyncio.sleep(settle_ms / 1000)
            released = True
            snapshot = [
                (client, packet)
                for client, packets in records.items()
                for packet in packets
            ]
            loop = asyncio.get_running_loop()
            for _ in range(3):
                for client, packet in snapshot:
                    try:
                        await loop.sock_sendto(relay_socket, packet, client)
                    except OSError:
                        pass
                await asyncio.sleep(0.025)
            writer.write(f"RECORDED {len(snapshot)}\n".encode("ascii"))
        else:
            writer.write(b"ERROR invalid command\n")
        await writer.drain()
    except (ValueError, UnicodeError, ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main():
    global relay_socket
    relay_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    relay_socket.setblocking(False)
    relay_socket.bind((HOST, RELAY_PORT))

    socks_server = await asyncio.start_server(
        handle_socks, HOST, SOCKS_PORT, backlog=4096
    )
    control_server = await asyncio.start_server(handle_control, HOST, CONTROL_PORT)
    asyncio.create_task(udp_relay())
    print(
        f"READY socks={HOST}:{SOCKS_PORT} udp={HOST}:{RELAY_PORT} "
        f"control={HOST}:{CONTROL_PORT}",
        flush=True,
    )

    async with socks_server, control_server:
        await asyncio.gather(socks_server.serve_forever(), control_server.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
