package main

import (
	"context"
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

type options struct {
	binary string
	target string
	maxClients int
	steps string
	workers int
	hold time.Duration
	connectTimeout time.Duration
	sourceIPs string
	reportPath string
}

type processMetrics struct {
	PID int `json:"pid,omitempty"`
	FDCount int `json:"fd_count,omitempty"`
	RSSKiB uint64 `json:"rss_kib,omitempty"`
	HWMKiB uint64 `json:"hwm_kib,omitempty"`
}

type stageResult struct {
	Clients int `json:"clients"`
	ConnectMS int64 `json:"connect_ms"`
	HoldMS int64 `json:"hold_ms"`
	Alive int `json:"alive"`
	Server processMetrics `json:"server,omitempty"`
	Generator processMetrics `json:"generator"`
}

type report struct {
	Target string `json:"target"`
	RequestedMax int `json:"requested_max_clients"`
	LastStable int `json:"last_stable_clients"`
	FailedAt int `json:"failed_at,omitempty"`
	Failure string `json:"failure,omitempty"`
	ServerPID int `json:"server_pid,omitempty"`
	NoFileSoft uint64 `json:"nofile_soft_limit"`
	NoFileHard uint64 `json:"nofile_hard_limit"`
	EphemeralPorts int `json:"ephemeral_ports_per_source_ip,omitempty"`
	SourceIPCount int `json:"source_ip_count"`
	EstimatedPortSlots int `json:"estimated_source_port_slots,omitempty"`
	Stages []stageResult `json:"stages"`
}

func parseOptions() options {
	var o options
	flag.StringVar(&o.binary, "binary", "", "Path to badvpn-udpgw; starts a local server when set")
	flag.StringVar(&o.target, "target", "", "Existing TCP target host:port; mutually exclusive with --binary")
	flag.IntVar(&o.maxClients, "max-clients", 100000, "Maximum concurrent TCP client sessions to attempt")
	flag.StringVar(&o.steps, "steps", "", "Comma-separated ascending ramp stages; default is adaptive")
	flag.IntVar(&o.workers, "workers", 128, "Maximum parallel connection attempts")
	flag.DurationVar(&o.hold, "hold", 2*time.Second, "Hold and verify each stage")
	flag.DurationVar(&o.connectTimeout, "connect-timeout", 5*time.Second, "Per-connection timeout")
	flag.StringVar(&o.sourceIPs, "source-ips", "", "Comma-separated source IPs; loopback tests auto-spread over 8 loopback IPs")
	flag.StringVar(&o.reportPath, "report", "", "Optional JSON report path")
	flag.Parse()
	return o
}

func buildStages(max int) []int {
	if max <= 0 { return nil }
	bounds := []int{1000, 2000, 5000, 10000, 25000, 50000, 75000, 100000}
	stages := make([]int, 0, len(bounds)+1)
	for _, n := range bounds {
		if n < max { stages = append(stages, n) }
	}
	if len(stages) == 0 || stages[len(stages)-1] != max {
		stages = append(stages, max)
	}
	return stages
}

func parseStages(raw string, max int) ([]int, error) {
	if strings.TrimSpace(raw) == "" { return buildStages(max), nil }
	parts := strings.Split(raw, ",")
	stages := make([]int, 0, len(parts))
	last := 0
	for _, part := range parts {
		n, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil || n <= last || n > max {
			return nil, fmt.Errorf("invalid --steps value %q: values must be positive, ascending, and <= --max-clients", part)
		}
		stages = append(stages, n)
		last = n
	}
	if stages[len(stages)-1] != max {
		return nil, fmt.Errorf("--steps must end at --max-clients (%d)", max)
	}
	return stages, nil
}

func raiseNoFileLimit(required int) (uint64, uint64, error) {
	var limit syscall.Rlimit
	if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil {
		return 0, 0, fmt.Errorf("read RLIMIT_NOFILE: %w", err)
	}
	need := uint64(required)
	if limit.Cur < need && limit.Max >= need {
		limit.Cur = need
		if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil {
			return limit.Cur, limit.Max, fmt.Errorf("raise RLIMIT_NOFILE to %d: %w", need, err)
		}
	}
	if limit.Cur < need {
		return limit.Cur, limit.Max, fmt.Errorf("RLIMIT_NOFILE too low: soft=%d hard=%d required=%d; raise the host/container limit", limit.Cur, limit.Max, need)
	}
	return limit.Cur, limit.Max, nil
}

func readEphemeralPortRange() int {
	data, err := os.ReadFile("/proc/sys/net/ipv4/ip_local_port_range")
	if err != nil { return 0 }
	fields := strings.Fields(string(data))
	if len(fields) != 2 { return 0 }
	low, e1 := strconv.Atoi(fields[0])
	high, e2 := strconv.Atoi(fields[1])
	if e1 != nil || e2 != nil || high < low { return 0 }
	return high - low + 1
}

func chooseSourceIPs(target, raw string) ([]net.IP, error) {
	var ips []net.IP
	if strings.TrimSpace(raw) != "" {
		for _, value := range strings.Split(raw, ",") {
			ip := net.ParseIP(strings.TrimSpace(value))
			if ip == nil { return nil, fmt.Errorf("invalid source IP %q", value) }
			ips = append(ips, ip)
		}
		return ips, nil
	}
	host, _, err := net.SplitHostPort(target)
	if err != nil { return nil, err }
	resolved, err := net.LookupIP(host)
	if err != nil { return nil, err }
	isLoopback := false
	for _, ip := range resolved {
		if ip.IsLoopback() { isLoopback = true; break }
	}
	if isLoopback {
		for i := byte(2); i < 10; i++ {
			ips = append(ips, net.IPv4(127, 0, 0, i))
		}
	}
	return ips, nil
}

func metricsFor(pid int) processMetrics {
	m := processMetrics{PID: pid}
	if entries, err := os.ReadDir(filepath.Join("/proc", strconv.Itoa(pid), "fd")); err == nil {
		m.FDCount = len(entries)
	}
	if data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "status")); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				switch fields[0] {
				case "VmRSS:": m.RSSKiB, _ = strconv.ParseUint(fields[1], 10, 64)
				case "VmHWM:": m.HWMKiB, _ = strconv.ParseUint(fields[1], 10, 64)
				}
			}
		}
	}
	return m
}

