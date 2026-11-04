import atexit
import os
import socket
import shlex
import subprocess
import tempfile
import threading
import time

from client import UDPGWClient

_SERVER_PROCESS = None
_SERVER_LOG = None
_SERVER_HOST = None
_SERVER_PORT = None
_SERVER_LOG_PATH = None


def _read_server_log():
    if not _SERVER_LOG_PATH or not os.path.exists(_SERVER_LOG_PATH):
        return ""
    with open(_SERVER_LOG_PATH, "r", encoding="utf-8", errors="replace") as stream:
        return stream.read()


def start_server():
    global _SERVER_PROCESS, _SERVER_LOG, _SERVER_HOST, _SERVER_PORT, _SERVER_LOG_PATH
    if _SERVER_PROCESS is not None:
        return _SERVER_HOST, _SERVER_PORT

    binary = os.environ.get("UDPGW_BIN")
    if not binary:
        raise RuntimeError("UDPGW_BIN is not set; build badvpn-udpgw and set UDPGW_BIN")
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise RuntimeError("UDPGW_BIN is not an executable file: " + binary)

    _SERVER_HOST = os.environ.get("UDPGW_TEST_HOST", "127.0.0.1")
    configured_port = int(os.environ.get("UDPGW_TEST_PORT", "0"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((_SERVER_HOST, configured_port))
        _SERVER_PORT = probe.getsockname()[1]

    configured_log = os.environ.get("UDPGW_LOG_FILE")
    if configured_log:
        _SERVER_LOG_PATH = configured_log
        _SERVER_LOG = open(_SERVER_LOG_PATH, "w", encoding="utf-8")
    else:
        handle = tempfile.NamedTemporaryFile(prefix="udpgw-conformance-", suffix=".log", delete=False)
        _SERVER_LOG_PATH = handle.name
        _SERVER_LOG = handle

    command = [
        binary,
        "--listen-addr", "%s:%d" % (_SERVER_HOST, _SERVER_PORT),
        "--udp-mtu", "4096",
        "--max-clients", "4092",
        "--max-connections-for-client", "2",
        "--client-socket-sndbuf", "65536",
        "--loglevel", "debug",
    ]
    command.extend(shlex.split(os.environ.get("UDPGW_EXTRA_ARGS", "")))
    _SERVER_PROCESS = subprocess.Popen(
        command,
        stdout=_SERVER_LOG,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        status = _SERVER_PROCESS.poll()
        if status is not None:
            raise RuntimeError("badvpn-udpgw exited during startup (%s):\n%s" % (status, _read_server_log()))
        try:
            with socket.create_connection((_SERVER_HOST, _SERVER_PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("badvpn-udpgw did not open its TCP listener:\n" + _read_server_log())

    atexit.register(stop_server)
    return _SERVER_HOST, _SERVER_PORT


def stop_server():
    global _SERVER_PROCESS, _SERVER_LOG
    if _SERVER_PROCESS is not None:
        if _SERVER_PROCESS.poll() is None:
            _SERVER_PROCESS.terminate()
            try:
                _SERVER_PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _SERVER_PROCESS.kill()
                _SERVER_PROCESS.wait(timeout=5)
        _SERVER_PROCESS = None
    if _SERVER_LOG is not None:
        _SERVER_LOG.close()
        _SERVER_LOG = None


def new_client():
    host, port = start_server()
    return UDPGWClient.connect(host, port)


class EchoServer:
    def __init__(self, family=socket.AF_INET, host=None):
        self.family = family
        self.host = host or ("::1" if family == socket.AF_INET6 else "127.0.0.1")
        self.sock = socket.socket(family, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, 0))
        self.address = self.sock.getsockname()
        self.sock.settimeout(0.2)
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.records = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop_event.is_set():
            try:
                payload, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            with self.condition:
                self.records.append((payload, address))
                self.condition.notify_all()
            try:
                self.sock.sendto(payload, address)
            except OSError:
                if not self.stop_event.is_set():
                    raise

    def wait_for(self, payload, occurrence=0, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                matches = [address for seen, address in self.records if seen == payload]
                if len(matches) > occurrence:
                    return matches[occurrence]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("UDP echo server did not receive payload: %r" % payload)
                self.condition.wait(remaining)

    def close(self):
        self.stop_event.set()
        self.sock.close()
        self.thread.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
