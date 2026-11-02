#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import os
import pwd
import random
import re
import shutil
import signal
from collections import defaultdict
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IP = shutil.which("ip") or "/usr/sbin/ip"
CURL = shutil.which("curl") or "/usr/bin/curl"
SSH = shutil.which("ssh") or "/usr/bin/ssh"
SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
USER = "relayvpn-test"
HOST_IP = "10.200.0.1"
CLIENT_IP = "10.200.0.2"
TARGET_IP = "198.51.100.10"
SOCKS_PORT = 1080
SSH_PORT = 2222
HTTP_PORT = 8080
RTT_PROFILES = (0, 10, 25, 50)
CONCURRENCY = (1, 8, 64, 256)
ROUNDS = 8
TTFB_SAMPLES = 200
MEMORY_COUNTS = (16, 64, 256, 512, 1024)


def command(args, timeout=30, check=True, capture=False):
    result = subprocess.run(
        [str(x) for x in args],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        details = result.stderr.strip() if capture and result.stderr else ""
        raise RuntimeError("command failed (%s): %s %s" % (
            result.returncode, " ".join(map(str, args)), details
        ))
    return result


def proc_status(pid):
    values = {}
    with open("/proc/%d/status" % pid, encoding="utf-8") as source:
        for line in source:
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
    return values


def proc_cpu_ticks(pid):
    with open("/proc/%d/stat" % pid, encoding="utf-8") as source:
        row = source.read()
    fields = row[row.rfind(")") + 2:].split()
    return int(fields[11]) + int(fields[12])


def reset_peak_rss(pid):
    try:
        with open("/proc/%d/clear_refs" % pid, "w", encoding="ascii") as target:
            target.write("5")
        return True
    except OSError:
        return False


def wait_port(host, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("port %s:%d did not become ready" % (host, port))


class Lab:
    def __init__(self, work_dir, artifact_dir, manifest):
        self.work = Path(work_dir)
        self.artifacts = Path(artifact_dir)
        self.manifest = manifest
        self.processes = []
        self.server = None
        self.sshd = None
        self.ssh_client = None
        self.engine = None
        self.engine_arm = None
        self.engine_log = None
        self.netns = False
        self.root_link = False
        self.user_created = False
        self.large_hashes = {}

    def log_path(self, name):
        path = self.artifacts / "logs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def start_process(self, args, log_path, env=None):
        stream = open(log_path, "ab", buffering=0)
        process = subprocess.Popen(
            [str(x) for x in args],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self.processes.append((process, stream))
        return process

    def stop_process(self, process, timeout=8):
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def create_data_files(self):
        webroot = self.work / "www"
        webroot.mkdir(parents=True, exist_ok=True)
        sizes = {"large.bin": 50 * 1024 * 1024, "large10.bin": 10 * 1024 * 1024}
        rng = random.Random(0x2E2026)
        for name, size in sizes.items():
            path = webroot / name
            if not path.exists() or path.stat().st_size != size:
                with path.open("wb") as output:
                    remaining = size
                    while remaining:
                        count = min(1024 * 1024, remaining)
                        output.write(rng.randbytes(count))
                        remaining -= count
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            self.large_hashes[name] = digest.hexdigest()
        (webroot / "small.txt").write_text(
            "RelayVPN phase2e benchmark probe\n", encoding="ascii"
        )

    def setup(self):
        if os.geteuid() != 0:
            raise RuntimeError("measure.py must run as root")
        self.work.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.create_data_files()

        command([IP, "netns", "add", "cli"])
        self.netns = True
        command([IP, "link", "add", "veth-root", "type", "veth", "peer", "name", "veth-cli"])
        self.root_link = True
        command([IP, "link", "set", "veth-cli", "netns", "cli"])
        command([IP, "addr", "add", "10.200.0.1/30", "dev", "veth-root"])
        command([IP, "link", "set", "veth-root", "up"])
        command([IP, "-n", "cli", "addr", "add", "10.200.0.2/30", "dev", "veth-cli"])
        command([IP, "-n", "cli", "link", "set", "lo", "up"])
        command([IP, "-n", "cli", "link", "set", "veth-cli", "up"])
        command([IP, "addr", "add", TARGET_IP + "/32", "dev", "lo"])

        if subprocess.run(["id", USER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            command(["useradd", "--create-home", "--shell", "/bin/bash", USER])
            self.user_created = True
        command(["usermod", "--password", "*", USER])
        home = Path("/home") / USER
        ssh_dir = home / ".ssh"
        account = pwd.getpwnam(USER)
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(ssh_dir, account.pw_uid, account.pw_gid)
        os.chmod(ssh_dir, 0o700)

        key = self.work / "id_ed25519"
        if not key.exists():
            command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
        public_key = Path(str(key) + ".pub").read_text(encoding="ascii")
        authorized = ssh_dir / "authorized_keys"
        authorized.write_text(public_key, encoding="ascii")
        os.chown(authorized, account.pw_uid, account.pw_gid)
        os.chmod(authorized, 0o600)
        command(["ssh-keygen", "-A"])

        sshd_config = self.work / "sshd_config"
        sshd_config.write_text(
            "Port %d\n"
            "ListenAddress 127.0.0.1\n"
            "HostKey /etc/ssh/ssh_host_ed25519_key\n"
            "PidFile %s\n"
            "AuthorizedKeysFile .ssh/authorized_keys\n"
            "PasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\n"
            "PubkeyAuthentication yes\n"
            "PermitRootLogin no\n"
            "AllowUsers %s\n"
            "AllowTcpForwarding yes\n"
            "PermitOpen any\n"
            "UsePAM no\n"
            "StrictModes yes\n"
            "LogLevel ERROR\n" % (SSH_PORT, self.work / "sshd.pid", USER),
            encoding="ascii",
        )
        Path("/run/sshd").mkdir(exist_ok=True)
        self.sshd = self.start_process(
            [SSHD, "-D", "-f", sshd_config, "-E", self.log_path("sshd.log")],
            self.log_path("sshd-process.log"),
        )
        wait_port("127.0.0.1", SSH_PORT, 30)

        known_hosts = self.work / "known_hosts"
        keyscan = command(
            ["ssh-keyscan", "-p", str(SSH_PORT), "127.0.0.1"],
            timeout=20,
            capture=True,
        )
        known_hosts.write_text(keyscan.stdout, encoding="ascii")
        if not known_hosts.stat().st_size:
            raise RuntimeError("ssh-keyscan returned no host key")

        self.ssh_client = self.start_process(
            [
                SSH, "-N",
                "-D", "%s:%d" % (HOST_IP, SOCKS_PORT),
                "-p", str(SSH_PORT),
                "-i", key,
                "-o", "UserKnownHostsFile=" + str(known_hosts),
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-o", "Compression=no",
                USER + "@127.0.0.1",
            ],
            self.log_path("ssh-client.log"),
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.ssh_client.poll() is not None:
                client_log = self.log_path("ssh-client.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                server_log = self.log_path("sshd.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(
                    "SSH client exited before SOCKS listener became ready.\n"
                    "SSH client log:\n" + client_log[-6000:] +
                    "\nsshd log:\n" + server_log[-6000:]
                )
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import socket; s=socket.create_connection(('%s',%d),1); s.close()" % (HOST_IP, SOCKS_PORT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("SSH dynamic SOCKS listener did not become ready")

        self.server = self.start_process(
            [
                sys.executable, ROOT / "server.py",
                "--root", self.work / "www",
                "--host", TARGET_IP,
                "--port", str(HTTP_PORT),
            ],
            self.log_path("http-server.log"),
        )
        wait_port(TARGET_IP, HTTP_PORT, 30)

    def apply_rtt(self, rtt_ms):
        delay = "%gms" % (rtt_ms / 2.0)
        command([IP, "-n", "cli", "link", "del", "ifb0"], check=False)
        if not rtt_ms:
            command([IP, "netns", "exec", "cli", "tc", "qdisc", "del", "dev", "tun0", "root"], check=False)
            command([IP, "netns", "exec", "cli", "tc", "qdisc", "del", "dev", "tun0", "clsact"], check=False)
            return

        command([IP, "-n", "cli", "link", "add", "ifb0", "type", "ifb"])
        command([IP, "-n", "cli", "link", "set", "ifb0", "up"])
        command([IP, "netns", "exec", "cli", "tc", "qdisc", "replace", "dev", "tun0", "root",
                 "netem", "limit", "32768", "delay", delay])
        command([IP, "netns", "exec", "cli", "tc", "qdisc", "replace", "dev", "tun0", "clsact"])
        command([IP, "netns", "exec", "cli", "tc", "filter", "replace", "dev", "tun0", "ingress",
                 "protocol", "all", "pref", "10", "matchall", "action", "mirred",
                 "egress", "redirect", "dev", "ifb0"])
        command([IP, "netns", "exec", "cli", "tc", "qdisc", "replace", "dev", "ifb0", "root",
                 "netem", "limit", "32768", "delay", delay])

        stats = command([IP, "netns", "exec", "cli", "tc", "-s", "qdisc", "show", "dev", "tun0"],
                        capture=True)
        ingress = command([IP, "netns", "exec", "cli", "tc", "-s", "filter", "show", "dev", "tun0",
                           "ingress"], capture=True)
        reverse = command([IP, "netns", "exec", "cli", "tc", "-s", "qdisc", "show", "dev", "ifb0"],
                          capture=True)
        (self.artifacts / ("netem-rtt-%dms.txt" % rtt_ms)).write_text(
            "TUN egress qdisc:\n" + stats.stdout +
            "\nTUN ingress redirect:\n" + ingress.stdout +
            "\nIFB reverse qdisc:\n" + reverse.stdout,
            encoding="utf-8",
        )

    def save_path_stats(self, label):
        sections = []
        for device in ("tun0", "ifb0"):
            qdisc = command([IP, "netns", "exec", "cli", "tc", "-s", "qdisc", "show",
                             "dev", device], check=False, capture=True)
            sections.append("=== qdisc %s ===\n%s\n" % (device, qdisc.stdout))
        ingress = command([IP, "netns", "exec", "cli", "tc", "-s", "filter", "show",
                           "dev", "tun0", "ingress"], check=False, capture=True)
        sections.append("=== tun0 ingress filters ===\n%s\n" % ingress.stdout)
        link_stats = command([IP, "-n", "cli", "-s", "link", "show", "dev", "tun0"],
                             check=False, capture=True)
        sections.append("=== tun0 link stats ===\n%s\n" % link_stats.stdout)
        safe_label = label.replace("/", "_")
        path = self.artifacts / ("path-stats-%s.txt" % safe_label)
        path.write_text("\n".join(sections), encoding="utf-8")
        return self.count_qdisc_drops(path.read_text(encoding="utf-8"))

    @staticmethod
    def count_qdisc_drops(text):
        qdisc_sections = re.findall(
            r"=== qdisc [^=]+ ===\s*(.*?)(?=\n===|\Z)", text, re.DOTALL
        )
        if not qdisc_sections:
            return None
        return sum(
            int(value)
            for section in qdisc_sections
            for value in re.findall(r"\bdropped\s+(\d+)", section)
        )

    def start_engine(self, arm):
        if self.engine is not None:
            raise RuntimeError("attempted to start an engine while another engine is active")
        name = arm["label"]
        self.engine_log = self.log_path("engine-%s.log" % name)
        if arm["kind"] == "badvpn":
            args = [
                IP, "netns", "exec", "cli", arm["binary"],
                "--tundev", "tun0",
                "--netif-ipaddr", "198.18.0.1",
                "--netif-netmask", "255.255.255.0",
                "--socks-server-addr", "%s:%d" % (HOST_IP, SOCKS_PORT),
                "--loglevel", "warning",
            ]
        else:
            config = self.work / "hev.yml"
            config.write_text(
                "tunnel:\n"
                "  name: tun0\n"
                "  mtu: 1500\n"
                "  ipv4: 198.18.0.1\n"
                "  icmp: 'off'\n"
                "socks5:\n"
                "  address: %s\n"
                "  port: %d\n"
                "  udp: 'udp'\n"
                "misc:\n"
                "  log-file: stderr\n"
                "  log-level: warn\n"
                "  connect-timeout: 15000\n"
                "  tcp-read-write-timeout: 120000\n" % (HOST_IP, SOCKS_PORT),
                encoding="ascii",
            )
            args = [IP, "netns", "exec", "cli", arm["binary"], config]

        self.engine = self.start_process(args, self.engine_log)
        self.engine_arm = arm
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.engine.poll() is not None:
                raise RuntimeError("%s engine exited during startup; see %s" % (name, self.engine_log))
            probe = subprocess.run(
                [IP, "-n", "cli", "link", "show", "tun0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("%s engine did not create tun0" % name)
        command([IP, "-n", "cli", "link", "set", "tun0", "mtu", "1500", "up"])
        command([IP, "-n", "cli", "route", "replace", "default", "dev", "tun0"])
        self.apply_rtt(0)

    def stop_engine(self):
        if self.engine is None:
            return None
        process = self.engine
        label = self.engine_arm["label"]
        self.stop_process(process, timeout=10)
        status = process.returncode
        self.engine = None
        self.engine_arm = None
        command([IP, "-n", "cli", "link", "del", "ifb0"], check=False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [IP, "-n", "cli", "link", "show", "tun0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if probe.returncode != 0:
                return status
            time.sleep(0.2)
        raise RuntimeError("%s did not release tun0 on shutdown" % label)

    def check_engine(self):
        if self.engine is None or self.engine.poll() is not None:
            label = self.engine_arm["label"] if self.engine_arm else "unknown"
            raise RuntimeError("%s engine stopped unexpectedly" % label)

    def curl_base(self):
        return [
            IP, "netns", "exec", "cli", CURL,
            "--noproxy", "*",
            "--fail", "--silent", "--show-error",
            "--connect-timeout", "10",
        ]

    def download_hash(self, path, expected, timeout=300):
        curl_args = self.curl_base() + [
            "--max-time", str(timeout),
            "--output", "-",
            "http://%s:%d/%s" % (TARGET_IP, HTTP_PORT, path),
        ]
        curl_process = subprocess.Popen(
            curl_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        hash_process = subprocess.Popen(
            ["sha256sum"], stdin=curl_process.stdout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        curl_process.stdout.close()
        try:
            digest_output, hash_error = hash_process.communicate(timeout=timeout + 10)
            curl_status = curl_process.wait(timeout=10)
            curl_error = curl_process.stderr.read().decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            curl_process.kill()
            hash_process.kill()
            curl_process.wait()
            hash_process.wait()
            return False, "transfer timeout"
        digest = digest_output.split()[0] if digest_output.strip() else ""
        if curl_status != 0:
            return False, curl_error.strip() or ("curl exit %d" % curl_status)
        if hash_process.returncode != 0 or digest != expected:
            return False, "SHA-256 mismatch"
        return True, ""

    def download_to_null(self, path, timeout=180):
        args = self.curl_base() + [
            "--max-time", str(timeout),
            "--output", "/dev/null",
            "http://%s:%d/%s" % (TARGET_IP, HTTP_PORT, path),
        ]
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def run_parallel_downloads(self, count, path, timeout=300):
        expected = self.large_hashes[path]
        started = time.monotonic()
        jobs = []
        for _ in range(count):
            curl_args = self.curl_base() + [
                "--max-time", str(timeout),
                "--output", "-",
                "http://%s:%d/%s" % (TARGET_IP, HTTP_PORT, path),
            ]
            curl_process = subprocess.Popen(
                curl_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            hash_process = subprocess.Popen(
                ["sha256sum"], stdin=curl_process.stdout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            curl_process.stdout.close()
            jobs.append((curl_process, hash_process))

        errors = 0
        error_samples = []
        for curl_process, hash_process in jobs:
            try:
                digest_output, _ = hash_process.communicate(timeout=timeout + 15)
                curl_status = curl_process.wait(timeout=10)
                curl_error = curl_process.stderr.read().decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                curl_process.kill()
                hash_process.kill()
                curl_process.wait()
                hash_process.wait()
                errors += 1
                error_samples.append("transfer timeout")
                continue
            digest = digest_output.split()[0] if digest_output.strip() else ""
            if curl_status != 0:
                errors += 1
                error_samples.append(curl_error.strip() or ("curl exit %d" % curl_status))
            elif hash_process.returncode != 0 or digest != expected:
                errors += 1
                error_samples.append("SHA-256 mismatch")

        elapsed = time.monotonic() - started
        byte_count = (Path(self.work / "www" / path).stat().st_size) * count
        mbps = (byte_count * 8 / 1_000_000) / elapsed if elapsed > 0 else 0.0
        return {
            "bytes": byte_count,
            "elapsed_ms": elapsed * 1000,
            "throughput_mbps": mbps,
            "errors": errors,
            "error_samples": error_samples[:5],
        }

    def ttfb_samples(self, mode, arm, rtt):
        values = []
        errors = 0
        for index in range(TTFB_SAMPLES):
            args = self.curl_base() + [
                "--max-time", "30",
                "--write-out", "%{time_starttransfer}",
                "--output", "/dev/null",
                "http://%s:%d/small.txt?probe=%d" % (TARGET_IP, HTTP_PORT, index),
            ]
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=35)
            if result.returncode != 0:
                errors += 1
                continue
            try:
                values.append(float(result.stdout.strip()) * 1000.0)
            except ValueError:
                errors += 1
        return values, errors

    def run_ttfb_profile(self, arm, rtt, ttfb_writer):
        for mode in ("idle", "loaded"):
            background = []
            if mode == "loaded":
                background = [
                    self.download_to_null("large10.bin", timeout=240)
                    for _ in range(8)
                ]
            values, errors = self.ttfb_samples(mode, arm, rtt)
            for process in background:
                try:
                    status = process.wait(timeout=240)
                    if status != 0:
                        errors += 1
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    errors += 1
            for index, value in enumerate(values, 1):
                ttfb_writer.writerow({
                    "arm": arm["label"],
                    "commit": arm["sha"],
                    "rtt_ms": rtt,
                    "mode": mode,
                    "sample": index,
                    "time_starttransfer_ms": "%.6f" % value,
                    "errors": errors,
                })
            print("TTFB %s RTT=%d mode=%s valid=%d/%d errors=%d" % (
                arm["label"], rtt, mode, len(values), TTFB_SAMPLES, errors
            ), flush=True)

    def run_benchmark(self):
        arms = self.manifest["arms"]
        by_label = {arm["label"]: arm for arm in arms}
        rows_path = self.artifacts / "results.csv"
        ttfb_path = self.artifacts / "ttfb.csv"
        with rows_path.open("w", newline="", encoding="utf-8") as result_file, \
             ttfb_path.open("w", newline="", encoding="utf-8") as ttfb_file:
            result_fields = [
                "arm", "commit", "rtt_ms", "concurrency", "round", "warmup",
                "bytes", "elapsed_ms", "throughput_mbps", "cpu_seconds",
                "cpu_seconds_per_gib", "rss_kib", "hwm_kib", "errors",
                "engine_exit_status", "error_samples",
            ]
            ttfb_fields = [
                "arm", "commit", "rtt_ms", "mode", "sample",
                "time_starttransfer_ms", "errors",
            ]
            writer = csv.DictWriter(result_file, fieldnames=result_fields)
            ttfb_writer = csv.DictWriter(ttfb_file, fieldnames=ttfb_fields)
            writer.writeheader()
            ttfb_writer.writeheader()

            for rtt in RTT_PROFILES:
                for arm in arms:
                    self.start_engine(arm)
                    try:
                        self.apply_rtt(rtt)
                        self.run_ttfb_profile(arm, rtt, ttfb_writer)
                        self.save_path_stats("%s-rtt%d-ttfb" % (arm["label"], rtt))
                    finally:
                        self.stop_engine()
                    ttfb_file.flush()

                for concurrency in CONCURRENCY:
                    path = "large.bin" if concurrency == 1 else "large10.bin"
                    for round_number in range(ROUNDS):
                        rotation = round_number % len(arms)
                        order = arms[rotation:] + arms[:rotation]
                        for arm in order:
                            self.start_engine(arm)
                            try:
                                self.apply_rtt(rtt)
                                self.check_engine()
                                pid = self.engine.pid
                                reset_peak_rss(pid)
                                before_status = proc_status(pid)
                                before_cpu = proc_cpu_ticks(pid)
                                result = self.run_parallel_downloads(concurrency, path)
                                self.check_engine()
                                self.save_path_stats("%s-rtt%d-c%d-round%d" % (
                                    arm["label"], rtt, concurrency, round_number + 1
                                ))
                                after_cpu = proc_cpu_ticks(pid)
                                after_status = proc_status(pid)
                                cpu_seconds = (
                                    (after_cpu - before_cpu) /
                                    float(os.sysconf("SC_CLK_TCK"))
                                )
                                cpu_per_gib = (
                                    cpu_seconds / (result["bytes"] / float(1024 ** 3))
                                    if result["bytes"] else 0.0
                                )
                                writer.writerow({
                                    "arm": arm["label"],
                                    "commit": arm["sha"],
                                    "rtt_ms": rtt,
                                    "concurrency": concurrency,
                                    "round": round_number + 1,
                                    "warmup": int(round_number == 0),
                                    "bytes": result["bytes"],
                                    "elapsed_ms": "%.3f" % result["elapsed_ms"],
                                    "throughput_mbps": "%.6f" % result["throughput_mbps"],
                                    "cpu_seconds": "%.6f" % cpu_seconds,
                                    "cpu_seconds_per_gib": "%.6f" % cpu_per_gib,
                                    "rss_kib": after_status.get("VmRSS", 0),
                                    "hwm_kib": after_status.get("VmHWM", 0),
                                    "errors": result["errors"],
                                    "engine_exit_status": "",
                                    "error_samples": " | ".join(result["error_samples"]),
                                })
                                result_file.flush()
                                print(
                                    "BENCH arm=%s RTT=%d C=%d round=%d/%d warmup=%s "
                                    "Mbps=%.2f errors=%d" % (
                                        arm["label"], rtt, concurrency,
                                        round_number + 1, ROUNDS,
                                        round_number == 0,
                                        result["throughput_mbps"], result["errors"]
                                    ),
                                    flush=True,
                                )
                            finally:
                                self.stop_engine()

        self.run_calibration(by_label["P2D"])


    def run_resumed_benchmark(self, source_dir, source_run_id):
        source = Path(source_dir)
        previous_results = source / "results.csv"
        previous_ttfb = source / "ttfb.csv"
        if not previous_results.is_file() or not previous_ttfb.is_file():
            raise RuntimeError("resume source is missing results.csv or ttfb.csv")

        def read_rows(path):
            with path.open(newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))

        old_results = read_rows(previous_results)
        old_ttfb = read_rows(previous_ttfb)
        arms = self.manifest["arms"]
        expected_rounds = set(range(1, ROUNDS + 1))
        result_groups = defaultdict(list)
        for row in old_results:
            result_groups[(int(row["rtt_ms"]), int(row["concurrency"]))].append(row)

        reusable_profiles = []
        rerun_profiles = []
        kept_results = []
        preserved_stats = set()

        for rtt in RTT_PROFILES:
            for concurrency in CONCURRENCY:
                rows = result_groups.get((rtt, concurrency), [])
                by_arm = defaultdict(list)
                for row in rows:
                    by_arm[row["arm"]].append(row)

                valid = len(rows) == len(arms) * ROUNDS
                for arm in arms:
                    arm_rows = by_arm.get(arm["label"], [])
                    rounds = {int(row["round"]) for row in arm_rows}
                    if rounds != expected_rounds or len(arm_rows) != ROUNDS:
                        valid = False
                    if any(int(row["errors"]) != 0 for row in arm_rows):
                        valid = False
                    for round_number in expected_rounds:
                        stat_name = (
                            "path-stats-%s-rtt%d-c%d-round%d.txt"
                            % (arm["label"], rtt, concurrency, round_number)
                        )
                        stat_path = source / stat_name
                        drops = (
                            self.count_qdisc_drops(stat_path.read_text(encoding="utf-8"))
                            if stat_path.is_file() else None
                        )
                        if drops is None or drops > 0:
                            valid = False

                if valid:
                    reusable_profiles.append("RTT=%d C=%d" % (rtt, concurrency))
                    kept_results.extend(rows)
                    for arm in arms:
                        for round_number in expected_rounds:
                            preserved_stats.add(
                                "path-stats-%s-rtt%d-c%d-round%d.txt"
                                % (arm["label"], rtt, concurrency, round_number)
                            )
                else:
                    rerun_profiles.append((rtt, concurrency))

        old_ttfb_groups = defaultdict(list)
        for row in old_ttfb:
            old_ttfb_groups[(row["arm"], int(row["rtt_ms"]), row["mode"])].append(row)

        kept_ttfb = []
        rerun_ttfb = []
        reused_ttfb_profiles = []
        for arm in arms:
            for rtt in RTT_PROFILES:
                stat_name = "path-stats-%s-rtt%d-ttfb.txt" % (arm["label"], rtt)
                stat_path = source / stat_name
                drops = (
                    self.count_qdisc_drops(stat_path.read_text(encoding="utf-8"))
                    if stat_path.is_file() else None
                )
                valid = drops == 0
                cell_rows = []
                for mode in ("idle", "loaded"):
                    group = old_ttfb_groups.get((arm["label"], rtt, mode), [])
                    samples = {int(row["sample"]) for row in group}
                    if len(group) != TTFB_SAMPLES or samples != set(range(1, TTFB_SAMPLES + 1)):
                        valid = False
                    if any(int(row["errors"]) != 0 for row in group):
                        valid = False
                    cell_rows.extend(group)
                if valid:
                    kept_ttfb.extend(cell_rows)
                    preserved_stats.add(stat_name)
                    reused_ttfb_profiles.append("%s RTT=%d" % (arm["label"], rtt))
                else:
                    rerun_ttfb.append((arm, rtt))

        for name in preserved_stats:
            source_path = source / name
            if source_path.is_file():
                shutil.copy2(source_path, self.artifacts / name)
        source_system_info = source / "system-info.txt"
        if source_system_info.is_file():
            shutil.copy2(source_system_info, self.artifacts / "source-system-info.txt")

        plan = {
            "source_run_id": str(source_run_id),
            "source_artifact": "relayvpn-phase2e-measurement-gate",
            "reused_profiles": reusable_profiles,
            "rerun_profiles": [
                "RTT=%d C=%d" % profile for profile in rerun_profiles
            ],
            "reused_ttfb_profiles": reused_ttfb_profiles,
            "rerun_ttfb_profiles": [
                "%s RTT=%d" % (arm["label"], rtt) for arm, rtt in rerun_ttfb
            ],
            "expected_rounds_per_arm_profile": ROUNDS,
            "expected_ttfb_samples_per_mode": TTFB_SAMPLES,
        }
        (self.artifacts / "resume-plan.json").write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8"
        )

        result_fields = [
            "arm", "commit", "rtt_ms", "concurrency", "round", "warmup",
            "bytes", "elapsed_ms", "throughput_mbps", "cpu_seconds",
            "cpu_seconds_per_gib", "rss_kib", "hwm_kib", "errors",
            "engine_exit_status", "error_samples",
        ]
        ttfb_fields = [
            "arm", "commit", "rtt_ms", "mode", "sample",
            "time_starttransfer_ms", "errors",
        ]
        rows_path = self.artifacts / "results.csv"
        ttfb_path = self.artifacts / "ttfb.csv"
        with rows_path.open("w", newline="", encoding="utf-8") as result_file, \
             ttfb_path.open("w", newline="", encoding="utf-8") as ttfb_file:
            writer = csv.DictWriter(result_file, fieldnames=result_fields)
            ttfb_writer = csv.DictWriter(ttfb_file, fieldnames=ttfb_fields)
            writer.writeheader()
            ttfb_writer.writeheader()
            writer.writerows(kept_results)
            ttfb_writer.writerows(kept_ttfb)
            result_file.flush()
            ttfb_file.flush()

            for arm, rtt in rerun_ttfb:
                self.start_engine(arm)
                try:
                    self.apply_rtt(rtt)
                    self.run_ttfb_profile(arm, rtt, ttfb_writer)
                    drops = self.save_path_stats(
                        "%s-rtt%d-ttfb" % (arm["label"], rtt)
                    )
                    if drops is None or drops > 0:
                        raise RuntimeError(
                            "invalid TTFB path: %s RTT=%d qdisc_drops=%s"
                            % (arm["label"], rtt, drops)
                        )
                    ttfb_file.flush()
                finally:
                    self.stop_engine()

            for rtt, concurrency in rerun_profiles:
                path = "large.bin" if concurrency == 1 else "large10.bin"
                for round_number in range(ROUNDS):
                    rotation = round_number % len(arms)
                    order = arms[rotation:] + arms[:rotation]
                    for arm in order:
                        self.start_engine(arm)
                        try:
                            self.apply_rtt(rtt)
                            self.check_engine()
                            pid = self.engine.pid
                            reset_peak_rss(pid)
                            before_cpu = proc_cpu_ticks(pid)
                            result = self.run_parallel_downloads(concurrency, path)
                            self.check_engine()
                            drops = self.save_path_stats(
                                "%s-rtt%d-c%d-round%d"
                                % (arm["label"], rtt, concurrency, round_number + 1)
                            )
                            if drops is None or drops > 0:
                                raise RuntimeError(
                                    "invalid emulator path: arm=%s RTT=%d C=%d "
                                    "round=%d qdisc_drops=%s"
                                    % (arm["label"], rtt, concurrency,
                                       round_number + 1, drops)
                                )
                            after_cpu = proc_cpu_ticks(pid)
                            status = proc_status(pid)
                            cpu_seconds = (
                                (after_cpu - before_cpu)
                                / float(os.sysconf("SC_CLK_TCK"))
                            )
                            cpu_per_gib = (
                                cpu_seconds / (result["bytes"] / float(1024 ** 3))
                                if result["bytes"] else 0.0
                            )
                            writer.writerow({
                                "arm": arm["label"],
                                "commit": arm["sha"],
                                "rtt_ms": rtt,
                                "concurrency": concurrency,
                                "round": round_number + 1,
                                "warmup": int(round_number == 0),
                                "bytes": result["bytes"],
                                "elapsed_ms": "%.3f" % result["elapsed_ms"],
                                "throughput_mbps": "%.6f" % result["throughput_mbps"],
                                "cpu_seconds": "%.6f" % cpu_seconds,
                                "cpu_seconds_per_gib": "%.6f" % cpu_per_gib,
                                "rss_kib": status.get("VmRSS", 0),
                                "hwm_kib": status.get("VmHWM", 0),
                                "errors": result["errors"],
                                "engine_exit_status": "",
                                "error_samples": " | ".join(result["error_samples"]),
                            })
                            result_file.flush()
                            print(
                                "RESUME BENCH arm=%s RTT=%d C=%d round=%d/%d "
                                "Mbps=%.2f errors=%d qdisc_drops=0"
                                % (arm["label"], rtt, concurrency, round_number + 1,
                                   ROUNDS, result["throughput_mbps"], result["errors"]),
                                flush=True,
                            )
                        finally:
                            self.stop_engine()

        print(
            "RESUME PLAN reused=%d rerun=%d TTFB-reused=%d TTFB-rerun=%d"
            % (len(reusable_profiles), len(rerun_profiles),
               len(reused_ttfb_profiles), len(rerun_ttfb)),
            flush=True,
        )

    def run_calibration(self, arm):
        path = "large.bin"
        output = self.artifacts / "calibration.csv"
        with output.open("w", newline="", encoding="utf-8") as target:
            fields = [
                "arm", "commit", "round", "bytes", "elapsed_ms",
                "throughput_mbps", "cpu_seconds", "cpu_seconds_per_gib",
                "rss_kib", "hwm_kib", "errors",
            ]
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            self.start_engine(arm)
            try:
                self.apply_rtt(0)
                for number in range(1, 11):
                    pid = self.engine.pid
                    reset_peak_rss(pid)
                    before_cpu = proc_cpu_ticks(pid)
                    status_before = proc_status(pid)
                    start = time.monotonic()
                    ok, error = self.download_hash(path, self.large_hashes[path])
                    elapsed = time.monotonic() - start
                    after_cpu = proc_cpu_ticks(pid)
                    status_after = proc_status(pid)
                    cpu_seconds = (
                        (after_cpu - before_cpu) / float(os.sysconf("SC_CLK_TCK"))
                    )
                    byte_count = Path(self.work / "www" / path).stat().st_size
                    mbps = (byte_count * 8 / 1_000_000) / elapsed if elapsed else 0.0
                    writer.writerow({
                        "arm": arm["label"],
                        "commit": arm["sha"],
                        "round": number,
                        "bytes": byte_count,
                        "elapsed_ms": "%.3f" % (elapsed * 1000),
                        "throughput_mbps": "%.6f" % mbps,
                        "cpu_seconds": "%.6f" % cpu_seconds,
                        "cpu_seconds_per_gib": "%.6f" % (cpu_seconds / (byte_count / float(1024 ** 3))),
                        "rss_kib": status_after.get("VmRSS", 0),
                        "hwm_kib": status_after.get("VmHWM", 0),
                        "errors": 0 if ok else 1,
                    })
                    target.flush()
                    print("CALIBRATION main run=%d/10 Mbps=%.2f ok=%s" % (
                        number, mbps, ok
                    ), flush=True)
            finally:
                self.stop_engine()

    def read_client_ready(self, process, timeout):
        import select
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            process.kill()
            process.wait()
            raise TimeoutError("memory client did not report ready before timeout")
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("memory client exited without a readiness record")
        return json.loads(line)

    def run_memory_case(self, arm, mode, count, writer):
        if self.engine is None:
            raise RuntimeError("memory case requires an active engine")
        pid = self.engine.pid
        reset_peak_rss(pid)
        baseline = proc_status(pid)
        start = time.monotonic()
        process = subprocess.Popen(
            [
                IP, "netns", "exec", "cli", sys.executable,
                ROOT / "hold_clients.py",
                "--mode", mode,
                "--count", str(count),
                "--hold-seconds", "10",
                "--connect-timeout", "15",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        ready_record = None
        samples = []
        try:
            ready_record = self.read_client_ready(process, timeout=180)
            while process.poll() is None:
                try:
                    status = proc_status(pid)
                    samples.append(status)
                except FileNotFoundError:
                    break
                time.sleep(0.15)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            try:
                samples.append(proc_status(pid))
            except FileNotFoundError:
                pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        elapsed = time.monotonic() - start
        if not samples:
            samples = [proc_status(pid)]
        ready_count = int(ready_record.get("ready", 0)) if ready_record else 0
        peak_rss = max(sample.get("VmRSS", 0) for sample in samples)
        peak_hwm = max(sample.get("VmHWM", 0) for sample in samples)
        post = samples[-1].get("VmRSS", 0)
        base_rss = baseline.get("VmRSS", 0)
        extra_peak = max(0, peak_rss - base_rss)
        extra_hwm = max(0, peak_hwm - baseline.get("VmHWM", 0))
        extra_post = max(0, post - base_rss)
        writer.writerow({
            "arm": arm["label"],
            "commit": arm["sha"],
            "mode": mode,
            "requested": count,
            "connected": ready_count,
            "client_errors": ready_record.get("errors", count) if ready_record else count,
            "elapsed_s": "%.3f" % elapsed,
            "baseline_rss_kib": base_rss,
            "peak_rss_kib": peak_rss,
            "hwm_kib": peak_hwm,
            "post_rss_kib": post,
            "peak_delta_bytes_per_connection": (
                "%.1f" % (extra_peak * 1024 / ready_count) if ready_count else ""
            ),
            "hwm_delta_bytes_per_connection": (
                "%.1f" % (extra_hwm * 1024 / ready_count) if ready_count else ""
            ),
            "post_delta_bytes_per_connection": (
                "%.1f" % (extra_post * 1024 / ready_count) if ready_count else ""
            ),
            "first_errors": json.dumps(ready_record.get("first_errors", [])) if ready_record else "[]",
        })
        print("MEMORY arm=%s mode=%s requested=%d connected=%d peak_delta=%d KiB" % (
            arm["label"], mode, count, ready_count, extra_peak
        ), flush=True)
        time.sleep(2)

    def run_memory(self):
        arms = [arm for arm in self.manifest["arms"] if arm["kind"] == "badvpn"]
        output = self.artifacts / "memory.csv"
        with output.open("w", newline="", encoding="utf-8") as target:
            fields = [
                "arm", "commit", "mode", "requested", "connected", "client_errors",
                "elapsed_s", "baseline_rss_kib", "peak_rss_kib", "hwm_kib",
                "post_rss_kib", "peak_delta_bytes_per_connection",
                "hwm_delta_bytes_per_connection", "post_delta_bytes_per_connection",
                "first_errors",
            ]
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            for arm in arms:
                self.start_engine(arm)
                try:
                    self.apply_rtt(0)
                    for count in MEMORY_COUNTS:
                        for mode in ("idle", "stalled"):
                            self.run_memory_case(arm, mode, count, writer)
                            target.flush()
                    self.run_memory_case(arm, "idle", 1100, writer)
                    target.flush()
                finally:
                    self.stop_engine()

    def cleanup(self):
        self.stop_engine()
        self.stop_process(self.ssh_client)
        self.stop_process(self.server)
        self.stop_process(self.sshd)
        for process, stream in reversed(self.processes):
            if process.poll() is None:
                self.stop_process(process)
            try:
                stream.close()
            except OSError:
                pass
        if self.netns:
            command([IP, "netns", "del", "cli"], check=False)
            self.netns = False
        if self.root_link:
            command([IP, "link", "del", "veth-root"], check=False)
            self.root_link = False
        command([IP, "addr", "del", TARGET_IP + "/32", "dev", "lo"], check=False)
        if self.user_created:
            command(["userdel", "-r", USER], check=False)
            self.user_created = False


def write_system_info(path):
    with open(path, "w", encoding="utf-8") as output:
        for label, args in (
            ("date_utc", ["date", "-u", "--iso-8601=seconds"]),
            ("uname", ["uname", "-a"]),
            ("nproc", ["nproc"]),
            ("lscpu", ["lscpu"]),
            ("os_release", ["cat", "/etc/os-release"]),
        ):
            output.write("=== %s ===\n" % label)
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=20)
            output.write(result.stdout + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--mode", choices=("benchmark", "memory", "all", "resume"), default="all")
    parser.add_argument("--resume-from")
    parser.add_argument("--source-run-id", default="unknown")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    artifacts = Path(args.artifact_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    write_system_info(artifacts / "system-info.txt")
    lab = Lab(args.work_dir, args.artifact_dir, manifest)
    try:
        lab.setup()
        if args.mode == "resume":
            if not args.resume_from:
                raise RuntimeError("--resume-from is required with --mode resume")
            lab.run_resumed_benchmark(args.resume_from, args.source_run_id)
            by_label = {arm["label"]: arm for arm in manifest["arms"]}
            lab.run_calibration(by_label["P2D"])
            lab.run_memory()
        else:
            if args.mode in ("benchmark", "all"):
                lab.run_benchmark()
            if args.mode in ("memory", "all"):
                lab.run_memory()
    finally:
        lab.cleanup()


if __name__ == "__main__":
    main()