type dialPool struct {
	target string
	timeout time.Duration
	ips []net.IP
	nextIP atomic.Uint64
}

func (d *dialPool) dial(ctx context.Context) (net.Conn, error) {
	dialer := net.Dialer{Timeout: d.timeout}
	if len(d.ips) > 0 {
		index := d.nextIP.Add(1)-1
		dialer.LocalAddr = &net.TCPAddr{IP: d.ips[index%uint64(len(d.ips))]}
	}
	return dialer.DialContext(ctx, "tcp", d.target)
}

type dialResult struct { conn net.Conn; err error }

func openConnections(pool *dialPool, from, to, workers int) ([]net.Conn, time.Duration, error) {
	count := to-from
	if count <= 0 { return nil, 0, nil }
	if workers > count { workers = count }
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	jobs := make(chan struct{}, count)
	results := make(chan dialResult, count)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range jobs {
				conn, err := pool.dial(ctx)
				results <- dialResult{conn: conn, err: err}
			}
		}()
	}
	started := time.Now()
	for i := 0; i < count; i++ { jobs <- struct{}{} }
	close(jobs)
	wg.Wait()
	close(results)
	opened := make([]net.Conn, 0, count)
	var firstErr error
	for result := range results {
		if result.err != nil {
			if firstErr == nil { firstErr = result.err; cancel() }
			continue
		}
		opened = append(opened, result.conn)
	}
	return opened, time.Since(started), firstErr
}

func connectionAlive(conn net.Conn) (bool, error) {
	sc, ok := conn.(syscall.Conn)
	if !ok { return false, errors.New("connection does not expose a raw socket") }
	raw, err := sc.SyscallConn()
	if err != nil { return false, err }
	alive := true
	var socketErr error
	err = raw.Control(func(fd uintptr) {
		var buffer [1]byte
		n, _, recvErr := syscall.Recvfrom(int(fd), buffer[:], syscall.MSG_PEEK|syscall.MSG_DONTWAIT)
		if recvErr == nil && n == 0 { alive = false
		} else if recvErr != nil && recvErr != syscall.EAGAIN && recvErr != syscall.EWOULDBLOCK && recvErr != syscall.EINTR {
			socketErr = recvErr
		}
	})
	if err != nil { return false, err }
	if socketErr != nil { return false, socketErr }
	return alive, nil
}

func countAlive(conns []net.Conn) (int, error) {
	alive := 0
	for _, conn := range conns {
		ok, err := connectionAlive(conn)
		if err != nil { return alive, err }
		if ok { alive++ }
	}
	return alive, nil
}

func verifyLimitRejected(target string, timeout time.Duration) error {
	conn, err := net.DialTimeout("tcp", target, timeout)
	if err != nil { return fmt.Errorf("extra connection could not reach listener: %w", err) }
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(timeout))
	var one [1]byte
	_, err = conn.Read(one[:])
	if errors.Is(err, os.ErrDeadlineExceeded) {
		return errors.New("server did not close the connection above --max-clients")
	}
	if err == nil { return errors.New("server sent data instead of rejecting the extra client") }
	if errors.Is(err, io.EOF) || errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE) {
		return nil
	}
	return fmt.Errorf("unexpected error while checking client rejection: %w", err)
}

func writeReport(path string, result report) error {
	if path == "" { return nil }
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil { return err }
	return os.WriteFile(path, append(data, '\n'), 0600)
}

