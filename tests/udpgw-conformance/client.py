import select
import socket
import struct
import time

from protocol import pack_frame, pack_udpgw, unpack_udpgw


def recv_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = sock.recv(length - len(chunks))
        if not part:
            raise EOFError("UDPGW TCP stream closed before the frame completed")
        chunks.extend(part)
    return bytes(chunks)


def recv_frame(sock):
    header = recv_exact(sock, 2)
    (length,) = struct.unpack("<H", header)
    return recv_exact(sock, length)


class UDPGWClient:
    def __init__(self, sock):
        self.sock = sock

    @classmethod
    def connect(cls, host, port, timeout=3.0):
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        return cls(sock)

    def send_raw_payload(self, payload):
        self.sock.sendall(pack_frame(payload))

    def send_raw_frame(self, frame, chunk_sizes=None, delay=0.02):
        if not chunk_sizes:
            self.sock.sendall(frame)
            return
        offset = 0
        for size in chunk_sizes:
            if offset >= len(frame):
                break
            self.sock.sendall(frame[offset:offset + size])
            offset += size
            if offset < len(frame):
                time.sleep(delay)
        if offset < len(frame):
            self.sock.sendall(frame[offset:])

    def send_packet(self, flags, conid, target=None, payload=b""):
        self.send_raw_payload(pack_udpgw(flags, conid, target, payload))

    def send_frames(self, *payloads):
        self.sock.sendall(b"".join(pack_frame(payload) for payload in payloads))

    def recv_packet(self):
        return unpack_udpgw(recv_frame(self.sock))

    def has_data(self, timeout=0.2):
        readable, _, _ = select.select([self.sock], [], [], timeout)
        return bool(readable)

    def wait_closed(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.sock], [], [], 0.1)
            if not readable:
                continue
            try:
                return self.sock.recv(1) == b""
            except ConnectionResetError:
                return True
        return False

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
