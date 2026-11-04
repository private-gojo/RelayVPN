#!/usr/bin/env python3
import argparse
import asyncio
import ipaddress
import json
import multiprocessing as mp
import os
import resource
import signal
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

MAGIC = b"RVBG"
META = struct.Struct("<4sBIQQ")
PACKET_PREFIX = struct.Struct("<H")
UDPGW_HEADER = struct.Struct("<BH")
FLAG_KEEPALIVE = 1
BATCH_FRAMES = 32


def make_payload(kind, token, seq, timestamp_ns, size):
    if size < META.size:
        raise ValueError("payload size must be at least 25 bytes")
    return META.pack(MAGIC, ord(kind), token, seq, timestamp_ns) + b"x" * (size - META.size)


def make_packet(conid, target, payload, flags=0):
    address = ipaddress.ip_address(target[0])
    if address.version != 4:
        raise ValueError("the initial benchmark target supports IPv4 only")
    body = UDPGW_HEADER.pack(flags, conid) + address.packed + struct.pack("!H", target[1]) + payload
    return PACKET_PREFIX.pack(len(body)) + body


def make_keepalive():
    body = UDPGW_HEADER.pack(FLAG_KEEPALIVE, 0)
    return PACKET_PREFIX.pack(len(body)) + body


async def read_exactly(reader, size):
    return await reader.readexactly(size)


async def read_frames(reader, state):
    try:
        while True:
            prefix = await read_exactly(reader, 2)
            length = PACKET_PREFIX.unpack(prefix)[0]
            body = await read_exactly(reader, length)
            if len(body) < 9:
                continue
            flags, conid = UDPGW_HEADER.unpack_from(body)
            addr_len = 18 if flags & 8 else 6
            payload = body[3 + addr_len:]
            if len(payload) < META.size or payload[:4] != MAGIC:
                continue
            _, kind, token, seq, sent_ns = META.unpack_from(payload)
            now_ns = time.monotonic_ns()
            if kind == ord("A"):
                state["acks"].add(token)
                if len(state["acks"]) >= state["expected_acks"]:
                    state["ack_event"].set()
            elif kind == ord("P"):
                started = state["tunnel_pending"].pop(seq, None)
                if started is not None:
                    state["tunnel_rtt_ns"][seq] = now_ns - started
            elif kind == ord("D"):
                state["down_packets"] += 1
                state["down_bytes"] += len(payload)
                state["down_latency_ns"].append(max(0, now_ns - sent_ns))
                started = state["data_pending"].pop(seq, None)
                if started is not None:
                    state["data_rtt_ns"].append(now_ns - started)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        state["reader_closed"] = True


async def direct_reader(sock, state):
    loop = asyncio.get_running_loop()
    try:
        while True:
            payload, _ = await loop.sock_recvfrom(sock, 65535)
            if len(payload) < META.size or payload[:4] != MAGIC or payload[4] != ord("P"):
                continue
            _, _, _, seq, _ = META.unpack_from(payload)
            started = state["direct_pending"].pop(seq, None)
            if started is not None:
                state["direct_rtt_ns"][seq - (1 << 48)] = time.monotonic_ns() - started
    except (asyncio.CancelledError, OSError):
        return


async def produce_data(session, state, target, payload_size, stop_at):
    flows = session["flows"]
    if not flows:
        return
    writer = session["writer"]
    seq = 0
    cursor = 0
    while time.monotonic() < stop_at:
        for _ in range(BATCH_FRAMES):
            conid, token = flows[cursor]
            cursor = (cursor + 1) % len(flows)
            seq += 1
            sent = time.monotonic_ns()
            payload = make_payload("D", token, seq, sent, payload_size)
            writer.write(make_packet(conid, target, payload))
            state["up_sent"] += 1
            if seq % 1000 == 0:
                state["data_pending"][seq] = sent
        await writer.drain()


async def keepalive_loop(session, stop_at):
    writer = session["writer"]
    packet = make_keepalive()
    while time.monotonic() < stop_at:
        await asyncio.sleep(8)
        if time.monotonic() < stop_at:
            writer.write(packet)
            await writer.drain()


async def probe_loop(session, target, direct_sock, state, payload_size, stop_at):
    flow = session["flows"][0]
    conid, token = flow
    loop = asyncio.get_running_loop()
    seq = 0
    while time.monotonic() < stop_at:
        seq += 1
        sent = time.monotonic_ns()
        payload = make_payload("P", token, seq, sent, payload_size)
        state["tunnel_pending"][seq] = sent
        session["writer"].write(make_packet(conid, target, payload))
        await session["writer"].drain()

        direct_seq = seq + (1 << 48)
        direct_sent = time.monotonic_ns()
        direct_payload = make_payload("P", token, direct_seq, direct_sent, payload_size)
        state["direct_pending"][direct_seq] = direct_sent
        await loop.sock_sendto(direct_sock, direct_payload, target)
        await asyncio.sleep(0.05)


