package udpgw

import (
	"encoding/binary"
	"errors"
	"fmt"
	"net"
)

const (
	FlagKeepalive byte = 1 << 0
	FlagRebind    byte = 1 << 1
	FlagDNS       byte = 1 << 2
	FlagIPv6      byte = 1 << 3

	packetProtoHeaderSize = 2
	udpgwHeaderSize       = 3
	maxPacketProtoPayload = 0xffff
)

var (
	errMalformedFrame = errors.New("malformed UDPGW frame")
	errPayloadTooLarge = errors.New("UDP payload exceeds configured MTU")
	errPacketTooLarge = errors.New("PacketProto payload exceeds uint16 length")
)

type request struct {
	flags     byte
	connID    uint16
	origAddr  *net.UDPAddr
	payload   []byte
	keepalive bool
}

func maxFramePayload(udpMTU int) int {
	size := udpMTU + udpgwHeaderSize + 18
	if size > maxPacketProtoPayload {
		return maxPacketProtoPayload
	}
	return size
}

func parseRequest(frame []byte, udpMTU int) (request, error) {
	if len(frame) < udpgwHeaderSize {
		return request{}, errMalformedFrame
	}

	flags := frame[0]
	connID := binary.LittleEndian.Uint16(frame[1:3])
	if flags&FlagKeepalive != 0 {
		return request{flags: flags, connID: connID, keepalive: true}, nil
	}

	addressSize := 4
	if flags&FlagIPv6 != 0 {
		addressSize = 16
	}
	required := udpgwHeaderSize + addressSize + 2
	if len(frame) < required {
		return request{}, errMalformedFrame
	}

	ip := append(net.IP(nil), frame[udpgwHeaderSize:udpgwHeaderSize+addressSize]...)
	portOffset := udpgwHeaderSize + addressSize
	port := int(binary.BigEndian.Uint16(frame[portOffset : portOffset+2]))
	payload := frame[required:]
	if len(payload) > udpMTU {
		return request{}, errPayloadTooLarge
	}

	return request{
		flags:    flags,
		connID:   connID,
		origAddr: &net.UDPAddr{IP: ip, Port: port},
		payload:  append([]byte(nil), payload...),
	}, nil
}

func encodeResponseFrame(connID uint16, returnAddr *net.UDPAddr, payload []byte) ([]byte, error) {
	if returnAddr == nil || returnAddr.IP == nil {
		return nil, fmt.Errorf("%w: missing return address", errMalformedFrame)
	}

	ip4 := returnAddr.IP.To4()
	var ipBytes []byte
	var flags byte
	if ip4 != nil {
		ipBytes = ip4
	} else {
		ip6 := returnAddr.IP.To16()
		if ip6 == nil {
			return nil, fmt.Errorf("%w: invalid return IP address", errMalformedFrame)
		}
		ipBytes = ip6
		flags |= FlagIPv6
	}

	packetSize := udpgwHeaderSize + len(ipBytes) + 2 + len(payload)
	if packetSize > maxPacketProtoPayload {
		return nil, errPacketTooLarge
	}

	packet := make([]byte, packetSize)
	packet[0] = flags
	binary.LittleEndian.PutUint16(packet[1:3], connID)
	copy(packet[3:3+len(ipBytes)], ipBytes)
	portOffset := 3 + len(ipBytes)
	binary.BigEndian.PutUint16(packet[portOffset:portOffset+2], uint16(returnAddr.Port))
	copy(packet[portOffset+2:], payload)

	frame := make([]byte, packetProtoHeaderSize+len(packet))
	binary.LittleEndian.PutUint16(frame[:packetProtoHeaderSize], uint16(len(packet)))
	copy(frame[packetProtoHeaderSize:], packet)
	return frame, nil
}

func writeFull(conn net.Conn, data []byte) error {
	for len(data) > 0 {
		n, err := conn.Write(data)
		if err != nil {
			return err
		}
		if n == 0 {
			return net.ErrClosed
		}
		data = data[n:]
	}
	return nil
}

func cloneUDPAddr(addr *net.UDPAddr) *net.UDPAddr {
	if addr == nil {
		return nil
	}
	return &net.UDPAddr{
		IP:   append(net.IP(nil), addr.IP...),
		Port: addr.Port,
		Zone: addr.Zone,
	}
}

func sameUDPAddr(a, b *net.UDPAddr) bool {
	if a == nil || b == nil {
		return a == b
	}
	return a.Port == b.Port && a.Zone == b.Zone && a.IP.Equal(b.IP)
}