func run(o options) error {
	if o.maxClients < 1 || o.workers < 1 || o.connectTimeout <= 0 || o.hold < 0 {
		return errors.New("invalid limits: max-clients/workers must be positive; timeouts cannot be negative")
	}
	if (o.binary == "") == (o.target == "") {
		return errors.New("set exactly one of --binary or --target")
	}
	stages, err := parseStages(o.steps, o.maxClients)
	if err != nil { return err }
	soft, hard, err := raiseNoFileLimit(o.maxClients+256)
	if err != nil { return err }

	var server *exec.Cmd
	var serverDone chan error
	serverWaited := false
	serverPID := 0
	if o.binary != "" {
		probe, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil { return fmt.Errorf("allocate listener address: %w", err) }
		address := probe.Addr().String()
		_ = probe.Close()
		o.target = address
		server = exec.Command(o.binary,
			"--listen-addr", address,
			"--max-clients", strconv.Itoa(o.maxClients),
			"--max-connections-for-client", "1",
			"--client-socket-sndbuf", "0",
			"--loglevel", "error",
		)
		server.Stdout = os.Stdout
		server.Stderr = os.Stderr
		if err := server.Start(); err != nil { return fmt.Errorf("start UDPGW: %w", err) }
		serverPID = server.Process.Pid
		serverDone = make(chan error, 1)
		go func() { serverDone <- server.Wait() }()
		defer func() {
			if !serverWaited {
				_ = server.Process.Signal(syscall.SIGTERM)
				<-serverDone
			}
		}()
		deadline := time.Now().Add(5*time.Second)
		ready := false
		for time.Now().Before(deadline) {
			select {
			case waitErr := <-serverDone:
				serverWaited = true
				return fmt.Errorf("UDPGW exited during startup: %w", waitErr)
			default:
			}
			conn, dialErr := net.DialTimeout("tcp", o.target, 100*time.Millisecond)
			if dialErr == nil {
				_ = conn.Close()
				ready = true
				break
			}
			time.Sleep(20*time.Millisecond)
		}
		if !ready { return errors.New("UDPGW listener did not become ready") }
	}

	ips, err := chooseSourceIPs(o.target, o.sourceIPs)
	if err != nil { return fmt.Errorf("source address setup: %w", err) }
	portsPerIP := readEphemeralPortRange()
	portSlots := 0
	if portsPerIP > 0 { portSlots = portsPerIP*max(1, len(ips)) }
	result := report{
		Target: o.target, RequestedMax: o.maxClients, ServerPID: serverPID,
		NoFileSoft: soft, NoFileHard: hard, EphemeralPorts: portsPerIP,
		SourceIPCount: max(1, len(ips)), EstimatedPortSlots: portSlots,
		Stages: make([]stageResult, 0, len(stages)),
	}
	if portSlots > 0 && o.maxClients > portSlots {
		result.Failure = fmt.Sprintf("requested %d clients exceeds estimated source-port slots %d; configure more source IPs", o.maxClients, portSlots)
		_ = writeReport(o.reportPath, result)
		return errors.New(result.Failure)
	}

	pool := &dialPool{target:o.target, timeout:o.connectTimeout, ips:ips}
	conns := make([]net.Conn, 0, o.maxClients)
	defer func() { for _, conn := range conns { _ = conn.Close() } }()
	last := 0
	for _, stage := range stages {
		opened, connectDuration, dialErr := openConnections(pool, last, stage, o.workers)
		conns = append(conns, opened...)
		if dialErr != nil {
			result.FailedAt = stage
			result.Failure = fmt.Sprintf("connection ramp failed at %d/%d clients: %v", len(conns), stage, dialErr)
			break
		}
		holdStart := time.Now()
		time.Sleep(o.hold)
		alive, aliveErr := countAlive(conns)
		stageRecord := stageResult{
			Clients:len(conns), ConnectMS:connectDuration.Milliseconds(),
			HoldMS:time.Since(holdStart).Milliseconds(), Alive:alive,
			Generator:metricsFor(os.Getpid()),
		}
		if serverPID != 0 { stageRecord.Server = metricsFor(serverPID) }
		result.Stages = append(result.Stages, stageRecord)
		fmt.Printf("stage=%d connect_ms=%d alive=%d generator_rss_kib=%d generator_fd=%d server_rss_kib=%d server_fd=%d\n",
			stageRecord.Clients, stageRecord.ConnectMS, stageRecord.Alive,
			stageRecord.Generator.RSSKiB, stageRecord.Generator.FDCount,
			stageRecord.Server.RSSKiB, stageRecord.Server.FDCount)
		if aliveErr != nil || alive != stage {
			result.FailedAt = stage
			result.Failure = fmt.Sprintf("session integrity failed at stage %d: alive=%d/%d error=%v", stage, alive, stage, aliveErr)
			break
		}
		result.LastStable = stage
		last = stage
	}
	if result.Failure == "" && serverPID != 0 {
		if err := verifyLimitRejected(o.target, o.connectTimeout); err != nil {
			result.Failure = "max-clients enforcement check failed: "+err.Error()
		}
	}
	if err := writeReport(o.reportPath, result); err != nil { return fmt.Errorf("write JSON report: %w", err) }
	if result.Failure != "" {
		fmt.Printf("FAIL: last_stable=%d failed_at=%d reason=%s\n", result.LastStable, result.FailedAt, result.Failure)
		return errors.New(result.Failure)
	}
	fmt.Printf("PASS: last_stable=%d requested_max=%d stages=%d report=%s\n", result.LastStable, result.RequestedMax, len(result.Stages), o.reportPath)
	return nil
}

func main() {
	if err := run(parseOptions()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