async def worker_async(worker_id, sessions_spec, target, payload_size, direction, duration,
                       start_event, ready_queue, result_queue):
    started_cpu = time.process_time()
    state = {
        "expected_acks": sum(len(flows) for _, flows in sessions_spec),
        "acks": set(),
        "ack_event": asyncio.Event(),
        "tunnel_pending": {},
        "direct_pending": {},
        "tunnel_rtt_ns": {},
        "direct_rtt_ns": {},
        "data_pending": {},
        "data_rtt_ns": [],
        "down_latency_ns": [],
        "down_packets": 0,
        "down_bytes": 0,
        "up_sent": 0,
        "reader_closed": False,
    }
    sessions = []
    reader_tasks = []
    direct_sock = None
    direct_task = None

    try:
        for client_index, flows in sessions_spec:
            reader, writer = await asyncio.open_connection("127.0.0.1", target[2])
            session = {"index": client_index, "flows": flows, "reader": reader, "writer": writer}
            sessions.append(session)
            reader_tasks.append(asyncio.create_task(read_frames(reader, state)))

        for session in sessions:
            writer = session["writer"]
            for conid, token in session["flows"]:
                payload = make_payload("S", token, 0, time.monotonic_ns(), payload_size)
                writer.write(make_packet(conid, target[:2], payload))
            await writer.drain()

        if state["expected_acks"]:
            await asyncio.wait_for(state["ack_event"].wait(), timeout=45)

        if worker_id == 0 and sessions and sessions[0]["flows"]:
            direct_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            direct_sock.setblocking(False)
            direct_sock.bind(("127.0.0.1", 0))
            direct_task = asyncio.create_task(direct_reader(direct_sock, state))

        cpu_before_measure = time.process_time()
        ready_queue.put({
            "worker": worker_id,
            "ready": True,
            "expected_flows": state["expected_acks"],
            "acked_flows": len(state["acks"]),
        })

        await asyncio.to_thread(start_event.wait)
        start = time.monotonic()
        stop_at = start + duration

        tasks = []
        for session in sessions:
            tasks.append(asyncio.create_task(keepalive_loop(session, stop_at)))
            if direction in ("up", "bidi"):
                tasks.append(asyncio.create_task(
                    produce_data(session, state, target[:2], payload_size, stop_at)
                ))

        if worker_id == 0 and direct_sock is not None:
            tasks.append(asyncio.create_task(
                probe_loop(sessions[0], target[:2], direct_sock, state, payload_size, stop_at)
            ))

        await asyncio.sleep(max(0, stop_at - time.monotonic()))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.2)

        result_queue.put({
            "worker": worker_id,
            "expected_flows": state["expected_acks"],
            "acked_flows": len(state["acks"]),
            "up_sent": state["up_sent"],
            "down_packets": state["down_packets"],
            "down_bytes": state["down_bytes"],
            "tunnel_rtt_ns": state["tunnel_rtt_ns"],
            "direct_rtt_ns": state["direct_rtt_ns"],
            "down_latency_ns": state["down_latency_ns"],
            "generator_cpu_seconds": time.process_time() - cpu_before_measure,
            "reader_closed": state["reader_closed"],
        })
    except Exception as exc:
        ready_queue.put({
            "worker": worker_id,
            "ready": False,
            "expected_flows": state["expected_acks"],
            "acked_flows": len(state["acks"]),
            "error": repr(exc),
        })
        result_queue.put({"worker": worker_id, "error": repr(exc)})
    finally:
        if direct_task:
            direct_task.cancel()
            await asyncio.gather(direct_task, return_exceptions=True)
        if direct_sock:
            direct_sock.close()
        for task in reader_tasks:
            task.cancel()
        await asyncio.gather(*reader_tasks, return_exceptions=True)
        for session in sessions:
            session["writer"].close()
            try:
                await session["writer"].wait_closed()
            except (ConnectionError, OSError):
                pass


def worker_entry(*args):
    asyncio.run(worker_async(*args))


