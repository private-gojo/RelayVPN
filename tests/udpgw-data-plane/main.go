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
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const payloadPrefixLen = 20
const responseTimeout = 2 * time.Minute

type options struct {
	binary string
	mappings int
	udpMTU int
	payloadSize int
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
	P50LatencyUS uint64 `json:"p50_latency_us"`
	P95LatencyUS uint64 `json:"p95_latency_us"`
	P99LatencyUS uint64 `json:"p99_latency_us"`
	MaxLatencyUS uint64 `json:"max_latency_us"`
	CompletionPPS float64 `json:"completion_pps"`
	EchoResponseMbps float64 `json:"echo_response_mbps"`
}

type report struct {
	Requested int `json:"requested_mappings"`
	LastStable int `json:"last_stable_mappings"`
	Clients int `json:"tcp_clients"`
	UDPMTU int `json:"udp_mtu"`
	PayloadSize int `json:"payload_size_bytes"`
	EchoServers int `json:"echo_servers"`
	SourceBindIPs int `json:"source_bind_ips"`
	EchoPackets uint64 `json:"echo_packets"`
	EchoErrors uint64 `json:"echo_errors"`
	PersistenceResponses int64 `json:"persistence_responses"`
	PersistenceRoundTripMS int64 `json:"persistence_round_trip_ms"`
	PersistenceP50LatencyUS uint64 `json:"persistence_p50_latency_us"`
	PersistenceP95LatencyUS uint64 `json:"persistence_p95_latency_us"`
	PersistenceP99LatencyUS uint64 `json:"persistence_p99_latency_us"`
	PersistenceMaxLatencyUS uint64 `json:"persistence_max_latency_us"`
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
	latency [2][]uint64
	payloadSize int
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
	flag.IntVar(&o.payloadSize, "payload-size", 1200, "UDP payload bytes per echo packet")
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
			buf := make([]byte, 65535)
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

func payload(size int, global, round uint32) []byte {
	b := make([]byte, size)
	copy(b[:4], "RVDP")
	binary.LittleEndian.PutUint32(b[4:8], global)
	binary.LittleEndian.PutUint32(b[8:12], round)
	binary.LittleEndian.PutUint64(b[12:20], uint64(time.Now().UnixNano()))
	for i := payloadPrefixLen; i < len(b); i++ {
		b[i] = byte((uint64(global)*31 + uint64(i)*17 + uint64(round)*13) & 0xff)
	}
	return b
}

func validatePayload(got []byte, global, round uint32) (uint64, error) {
	if len(got) < payloadPrefixLen || string(got[:4]) != "RVDP" ||
		binary.LittleEndian.Uint32(got[4:8]) != global ||
		binary.LittleEndian.Uint32(got[8:12]) != round {
		return 0, errors.New("payload identity mismatch")
	}
	sentAt := binary.LittleEndian.Uint64(got[12:20])
	if sentAt == 0 {
		return 0, errors.New("missing payload timestamp")
	}
	for i := payloadPrefixLen; i < len(got); i++ {
		want := byte((uint64(global)*31 + uint64(i)*17 + uint64(round)*13) & 0xff)
		if got[i] != want {
			return 0, fmt.Errorf("payload corruption at byte %d", i)
		}
	}
	return sentAt, nil
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

func packet(m mapping, conid uint16, round uint32, payloadSize int) []byte {
	bodyLen := 3 + 6 + payloadSize
	out := make([]byte, 2+bodyLen)
	binary.LittleEndian.PutUint16(out[:2], uint16(bodyLen))
	out[2] = 0
	binary.LittleEndian.PutUint16(out[3:5], conid)
	copy(out[5:11], m.addr[:])
	p := payload(payloadSize, m.global, round)
	copy(out[11:], p)
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
		if n < 3+6+s.payloadSize || n > 65535 {
			select { case s.err <- fmt.Errorf("invalid response frame length %d", n): default: }
			return
		}
		b := make([]byte, n)
		if _, err := io.ReadFull(s.reader, b); err != nil {
			select { case s.err <- fmt.Errorf("read response frame: %w", err): default: }
			return
		}
		id := int(binary.LittleEndian.Uint16(b[1:3]))
		if id >= len(s.maps) || string(b[3:9]) != string(s.maps[id].addr[:]) || len(b[9:]) != s.payloadSize {
			select { case s.err <- fmt.Errorf("invalid response mapping id=%d", id): default: }
			return
		}
		got := b[9:]
		round := binary.LittleEndian.Uint32(got[8:12])
		if round < 1 || round > 2 || s.seen[round-1][id] {
			select { case s.err <- fmt.Errorf("duplicate or invalid response round=%d id=%d", round, id): default: }
			return
		}
		sentAt, err := validatePayload(got, s.maps[id].global, round)
		if err != nil {
			select { case s.err <- fmt.Errorf("response payload mapping=%d: %w", id, err): default: }
			return
		}
		latency := time.Since(time.Unix(0, int64(sentAt)))
		if latency < 0 {
			select { case s.err <- fmt.Errorf("negative response latency mapping=%d", id): default: }
			return
		}
		s.latency[round-1][id] = uint64(latency.Microseconds())
		s.seen[round-1][id] = true
		c.round[round-1].Add(1)
	}
}

func startServer(binary string, clients, maxConnections, mtu int) (*exec.Cmd, int, string, <-chan error, error) {
	probe, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return nil, 0, "", nil, err }
	addr := probe.Addr().String()
	_ = probe.Close()
	cmd := exec.Command(binary, "--listen-addr", addr,
		"--max-clients", strconv.Itoa(clients),
		"--max-connections-for-client", strconv.Itoa(maxConnections),
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

func dialSessions(target string, sessions []*session, workers int) error {
	if workers < 1 { workers = 1 }
	if workers > len(sessions) { workers = len(sessions) }
	jobs := make(chan int)
	var wg sync.WaitGroup
	var firstErr error
	var errMu sync.Mutex
	for worker := 0; worker < workers; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for index := range jobs {
				conn, err := net.DialTimeout("tcp", target, 5*time.Second)
				if err != nil {
					errMu.Lock()
					if firstErr == nil { firstErr = fmt.Errorf("connect client %d: %w", index, err) }
					errMu.Unlock()
					continue
				}
				if tcp, ok := conn.(*net.TCPConn); ok { _ = tcp.SetNoDelay(true) }
				sessions[index].conn = conn
				sessions[index].reader = bufio.NewReaderSize(conn, 128)
			}
		}()
	}
	for index := range sessions { jobs <- index }
	close(jobs)
	wg.Wait()
	if firstErr != nil {
		for _, current := range sessions { if current.conn != nil { _ = current.conn.Close() } }
		return firstErr
	}
	return nil
}

