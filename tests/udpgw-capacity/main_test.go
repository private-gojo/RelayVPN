package main

import (
	"bytes"
	"io"
	"net"
	"reflect"
	"testing"
	"time"
)

func TestWriteUDPGWKeepaliveFrame(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()

	done := make(chan error, 1)
	go func() {
		done <- writeUDPGWKeepalive(client)
	}()

	got := make([]byte, len(udpgwKeepaliveFrame))
	if _, err := io.ReadFull(server, got); err != nil {
		t.Fatalf("read keepalive frame: %v", err)
	}
	if !bytes.Equal(got, []byte{3, 0, 1, 0, 0}) {
		t.Fatalf("keepalive frame = %v, want [3 0 1 0 0]", got)
	}
	if err := <-done; err != nil {
		t.Fatalf("write keepalive frame: %v", err)
	}
}

func TestBuildStagesAdaptive(t *testing.T) {
	got := buildStages(100000)
	want := []int{1000, 2000, 5000, 10000, 25000, 50000, 75000, 100000}
	if !reflect.DeepEqual(got, want) { t.Fatalf("buildStages(100000) = %v, want %v", got, want) }
}

func TestBuildStagesSmallTarget(t *testing.T) {
	got := buildStages(750)
	want := []int{750}
	if !reflect.DeepEqual(got, want) { t.Fatalf("buildStages(750) = %v, want %v", got, want) }
}

func TestParseStagesRequiresAscendingAndFinalTarget(t *testing.T) {
	if _, err := parseStages("1000,500", 1000); err == nil { t.Fatal("expected descending stages to fail") }
	if _, err := parseStages("100,500", 1000); err == nil { t.Fatal("expected final stage mismatch to fail") }
	got, err := parseStages("250,1000", 1000)
	if err != nil { t.Fatalf("parseStages returned error: %v", err) }
	want := []int{250, 1000}
	if !reflect.DeepEqual(got, want) { t.Fatalf("parseStages = %v, want %v", got, want) }
}

func TestLoopbackSourcePoolCanOpenAndVerifySessions(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	accepted := make(chan net.Conn, 16)
	go func() {
		for {
			conn, acceptErr := listener.Accept()
			if acceptErr != nil { return }
			accepted <- conn
		}
	}()
	ips, err := chooseSourceIPs(listener.Addr().String(), "")
	if err != nil { t.Fatal(err) }
	if len(ips) != 8 { t.Fatalf("expected 8 loopback source addresses, got %d", len(ips)) }
	pool := &dialPool{target: listener.Addr().String(), timeout: time.Second, ips: ips}
	conns, _, err := openConnections(pool, 0, 8, 8)
	if err != nil { t.Fatalf("openConnections failed: %v", err) }
	defer func() { for _, conn := range conns { _ = conn.Close() } }()
	if len(conns) != 8 { t.Fatalf("opened %d sessions, want 8", len(conns)) }
	for range conns {
		select {
		case conn := <-accepted: defer conn.Close()
		case <-time.After(time.Second): t.Fatal("server did not accept all sessions")
		}
	}
	alive, err := countAlive(conns)
	if err != nil { t.Fatalf("countAlive failed: %v", err) }
	if alive != len(conns) { t.Fatalf("alive=%d, want %d", alive, len(conns)) }
}

func TestVerifyLimitRejectedAcceptsServerClose(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	go func() {
		for {
			conn, acceptErr := listener.Accept()
			if acceptErr != nil { return }
			_ = conn.Close()
		}
	}()
	if err := verifyLimitRejected(listener.Addr().String(), time.Second); err != nil {
		t.Fatalf("expected server close to count as rejection: %v", err)
	}
}

func TestVerifyLimitRejectedFailsWhenServerKeepsConnection(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	accepted := make(chan net.Conn, 1)
	go func() {
		conn, acceptErr := listener.Accept()
		if acceptErr == nil { accepted <- conn }
	}()
	if err := verifyLimitRejected(listener.Addr().String(), 20*time.Millisecond); err == nil {
		t.Fatal("expected a still-open connection to fail the rejection check")
	}
	select {
	case conn := <-accepted: _ = conn.Close()
	case <-time.After(time.Second): t.Fatal("server did not accept the test connection")
	}
}
