import ipaddress
import os
import socket
import struct

FLAG_KEEPALIVE = 1 << 0
FLAG_REBIND = 1 << 1
FLAG_DNS = 1 << 2
FLAG_IPV6 = 1 << 3

PACKETPROTO_HEADER = struct.Struct("<H")
UDPGW_HEADER = struct.Struct("<BH")
UDP_MTU = int(os.environ.get("UDPGW_UDP_MTU_BYTES", "4096"))
UDPGW_MTU = UDP_MTU + UDPGW_HEADER.size + 18


def pack_frame(payload):
    if len(payload) > 0xFFFF:
        raise ValueError("PacketProto payload exceeds uint16 length")
    return PACKETPROTO_HEADER.pack(len(payload)) + payload


def drain_frames(buffer):
    frames = []
    offset = 0
    while len(buffer) - offset >= PACKETPROTO_HEADER.size:
        (length,) = PACKETPROTO_HEADER.unpack_from(buffer, offset)
        end = offset + PACKETPROTO_HEADER.size + length
        if end > len(buffer):
            break
        frames.append(buffer[offset + PACKETPROTO_HEADER.size:end])
        offset = end
    return frames, buffer[offset:]


def pack_udpgw(flags, conid, target=None, payload=b""):
    if not 0 <= conid <= 0xFFFF:
        raise ValueError("UDPGW connection ID must fit uint16")
    packet = bytearray(UDPGW_HEADER.pack(flags, conid))
    if target is not None:
        host, port = target[:2]
        address = ipaddress.ip_address(host)
        packet.extend(address.packed)
        if address.version == 6:
            packet[0] |= FLAG_IPV6
        packet.extend(struct.pack("!H", port))
    packet.extend(payload)
    return bytes(packet)


def unpack_udpgw(packet):
    if len(packet) < UDPGW_HEADER.size:
        raise ValueError("UDPGW response is shorter than its header")
    flags, conid = UDPGW_HEADER.unpack_from(packet)
    offset = UDPGW_HEADER.size
    if flags & FLAG_IPV6:
        address_length = 16
        if len(packet) < offset + address_length + 2:
            raise ValueError("UDPGW IPv6 response has a truncated address")
        host = str(ipaddress.IPv6Address(packet[offset:offset + address_length]))
        offset += address_length
    else:
        address_length = 4
        if len(packet) < offset + address_length + 2:
            raise ValueError("UDPGW IPv4 response has a truncated address")
        host = socket.inet_ntoa(packet[offset:offset + address_length])
        offset += address_length
    (port,) = struct.unpack_from("!H", packet, offset)
    offset += 2
    return {
        "flags": flags,
        "conid": conid,
        "target": (host, port),
        "payload": packet[offset:],
    }
