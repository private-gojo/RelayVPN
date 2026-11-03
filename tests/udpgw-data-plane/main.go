package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const payloadLen = 12
const responseTimeout = 2 * time.Minute

type options struct {
	binary string
	mappings int
	udpMTU int
	report string
	oneClientPerMapping bool
}

type metrics struct {
	PID int `json:"pid,omitempty"`
	FD int `json:"fd_count,omitempty"`
	RSS uint64 `json:"rss_kib,omitempty"`
	HWM uint64 `json:"hwm_kib,omitempty"`
}

type stage struct {
	Mappings int `json:"mappings"`
	RoundTripMS int64 `json:"round_trip_ms"`
	Responses int64 `json:"responses"`
	Server metrics `json:"server"`
	Generator metrics `json:"generator"`
}

type report struct {
	Requested int `json:"requested_mappings"`
	LastStable int `json:"last_stable_mappings"`
	Clients int `json:"tcp_clients"`
	UDPMTU int `json:"udp_mtu"`
	EchoServers int `json:"echo_servers"`
	SourceBindIPs int `json:"source_bind_ips"`
	EchoPackets uint64 `json:"echo_packets"`
	EchoErrors uint64 `json:"echo_errors"`
	PersistenceResponses int64 `json:"persistence_responses"`
	PersistenceRoundTripMS int64 `json:"persistence_round_trip_ms"`
	ServerPID int `json:"server_pid"`
	OneClientPerMapping bool `json:"one_client_per_mapping"`
	FailedAt int `json:"failed_at_mappings,omitempty"`
	FailureRound int `json:"failure_round,omitempty"`
	CurrentResponses int64 `json:"current_responses,omitempty"`
	Failure string `json:"failure,omitempty"`
	Stages []stage `json:"stages"`
}

type mapping struct {
	addr [6]byte
	global uint32
}

type echoPool struct {
	conns []*net.UDPConn
	addrs []*net.UDPAddr
	packets atomic.Uint64
	errors atomic.Uint64
}

type session struct {
	conn net.Conn
	reader *bufio.Reader
	maps []mapping
	seen [2][]bool
	writeMu sync.Mutex
	err chan error
}

type counters struct {
	round [2]atomic.Int64
}

func parseOptions() options {
	var o options
	flag.StringVar(&o.binary, "binary", "", "Path to badvpn-udpgw")
	flag.IntVar(&o.mappings, "mappings", 1000, "Maximum active UDP mappings")
	flag.IntVar(&o.udpMTU, "udp-mtu", 1500, "UDP MTU for this benchmark")
	flag.StringVar(&o.report, "report", "", "Optional JSON report path")
	flag.BoolVar(&o.oneClientPerMapping, "one-client-per-mapping", false, "Use one TCP client session per active UDP mapping")
	flag.Parse()
	return o
}

func stages(maximum int) []int {
	bounds := []int{1000, 2000, 5000, 10000, 25000, 50000, 75000, 100000}
	out := make([]int, 0, len(bounds)+1)
	for _, n := range bounds {
		if n < maximum { out = append(out, n) }
	}
	if len(out) == 0 || out[len(out)-1] != maximum { out = append(out, maximum) }
	return out
}

func startEchoPool() (*echoPool, error) {
	p := &echoPool{}
	for _, ipText := range []string{"127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6", "127.0.0.7", "127.0.0.8", "127.0.0.9"} {
		ip := net.ParseIP(ipText).To4()
		port := 0
		if len(p.addrs) > 0 { port = p.addrs[0].Port }
		conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: ip, Port: port})
		if err != nil { p.close(); return nil, fmt.Errorf("listen UDP echo %s: %w", ipText, err) }
		_ = conn.SetReadBuffer(8 << 20)
		_ = conn.SetWriteBuffer(8 << 20)
		p.conns = append(p.conns, conn)
		p.addrs = append(p.addrs, conn.LocalAddr().(*net.UDPAddr))
		go func(c *net.UDPConn) {
			buf := make([]byte, 2048)
			for {
				n, peer, readErr := c.ReadFromUDP(buf)
				if readErr != nil {
					if !errors.Is(readErr, net.ErrClosed) { p.errors.Add(1) }
					return
				}
				if _, writeErr := c.WriteToUDP(buf[:n], peer); writeErr != nil { p.errors.Add(1) } else { p.packets.Add(1) }
			}
		}(conn)
	}
	return p, nil
}

