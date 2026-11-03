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

func TestPacketEncoding(t *testing.T) {
	m := makeMapping(&net.UDPAddr{IP: net.ParseIP("127.0.0.2"), Port: 5353}, 42)
	got := packet(m, 7, 1)
	if n := int(binary.LittleEndian.Uint16(got[:2])); n != len(got)-2 {
		t.Fatalf("frame length = %d, want %d", n, len(got)-2)
	}
	if got[2] != 0 || binary.LittleEndian.Uint16(got[3:5]) != 7 {
		t.Fatalf("invalid UDPGW header: %v", got[:5])
	}
	if !reflect.DeepEqual(got[5:11], []byte{127, 0, 0, 2, 0x14, 0xe9}) {
		t.Fatalf("address bytes = %v", got[5:11])
	}
	if string(got[11:15]) != "RVDP" {
		t.Fatalf("payload magic = %q", got[11:15])
	}
}

func TestKeepaliveEncoding(t *testing.T) {
	if got := keepaliveFrame(); !reflect.DeepEqual(got, []byte{3, 0, 1, 0, 0}) {
		t.Fatalf("keepalive frame = %v", got)
	}
}