func keepaliveSessions(ctx context.Context, sessions []*session) {
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				for _, current := range sessions {
					if err := writeFrames(current, keepaliveFrame()); err != nil {
						select { case current.err <- fmt.Errorf("client keepalive: %w", err): default: }
					}
				}
			}
		}
	}()
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

func latencyStats(sessions []*session, round, count int) (uint64, uint64, uint64, uint64) {
	samples := make([]uint64, 0, count)
	remaining := count
	for _, current := range sessions {
		if remaining == 0 { break }
		n := len(current.latency[round-1])
		if n > remaining { n = remaining }
		for _, sample := range current.latency[round-1][:n] {
			if sample > 0 { samples = append(samples, sample) }
		}
		remaining -= n
	}
	if len(samples) == 0 { return 0, 0, 0, 0 }
	sort.Slice(samples, func(i, j int) bool { return samples[i] < samples[j] })
	at := func(percent float64) uint64 {
		index := int(float64(len(samples))*percent + 0.999999) - 1
		if index < 0 { index = 0 }
		if index >= len(samples) { index = len(samples)-1 }
		return samples[index]
	}
	return at(0.50), at(0.95), at(0.99), samples[len(samples)-1]
}

func throughput(responses, payloadSize int, elapsed time.Duration) (float64, float64) {
	seconds := elapsed.Seconds()
	if seconds <= 0 { return 0, 0 }
	pps := float64(responses) / seconds
	mbps := float64(responses) * float64(payloadSize) * 8 / seconds / 1_000_000
	return pps, mbps
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
	if o.payloadSize < payloadPrefixLen || o.payloadSize > 65507 || o.payloadSize > o.udpMTU { return errors.New("--payload-size must be >=20, <=65507, and <= --udp-mtu") }
	clients := (o.mappings + 65535) / 65536
	maxConnections := 65536
	requiredFDs := o.mappings + clients + 512
	if o.oneClientPerMapping {
		if o.mappings > 10000 { return errors.New("--one-client-per-mapping is currently limited to 10000 clients") }
		clients = o.mappings
		maxConnections = 1
		requiredFDs = 2*o.mappings + 512
	}
	if err := raiseNoFile(requiredFDs); err != nil { return err }

	echo, err := startEchoPool()
	if err != nil { return err }
	defer echo.close()

	server, pid, target, done, err := startServer(o.binary, clients, maxConnections, o.udpMTU)
	if err != nil { return err }
	defer func() { _ = server.Process.Signal(syscall.SIGTERM); <-done }()

	errs := make(chan error, clients)
	sessions := make([]*session, clients)
	for i := range sessions {
		count := 1
		if !o.oneClientPerMapping {
			count = o.mappings - i*65536
			if count > 65536 { count = 65536 }
		}
		sessions[i] = &session{
			maps: make([]mapping, count),
			seen: [2][]bool{make([]bool, count), make([]bool, count)},
			latency: [2][]uint64{make([]uint64, count), make([]uint64, count)},
			payloadSize: o.payloadSize,
			err: errs,
		}
	}

	global := 0
	for _, current := range sessions {
		for i := range current.maps {
			current.maps[i] = makeMapping(echo.target(global), uint32(global))
			global++
		}
	}
	if err := dialSessions(target, sessions, 64); err != nil { return err }
	defer func() { for _, current := range sessions { if current.conn != nil { _ = current.conn.Close() } } }()

	c := &counters{}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for _, current := range sessions { go current.readLoop(c) }
	keepaliveSessions(ctx, sessions)

	r := report{Requested: o.mappings, Clients: clients, UDPMTU: o.udpMTU, PayloadSize: o.payloadSize,
		EchoServers: len(echo.conns), SourceBindIPs: 4, OneClientPerMapping: o.oneClientPerMapping, ServerPID: pid,
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
				frameSize := 2 + 3 + 6 + o.payloadSize
				batch := make([]byte, 0, (hi-lo)*frameSize)
				for i := lo; i < hi; i++ { batch = append(batch, packet(s.maps[i], uint16(i), 1, o.payloadSize)...)}
				if err := writeFrames(s, batch); err != nil { return recordFailure(&r, targetCount, 1, started, c, pid, echo, fmt.Errorf("send stage %d: %w", targetCount, err)) }
			}
			offset += len(s.maps)
		}
		if err := waitCount(c, 1, int64(targetCount), errs); err != nil { return recordFailure(&r, targetCount, 1, started, c, pid, echo, err) }
		elapsed := time.Since(started)
		p50, p95, p99, maxLatency := latencyStats(sessions, 1, targetCount)
		pps, mbps := throughput(targetCount, o.payloadSize, elapsed)
		entry := stage{Mappings: targetCount, RoundTripMS: elapsed.Milliseconds(),
			Responses: c.round[0].Load(), Server: procMetrics(pid), Generator: procMetrics(os.Getpid()),
			P50LatencyUS: p50, P95LatencyUS: p95, P99LatencyUS: p99, MaxLatencyUS: maxLatency,
			CompletionPPS: pps, EchoResponseMbps: mbps}
		r.Stages = append(r.Stages, entry)
		r.LastStable = targetCount
		fmt.Printf("mappings=%d round_trip_ms=%d replies=%d server_rss_kib=%d server_fd=%d\n",
			entry.Mappings, entry.RoundTripMS, entry.Responses, entry.Server.RSS, entry.Server.FD)
		previous = targetCount
	}

	persistStart := time.Now()
	for _, s := range sessions {
		frameSize := 2 + 3 + 6 + o.payloadSize
		batch := make([]byte, 0, len(s.maps)*frameSize)
		for i, m := range s.maps { batch = append(batch, packet(m, uint16(i), 2, o.payloadSize)...)}
		if err := writeFrames(s, batch); err != nil { return recordFailure(&r, o.mappings, 2, persistStart, c, pid, echo, fmt.Errorf("send persistence round: %w", err)) }
	}
	if err := waitCount(c, 2, int64(o.mappings), errs); err != nil { return recordFailure(&r, o.mappings, 2, persistStart, c, pid, echo, err) }
	r.PersistenceResponses = c.round[1].Load()
	persistElapsed := time.Since(persistStart)
	r.PersistenceRoundTripMS = persistElapsed.Milliseconds()
	r.PersistenceP50LatencyUS, r.PersistenceP95LatencyUS, r.PersistenceP99LatencyUS, r.PersistenceMaxLatencyUS = latencyStats(sessions, 2, o.mappings)
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
