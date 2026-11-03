package main

import (
	"encoding/binary"
	"net"
	"reflect"
	"testing"
)

func TestAdaptiveStages(t *testing.T) {
	want := []int{1000, 2000, 5000, 10000, 25000, 50000, 75000, 100000}
	if got := stages(100000); !reflect.DeepEqual(got, want) {
		t.Fatalf("stages(100000) = %v, want %v", got, want)
	}
	if got := stages(750); !reflect.DeepEqual(got, []int{750}) {
		t.Fatalf("stages(750) = %v", got)
	}
}

func TestPacketEncodingAndPayloadValidation(t *testing.T) {
	const size = 1200
	m := makeMapping(&net.UDPAddr{IP: net.ParseIP("127.0.0.2"), Port: 5353}, 42)
	got := packet(m, 7, 1, size)
	if n := int(binary.LittleEndian.Uint16(got[:2])); n != len(got)-2 {
		t.Fatalf("frame length = %d, want %d", n, len(got)-2)
	}
	if got[2] != 0 || binary.LittleEndian.Uint16(got[3:5]) != 7 {
		t.Fatalf("invalid UDPGW header: %v", got[:5])
	}
	if !reflect.DeepEqual(got[5:11], []byte{127, 0, 0, 2, 0x14, 0xe9}) {
		t.Fatalf("address bytes = %v", got[5:11])
	}
	sentAt, err := validatePayload(got[11:], 42, 1)
	if err != nil {
		t.Fatalf("validatePayload failed: %v", err)
	}
	if sentAt == 0 || len(got[11:]) != size {
		t.Fatalf("timestamp=%d payload_len=%d", sentAt, len(got[11:]))
	}
}

func TestValidatePayloadDetectsCorruption(t *testing.T) {
	got := payload(1200, 99, 2)
	got[len(got)-1] ^= 0xff
	if _, err := validatePayload(got, 99, 2); err == nil {
		t.Fatal("expected payload corruption to be detected")
	}
}

func TestLatencyStats(t *testing.T) {
	sessions := []*session{
		{maps: make([]mapping, 2), latency: [2][]uint64{[]uint64{10, 20}, []uint64{40, 50}}},
		{maps: make([]mapping, 1), latency: [2][]uint64{[]uint64{30}, []uint64{60}}},
	}
	p50, p95, p99, maximum := latencyStats(sessions, 1, 0, 3)
	if p50 != 20 || p95 != 30 || p99 != 30 || maximum != 30 {
		t.Fatalf("latency stats = %d/%d/%d/%d, want 20/30/30/30", p50, p95, p99, maximum)
	}
	p50, p95, p99, maximum = latencyStats(sessions, 1, 2, 3)
	if p50 != 30 || p95 != 30 || p99 != 30 || maximum != 30 {
		t.Fatalf("range latency stats = %d/%d/%d/%d, want 30/30/30/30", p50, p95, p99, maximum)
	}
}

func TestKeepaliveEncoding(t *testing.T) {
	if got := keepaliveFrame(); !reflect.DeepEqual(got, []byte{3, 0, 1, 0, 0}) {
		t.Fatalf("keepalive frame = %v", got)
	}
}