def proc_snapshot(pid):
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        rss_kb = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
                break
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        ticks = int(fields[11]) + int(fields[12])
        return {"rss_kb": rss_kb, "cpu_seconds": ticks / os.sysconf("SC_CLK_TCK")}
    except (OSError, ValueError, IndexError):
        return {"rss_kb": None, "cpu_seconds": None}


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def summarize_latency(workers):
    tunnel = []
    direct = []
    added = []
    for worker in workers:
        t = worker.get("tunnel_rtt_ns", {})
        d = worker.get("direct_rtt_ns", {})
        tunnel.extend(t.values())
        direct.extend(d.values())
        if worker.get("worker") == 0:
            added.extend(t[seq] - d[seq] for seq in (t.keys() & d.keys()))
    return {
        "probe_samples_tunnel": len(tunnel),
        "probe_samples_direct": len(direct),
        "p50_added_us": None if not added else round(percentile(added, 0.50) / 1000, 3),
        "p99_added_us": None if not added else round(percentile(added, 0.99) / 1000, 3),
        "p50_tunnel_rtt_us": None if not tunnel else round(percentile(tunnel, 0.50) / 1000, 3),
        "p50_direct_rtt_us": None if not direct else round(percentile(direct, 0.50) / 1000, 3),
    }


def read_final_json(proc):
    try:
        line = proc.stdout.readline()
        if line:
            return json.loads(line)
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def run_case(args):
    if args.flows < 1 or args.clients < 1:
        raise SystemExit("flows and clients must be positive")
    if args.flows > args.clients * 65536:
        raise SystemExit("legacy conid namespace exceeded: flows must be <= clients * 65536")
    if args.payload_size < META.size:
        raise SystemExit(f"payload-size must be >= {META.size}")

    flow_lists = [[] for _ in range(args.clients)]
    for token in range(args.flows):
        client_index = token % args.clients
        conid = len(flow_lists[client_index])
        flow_lists[client_index].append((conid, token))

    worker_count = min(args.workers, args.clients)
    worker_specs = [[] for _ in range(worker_count)]
    for client_index, flows in enumerate(flow_lists):
        worker_specs[client_index % worker_count].append((client_index, flows))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        listen_port = reserve.getsockname()[1]

    target_proc = subprocess.Popen(
        [args.echo_bin, "--port", "0", "--mode", args.direction,
         "--max-flows", str(args.flows), "--payload-size", str(args.payload_size),
         "--rate-pps", str(args.down_rate_pps)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    ready_line = target_proc.stdout.readline().strip()
    if not ready_line.startswith("READY "):
        target_proc.terminate()
        raise RuntimeError(f"echo target failed to start: {ready_line}")
    udp_port = int(ready_line.split()[1])
    target = ("127.0.0.1", udp_port, listen_port)

    max_per_client = max(1, max(len(flows) for flows in flow_lists) + 8)
    command = [
        args.engine_bin,
        "--listen-addr", f"127.0.0.1:{listen_port}",
        "--max-clients", str(args.clients),
        "--max-connections-for-client", str(max_per_client),
        "--udp-mtu", str(args.payload_size),
        "--client-socket-sndbuf", "1048576",
        "--loglevel", "none",
    ]
    if args.engine == "rust":
        command.extend(["--udp-idle-timeout", "0", "--max-total-connections", str(args.flows)])

    log_file = tempfile.TemporaryFile(mode="w+t")
    server_proc = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server_proc.poll() is not None:
            target_proc.terminate()
            log_file.seek(0)
            raise RuntimeError("UDPGW exited during startup: " + log_file.read()[-4000:])
        try:
            with socket.create_connection(("127.0.0.1", listen_port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        server_proc.terminate()
        target_proc.terminate()
        raise RuntimeError("UDPGW listener did not become ready")

    server_before = proc_snapshot(server_proc.pid)
    ctx = mp.get_context("fork")
    start_event = ctx.Event()
    ready_queue = ctx.Queue()
    result_queue = ctx.Queue()
    workers = []
    for index, spec in enumerate(worker_specs):
        process = ctx.Process(
            target=worker_entry,
            args=(index, spec, target, args.payload_size, args.direction,
                  args.duration, start_event, ready_queue, result_queue),
        )
        process.start()
        workers.append(process)

    ready = []
    for _ in workers:
        try:
            ready.append(ready_queue.get(timeout=60))
        except Exception:
            ready.append({"ready": False, "error": "worker setup timeout"})

    if not all(item.get("ready") for item in ready):
        start_event.set()
        for process in workers:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
        target_proc.terminate()
        server_proc.terminate()
        return {
            "status": "setup_failed",
            "case": vars(args),
            "setup": ready,
            "server": proc_snapshot(server_proc.pid),
        }

    server_after_setup = proc_snapshot(server_proc.pid)
    cpu_before_measure = server_after_setup
    start_event.set()
    for process in workers:
        process.join(timeout=args.duration + 20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)

    results = []
    for _ in workers:
        try:
            results.append(result_queue.get(timeout=3))
        except Exception:
            results.append({"error": "worker result missing"})

    server_after = proc_snapshot(server_proc.pid)
    target_proc.send_signal(signal.SIGTERM)
    try:
        target_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        target_proc.kill()
        target_proc.wait(timeout=3)
    target_stats = read_final_json(target_proc)

    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        server_proc.wait(timeout=3)
    log_file.seek(0)
    server_log_tail = log_file.read()[-2000:]
    log_file.close()

    valid_results = [item for item in results if "error" not in item]
    up_packets = int(target_stats.get("rx_data", 0))
    down_packets = sum(int(item.get("down_packets", 0)) for item in valid_results)
    total_packets = up_packets + down_packets
    cpu_delta = None
    if cpu_before_measure["cpu_seconds"] is not None and server_after["cpu_seconds"] is not None:
        cpu_delta = max(0.0, server_after["cpu_seconds"] - cpu_before_measure["cpu_seconds"])
    rss_delta_kb = None
    if server_before["rss_kb"] is not None and server_after_setup["rss_kb"] is not None:
        rss_delta_kb = max(0, server_after_setup["rss_kb"] - server_before["rss_kb"])
    paired = summarize_latency(valid_results)
    generator_cpu = sum(float(item.get("generator_cpu_seconds", 0)) for item in valid_results)

    metrics = {
        "active_flows_requested": args.flows,
        "active_flows_confirmed": int(target_stats.get("active_flows", 0)),
        "max_flows_before_first_error": int(target_stats.get("active_flows", 0)),
        "up_packets": up_packets,
        "down_packets": down_packets,
        "up_pps": round(up_packets / args.duration, 2),
        "down_pps": round(down_packets / args.duration, 2),
        "up_mbps": round(up_packets * args.payload_size * 8 / args.duration / 1_000_000, 3),
        "down_mbps": round(down_packets * args.payload_size * 8 / args.duration / 1_000_000, 3),
        "total_pps": round(total_packets / args.duration, 2),
        "server_cpu_seconds": None if cpu_delta is None else round(cpu_delta, 6),
        "server_cpu_seconds_per_million_packets": (
            None if cpu_delta is None or total_packets == 0
            else round(cpu_delta * 1_000_000 / total_packets, 6)
        ),
        "server_rss_delta_kb": rss_delta_kb,
        "server_rss_delta_bytes_per_flow": (
            None if rss_delta_kb is None or args.flows == 0
            else round(rss_delta_kb * 1024 / max(1, int(target_stats.get("active_flows", 0))), 2)
        ),
        "generator_cpu_seconds": round(generator_cpu, 6),
        **paired,
    }
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        soft, hard = None, None
    ephemeral = "unknown"
    try:
        ephemeral = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().strip()
    except OSError:
        pass

    return {
        "status": "ok" if metrics["active_flows_confirmed"] == args.flows and not any("error" in x for x in results) else "partial",
        "case": {
            "engine": args.engine,
            "engine_bin": args.engine_bin,
            "flows": args.flows,
            "clients": args.clients,
            "payload_size": args.payload_size,
            "direction": args.direction,
            "duration_seconds": args.duration,
            "workers": worker_count,
            "down_rate_pps": args.down_rate_pps,
        },
        "environment": {
            "nproc": os.cpu_count(),
            "kernel": os.uname().release,
            "machine": os.uname().machine,
            "platform": os.uname().sysname,
            "rlimit_nofile_soft": soft,
            "rlimit_nofile_hard": hard,
            "ephemeral_port_range": ephemeral,
            "runner_type": os.environ.get("RUNNER_ENVIRONMENT", "local/unknown"),
        },
        "metrics": metrics,
        "echo_target": target_stats,
        "workers": results,
        "setup": ready,
        "server_log_tail": server_log_tail,
    }


def main():
    parser = argparse.ArgumentParser(description="Implementation-agnostic BadVPN UDPGW load generator")
    parser.add_argument("--engine", choices=("c", "rust"), required=True)
    parser.add_argument("--engine-bin", required=True)
    parser.add_argument("--echo-bin", required=True)
    parser.add_argument("--flows", type=int, required=True)
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--payload-size", type=int, choices=(64, 512, 1200), required=True)
    parser.add_argument("--direction", choices=("up", "down", "bidi"), required=True)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    parser.add_argument("--down-rate-pps", type=int, default=0)
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    result = run_case(args)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output == "-":
        print(rendered)
    else:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