func (p *echoPool) close() {
	for _, c := range p.conns { _ = c.Close() }
}

func (p *echoPool) target(i int) *net.UDPAddr { return p.addrs[i%len(p.addrs)] }

func payload(global, round uint32) [payloadLen]byte {
	var b [payloadLen]byte
	copy(b[:4], "RVDP")
	binary.LittleEndian.PutUint32(b[4:8], global)
	binary.LittleEndian.PutUint32(b[8:12], round)
	return b
}

func makeMapping(addr *net.UDPAddr, global uint32) mapping {
	var m mapping
	copy(m.addr[:4], addr.IP.To4())
	binary.BigEndian.PutUint16(m.addr[4:], uint16(addr.Port))
	m.global = global
	return m
}

func keepaliveFrame() []byte {
	return []byte{3, 0, 1, 0, 0}
}

func packet(m mapping, conid uint16, round uint32) []byte {
	bodyLen := 3 + 6 + payloadLen
	out := make([]byte, 2+bodyLen)
	binary.LittleEndian.PutUint16(out[:2], uint16(bodyLen))
	out[2] = 0
	binary.LittleEndian.PutUint16(out[3:5], conid)
	copy(out[5:11], m.addr[:])
	p := payload(m.global, round)
	copy(out[11:], p[:])
	return out
}

func writeFrames(s *session, data []byte) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_ = s.conn.SetWriteDeadline(time.Now().Add(responseTimeout))
	defer s.conn.SetWriteDeadline(time.Time{})
	for len(data) > 0 {
		n, err := s.conn.Write(data)
		if err != nil { return err }
		if n == 0 { return io.ErrShortWrite }
		data = data[n:]
	}
	return nil
}

func (s *session) keepalive(ctx context.Context) {
	go func() {
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done(): return
			case <-t.C:
				if err := writeFrames(s, keepaliveFrame()); err != nil {
					select { case s.err <- fmt.Errorf("keepalive: %w", err): default: }
					return
				}
			}
		}
	}()
}

func (s *session) readLoop(c *counters) {
	for {
		_ = s.conn.SetReadDeadline(time.Now().Add(responseTimeout))
		var lb [2]byte
		if _, err := io.ReadFull(s.reader, lb[:]); err != nil {
			select { case s.err <- fmt.Errorf("read frame length: %w", err): default: }
			return
		}
		n := int(binary.LittleEndian.Uint16(lb[:]))
		if n < 3+6+payloadLen || n > 65535 {
			select { case s.err <- fmt.Errorf("invalid response frame length %d", n): default: }
			return
		}
		b := make([]byte, n)
		if _, err := io.ReadFull(s.reader, b); err != nil {
			select { case s.err <- fmt.Errorf("read response frame: %w", err): default: }
			return
		}
		id := int(binary.LittleEndian.Uint16(b[1:3]))
		if id >= len(s.maps) || string(b[3:9]) != string(s.maps[id].addr[:]) || len(b[9:]) != payloadLen {
			select { case s.err <- fmt.Errorf("invalid response mapping id=%d", id): default: }
			return
		}
		got := b[9:]
		if string(got[:4]) != "RVDP" || binary.LittleEndian.Uint32(got[4:8]) != s.maps[id].global {
			select { case s.err <- fmt.Errorf("response payload mismatch for mapping %d", id): default: }
			return
		}
		r := binary.LittleEndian.Uint32(got[8:12])
		if r < 1 || r > 2 || s.seen[r-1][id] {
			select { case s.err <- fmt.Errorf("duplicate or invalid response round=%d id=%d", r, id): default: }
			return
		}
		want := payload(s.maps[id].global, r)
		if string(got) != string(want[:]) {
			select { case s.err <- fmt.Errorf("echo payload mismatch round=%d id=%d", r, id): default: }
			return
		}
		s.seen[r-1][id] = true
		c.round[r-1].Add(1)
	}
}

func startServer(binary string, clients, mtu int) (*exec.Cmd, int, string, <-chan error, error) {
	probe, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return nil, 0, "", nil, err }
	addr := probe.Addr().String()
	_ = probe.Close()
	cmd := exec.Command(binary, "--listen-addr", addr,
		"--max-clients", strconv.Itoa(clients),
		"--max-connections-for-client", "65536",
		"--udp-mtu", strconv.Itoa(mtu),
		"--local-udp-bind-addr", "127.0.0.10:0",
		"--local-udp-bind-addr", "127.0.0.11:0",
		"--local-udp-bind-addr", "127.0.0.12:0",
		"--local-udp-bind-addr", "127.0.0.13:0",
		"--client-socket-sndbuf", "0", "--loglevel", "error")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil { return nil, 0, "", nil, err }
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		select { case e := <-done: return nil, 0, "", nil, fmt.Errorf("UDPGW exited during startup: %w", e); default: }
		c, e := net.DialTimeout("tcp", addr, 100*time.Millisecond)
		if e == nil { _ = c.Close(); time.Sleep(100 * time.Millisecond); return cmd, cmd.Process.Pid, addr, done, nil }
		time.Sleep(20 * time.Millisecond)
	}
	_ = cmd.Process.Kill()
	<-done
	return nil, 0, "", nil, errors.New("UDPGW listener did not become ready")
}

func procMetrics(pid int) metrics {
	m := metrics{PID: pid}
	if fds, err := os.ReadDir(filepath.Join("/proc", strconv.Itoa(pid), "fd")); err == nil { m.FD = len(fds) }
	if b, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "status")); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			f := strings.Fields(line)
			if len(f) > 1 {
				switch f[0] {
				case "VmRSS:": m.RSS, _ = strconv.ParseUint(f[1], 10, 64)
				case "VmHWM:": m.HWM, _ = strconv.ParseUint(f[1], 10, 64)
				}
			}
		}
	}
	return m
}

func raiseNoFile(required int) error {
	var limit syscall.Rlimit
	if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil { return err }
	need := uint64(required)
	if limit.Cur < need && limit.Max >= need {
		limit.Cur = need
		if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil { return err }
	}
	if limit.Cur < need { return fmt.Errorf("RLIMIT_NOFILE soft=%d hard=%d required=%d", limit.Cur, limit.Max, need) }
	return nil
}

func waitCount(c *counters, round int, want int64, errs <-chan error) error {
	timer := time.NewTimer(responseTimeout)
	defer timer.Stop()
	tick := time.NewTicker(20 * time.Millisecond)
	defer tick.Stop()
	for c.round[round-1].Load() < want {
		select {
		case err := <-errs: return fmt.Errorf("round %d received %d/%d replies before reader error: %w", round, c.round[round-1].Load(), want, err)
		case <-timer.C: return fmt.Errorf("round %d timed out: %d/%d replies", round, c.round[round-1].Load(), want)
		case <-tick.C:
		}
	}
	return nil
}

func recordFailure(r *report, stageCount, round int, started time.Time, c *counters, pid int, echo *echoPool, cause error) error {
	r.FailedAt = stageCount
	r.FailureRound = round
	r.CurrentResponses = c.round[round-1].Load()
	r.Failure = cause.Error()
	r.EchoPackets = echo.packets.Load()
	r.EchoErrors = echo.errors.Load()
	r.Stages = append(r.Stages, stage{
		Mappings: stageCount,
		RoundTripMS: time.Since(started).Milliseconds(),
		Responses: r.CurrentResponses,
		Server: procMetrics(pid),
		Generator: procMetrics(os.Getpid()),
	})
	return cause
}

func writeReport(path string, r report) error {
	if path == "" { return nil }
	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil { return err }
	return os.WriteFile(path, append(b, '\n'), 0600)
}

func run(o options) error {
	if o.binary == "" { return errors.New("--binary is required") }
	if o.mappings < 1 || o.mappings > 100000 { return errors.New("--mappings must be 1..100000") }
	if o.udpMTU < 256 || o.udpMTU > 65514 { return errors.New("--udp-mtu must be 256..65514") }
	clients := (o.mappings + 65535) / 65536
	if err := raiseNoFile(o.mappings + clients + 512); err != nil { return err }

	echo, err := startEchoPool()
	if err != nil { return err }
	defer echo.close()

	server, pid, target, done, err := startServer(o.binary, clients, o.udpMTU)
	if err != nil { return err }
	defer func() { _ = server.Process.Signal(syscall.SIGTERM); <-done }()

	sessions := make([]*session, clients)
	for i := range sessions {
		conn, err := net.DialTimeout("tcp", target, 5*time.Second)
		if err != nil { return fmt.Errorf("connect client %d: %w", i, err) }
		if tcp, ok := conn.(*net.TCPConn); ok { _ = tcp.SetNoDelay(true) }
		count := o.mappings - i*65536
		if count > 65536 { count = 65536 }
		sessions[i] = &session{conn: conn, reader: bufio.NewReaderSize(conn, 1<<20),
			maps: make([]mapping, count), seen: [2][]bool{make([]bool, count), make([]bool, count)}, err: make(chan error, 1)}
	}
	defer func() { for _, s := range sessions { _ = s.conn.Close() } }()

	global := 0
	for _, s := range sessions {
		for i := range s.maps {
			s.maps[i] = makeMapping(echo.target(global), uint32(global))
			global++
		}
	}

	c := &counters{}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	errs := make(chan error, len(sessions))
	for _, s := range sessions {
		go s.readLoop(c)
		s.keepalive(ctx)
		go func(current *session) {
			select { case e := <-current.err: errs <- e; case <-ctx.Done(): }
		}(s)
	}

	r := report{Requested: o.mappings, Clients: clients, UDPMTU: o.udpMTU,
		EchoServers: len(echo.conns), SourceBindIPs: 4, ServerPID: pid,
		Stages: make([]stage, 0, len(stages(o.mappings))+1)}
	defer func() {
		if e := writeReport(o.report, r); e != nil { fmt.Fprintf(os.Stderr, "write partial report: %v\n", e) }
	}()

	previous := 0
	for _, targetCount := range stages(o.mappings) {
		started := time.Now()
		offset := 0
		for _, s := range sessions {
			lo := previous - offset
			if lo < 0 { lo = 0 }
			hi := targetCount - offset
			if hi > len(s.maps) { hi = len(s.maps) }
			if hi > lo {
				batch := make([]byte, 0, (hi-lo)*23)
				for i := lo; i < hi; i++ { batch = append(batch, packet(s.maps[i], uint16(i), 1)...)}
				if err := writeFrames(s, batch); err != nil { return recordFailure(&r, targetCount, 1, started, c, pid, echo, fmt.Errorf("send stage %d: %w", targetCount, err)) }
			}
			offset += len(s.maps)
		}
		if err := waitCount(c, 1, int64(targetCount), errs); err != nil { return recordFailure(&r, targetCount, 1, started, c, pid, echo, err) }
		entry := stage{Mappings: targetCount, RoundTripMS: time.Since(started).Milliseconds(),
			Responses: c.round[0].Load(), Server: procMetrics(pid), Generator: procMetrics(os.Getpid())}
		r.Stages = append(r.Stages, entry)
		r.LastStable = targetCount
		fmt.Printf("mappings=%d round_trip_ms=%d replies=%d server_rss_kib=%d server_fd=%d\n",
			entry.Mappings, entry.RoundTripMS, entry.Responses, entry.Server.RSS, entry.Server.FD)
		previous = targetCount
	}

	persistStart := time.Now()
	for _, s := range sessions {
		batch := make([]byte, 0, len(s.maps)*23)
		for i, m := range s.maps { batch = append(batch, packet(m, uint16(i), 2)...)}
		if err := writeFrames(s, batch); err != nil { return recordFailure(&r, o.mappings, 2, persistStart, c, pid, echo, fmt.Errorf("send persistence round: %w", err)) }
	}
	if err := waitCount(c, 2, int64(o.mappings), errs); err != nil { return recordFailure(&r, o.mappings, 2, persistStart, c, pid, echo, err) }
	r.PersistenceResponses = c.round[1].Load()
	r.PersistenceRoundTripMS = time.Since(persistStart).Milliseconds()
	r.EchoPackets, r.EchoErrors = echo.packets.Load(), echo.errors.Load()
	if r.EchoPackets < uint64(o.mappings*2) || r.EchoErrors != 0 {
		return recordFailure(&r, o.mappings, 2, persistStart, c, pid, echo, fmt.Errorf("UDP echo validation failed: packets=%d expected>=%d errors=%d", r.EchoPackets, o.mappings*2, r.EchoErrors))
	}
	if err := writeReport(o.report, r); err != nil { return err }
	fmt.Printf("PASS: active_mappings=%d clients=%d udp_mtu=%d echo_packets=%d persistence_replies=%d server_rss_kib=%d report=%s\n",
		o.mappings, clients, o.udpMTU, r.EchoPackets, r.PersistenceResponses, procMetrics(pid).RSS, o.report)
	return nil
}

func main() {
	if err := run(parseOptions()); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
}
