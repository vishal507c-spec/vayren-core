"""Single-endpoint WebSocket transport for remote EXE/APK clients.

Stdlib only (socket + ssl + threading + hashlib): no framework, bounded
queues, one controlled port. The server is a pure adapter — snapshots and
commands delegate to a :class:`BackendGateway`, so every remote command
passes through the existing safety gates and no trading logic lives here.

Wire discipline per connection::

    TCP accept → HTTP/WebSocket handshake → hello (≤ auth_timeout)
      → welcome + snapshot → subscribe/stream/commands ↔ ping/pong
      → close

Reconnects resubscribe with ``last_seq``: missed events replay from the
bounded ring when contiguous, otherwise the client gets a ``resync``
snapshot. Repeated delivery of a state-changing command replays the
cached result — a retry can never start a second session or order.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import queue
import socket
import ssl
import struct
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from remote.auth import AuthTracker, RemoteRole, TokenEntry, verify_token
from remote.gateway import BackendGateway, entry_signature, new_entries
from remote.protocol import (
    SCHEMA_VERSION,
    ProtocolError,
    decode_message,
    encode_message,
    error_envelope,
    make_envelope,
    utcnow_iso,
)
from remote.snapshot import build_snapshot, project_event

logger = logging.getLogger(__name__)

# Latency percentiles are kept over a bounded sample so a long-lived gateway
# can report p50/p95/p99 without unbounded memory (queue-overflow guard).
_LATENCY_SAMPLE_CAP = 512

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_HANDSHAKE_LIMIT = 16_384
_FRAME_LIMIT = 1_000_000
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

#: Remote command names accepted (each maps 1:1 to the headless action path).
MUTATING_ACTIONS = (
    "setup",
    "mode",
    "start",
    "stop",
    "refresh",
    "select_symbol",
    "select_broker",
    "tick",
    "server_op",
    "confirm_account",
    "confirm_live",
    "disarm_live",
    "connect_broker",
    "disconnect_broker",
    "revoke_session",
    "broker_status",
    "save_broker_credentials",
    "refresh_capital",
    "sync_candles",
    # Operator-authorized verification of the LIVE order path. Mutating by
    # definition: it can place a real order. The backend additionally refuses
    # it without prior LIVE consent and without an explicit authorize flag, so
    # the transport exposes the action without ever granting the permission.
    "order_test",
    # Orchestrated safe disconnect: stop engine, reconcile exposure, then
    # disconnect the broker only when it is safe. Never auto-liquidates.
    "safe_disconnect",
    # Historical Download: runs the preserved engine against the broker that
    # is CONNECTED on the gateway. Network-bound, deferred and progress-bearing.
    "download_action",
)

#: Commands whose backend work is network-bound (broker auth, REST,
#: provisioning, candle backfill) and therefore must never run on the socket
#: read thread. They are executed on bounded workers; the read thread stays
#: free to answer pings and push heartbeats/snapshots while they run.
_DEFERRED_ACTIONS = frozenset(
    {
        "server_op",
        "connect_broker",
        "disconnect_broker",
        "revoke_session",
        "save_broker_credentials",
        "broker_status",
        "refresh_capital",
        "sync_candles",
        "order_test",
        "safe_disconnect",
        "download_action",
    }
)

#: Deferred commands that emit progress frames so a slow venue call still
#: shows real activity (and keeps the desktop's deadline alive). The poll-style
#: commands keep a single result frame to preserve their wire semantics.
_PROGRESS_ACTIONS = frozenset(
    {
        "server_op",
        "connect_broker",
        "disconnect_broker",
        "revoke_session",
        "safe_disconnect",
        "save_broker_credentials",
        "order_test",
        "download_action",
    }
)

#: Event channels a client may subscribe to (snapshot sections + event stream).
EVENT_CHANNELS = (
    "worker",
    "recovery",
    "desired_state",
    "broker",
    # The authoritative unified broker session and the per-venue rows
    # (status, credential schema). These are the facts SYSTEM and LIVE both
    # render from; without them in this allowlist the section filter at
    # `_send_snapshot` silently dropped them, so a remote client could only
    # ever see the coarse legacy `broker` flag.
    "broker_session",
    "brokers",
    "market",
    # Historical Download console facts, produced by the preserved engine
    # running on the gateway against the broker CONNECTED there.
    "download",
    "order_stream",
    "strategy",
    "risk",
    "capital",
    "orders",
    "positions",
    "fills",
    "stops",
    "reconciliation",
    "system",
    "blockers",
    "gates",
    "lifecycle_progress",
    "events",
    "available_strategies",
    "available_symbols",
    "selected_symbols",
    "available_timeframes",
    "selected_timeframe",
    "quotes",
    "pnl",
    "server_code",
    "safe_disconnect",
    "as_of",
)

_CLOSE_POLICY = 1008
_CLOSE_NORMAL = 1000


class ConfigError(Exception):
    """The server refuses an unsafe configuration (fail closed, never listen)."""


@dataclass(frozen=True, slots=True)
class RemoteConfig:
    """Server configuration (values only; see :func:`config_from_env`)."""

    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/vayren/v1"
    auth_timeout_s: float = 10.0
    heartbeat_interval_s: float = 20.0
    idle_timeout_s: float = 90.0
    poll_interval_s: float = 1.0
    max_connections: int = 8
    #: Server-side deadline for a deferred command. Exceeding it is logged and
    #: surfaced in the next heartbeat health frame — it never silently raises
    #: a client timeout or cancels a risk-reducing operation.
    command_deadline_s: float = 30.0
    #: Consecutive deferred-command failures that open the gateway circuit.
    circuit_failure_threshold: int = 3
    circuit_cooldown_s: float = 30.0
    #: Bounded deadline for building/delivering an initial snapshot before
    #: reporting SNAPSHOT_TIMEOUT (or explicitly flagged cached fallback).
    snapshot_timeout_s: float = 5.0
    #: Maximum age for warm fallback snapshots (explicitly labeled stale=True).
    snapshot_cache_max_age_s: float = 15.0
    #: Per-frame socket write timeout so stalled peers cannot block writers.
    write_timeout_s: float = 2.0
    tls_cert_file: str = ""
    tls_key_file: str = ""
    allow_plaintext_non_loopback: bool = False

    def check(self) -> None:
        """Reject unsafe bindings before any socket is opened."""
        if self.port < 0 or self.port > 65535:
            raise ConfigError(f"invalid port {self.port}")
        if self.max_connections < 1:
            raise ConfigError("max_connections must be at least 1")
        if self.command_deadline_s <= 0 or self.circuit_cooldown_s <= 0:
            raise ConfigError("deadline and circuit cooldown must be positive")
        if (
            self.snapshot_timeout_s <= 0
            or self.snapshot_cache_max_age_s <= 0
            or self.write_timeout_s <= 0
        ):
            raise ConfigError("snapshot and write timeouts must be positive")
        if self.circuit_failure_threshold < 1:
            raise ConfigError("circuit_failure_threshold must be at least 1")
        non_loopback = self.host not in _LOOPBACK_HOSTS
        tls = bool(self.tls_cert_file or self.tls_key_file)
        if non_loopback and not tls and not self.allow_plaintext_non_loopback:
            raise ConfigError(
                f"refusing plaintext non-loopback bind on {self.host} "
                "(set TLS cert/key or VAYREN_REMOTE_ALLOW_PLAINTEXT=1 behind a tunnel)"
            )
        if tls and not (self.tls_cert_file and self.tls_key_file):
            raise ConfigError("TLS needs both VAYREN_REMOTE_TLS_CERT and VAYREN_REMOTE_TLS_KEY")


def config_from_env(env: dict[str, str] | None = None) -> RemoteConfig:
    """Build server configuration from environment (no code changes to tune)."""
    import os

    source = env if env is not None else os.environ

    def _float(name: str, default: float) -> float:
        try:
            return float(source.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    def _int(name: str, default: int) -> int:
        try:
            return int(source.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    return RemoteConfig(
        host=(source.get("VAYREN_REMOTE_HOST", "") or "127.0.0.1").strip(),
        port=_int("VAYREN_REMOTE_PORT", 8765),
        path=(source.get("VAYREN_REMOTE_PATH", "") or "/vayren/v1").strip(),
        auth_timeout_s=_float("VAYREN_REMOTE_AUTH_TIMEOUT_S", 10.0),
        heartbeat_interval_s=_float("VAYREN_REMOTE_HEARTBEAT_S", 20.0),
        idle_timeout_s=_float("VAYREN_REMOTE_IDLE_TIMEOUT_S", 90.0),
        poll_interval_s=_float("VAYREN_REMOTE_POLL_S", 1.0),
        max_connections=_int("VAYREN_REMOTE_MAX_CLIENTS", 8),
        command_deadline_s=_float("VAYREN_REMOTE_COMMAND_DEADLINE_S", 30.0),
        circuit_failure_threshold=_int("VAYREN_REMOTE_CIRCUIT_FAILURES", 3),
        circuit_cooldown_s=_float("VAYREN_REMOTE_CIRCUIT_COOLDOWN_S", 30.0),
        snapshot_timeout_s=_float("VAYREN_REMOTE_SNAPSHOT_TIMEOUT_S", 5.0),
        snapshot_cache_max_age_s=_float("VAYREN_REMOTE_SNAPSHOT_MAX_AGE_S", 15.0),
        write_timeout_s=_float("VAYREN_REMOTE_WRITE_TIMEOUT_S", 2.0),
        tls_cert_file=(source.get("VAYREN_REMOTE_TLS_CERT", "") or "").strip(),
        tls_key_file=(source.get("VAYREN_REMOTE_TLS_KEY", "") or "").strip(),
        allow_plaintext_non_loopback=(source.get("VAYREN_REMOTE_ALLOW_PLAINTEXT", "") or "")
        .strip()
        .lower()
        == "true",
    )


# ── RFC 6455 framing (text + control frames; stdlib only) ────────────────


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one client frame; returns (opcode, payload). Masks are enforced."""
    header = _recv_exact(sock, 2)
    first, second = header[0], header[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _recv_exact(sock, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _recv_exact(sock, 8))
    if length > _FRAME_LIMIT:
        raise ProtocolError("BAD_MESSAGE", "frame exceeds size limit")
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, length) if length else b""
    if opcode in (0x1, 0x2, 0x8, 0x9, 0xA) and not masked:
        raise ProtocolError("BAD_MESSAGE", "client frames must be masked")
    if masked and length:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    if not fin and opcode not in (0x0, 0x8, 0x9, 0xA):
        raise ProtocolError("BAD_MESSAGE", "fragmented messages are not supported")
    if opcode == 0x0:
        raise ProtocolError("BAD_MESSAGE", "fragmented messages are not supported")
    return opcode, payload


def write_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    """Write one server frame (never masked)."""
    header = bytes([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack("!H", length)
    else:
        header += bytes([127]) + struct.pack("!Q", length)
    sock.sendall(header + payload)


def send_text(sock: socket.socket, data: bytes) -> None:
    write_frame(sock, 0x1, data)


def perform_handshake(sock: socket.socket, expected_path: str) -> None:
    """Validate the HTTP upgrade request; raises on any mismatch."""
    sock.settimeout(10.0)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("handshake closed by peer")
        data += chunk
        if len(data) > _HANDSHAKE_LIMIT:
            raise ProtocolError("BAD_MESSAGE", "handshake headers too large")
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError as exc:
        raise ProtocolError("BAD_MESSAGE", "malformed handshake request") from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    if method.upper() != "GET":
        raise ProtocolError("BAD_MESSAGE", "handshake requires GET")
    if target.split("?", 1)[0] != expected_path:
        raise ProtocolError("BAD_MESSAGE", f"unknown endpoint {target!r}")
    if headers.get("upgrade", "").lower() != "websocket":
        raise ProtocolError("BAD_MESSAGE", "missing WebSocket upgrade")
    if "upgrade" not in headers.get("connection", "").lower():
        raise ProtocolError("BAD_MESSAGE", "missing Connection upgrade")
    if headers.get("sec-websocket-version", "") != "13":
        raise ProtocolError("BAD_MESSAGE", "unsupported WebSocket version")
    key = headers.get("sec-websocket-key", "")
    if not key:
        raise ProtocolError("BAD_MESSAGE", "missing Sec-WebSocket-Key")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {_accept_key(key)}\r\n"
        "\r\n"
    )
    sock.sendall(response.encode("latin-1"))


# ── connection state ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _OutboundFrame:
    """Single serialized WebSocket frame queued for a connection writer."""

    opcode: int
    payload: bytes
    enqueued_at: float = field(default_factory=time.monotonic)
    is_init_snapshot: bool = False
    build_ms: float | None = None
    snapshot_seq: int = 0


@dataclass
class _SnapshotBundle:
    """Immutable point-in-time snapshot projection produced by one build."""

    seq: int
    remote: dict[str, Any]
    hashes: dict[str, str]
    events: list[Any]
    built_at: float
    wall_time: str
    build_ms: float


@dataclass
class _SnapshotFetch:
    """Single-flight snapshot build ticket shared by poll and new clients."""

    started_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    bundle: _SnapshotBundle | None = None
    error: Exception | None = None


@dataclass
class _Connection:
    sock: socket.socket
    address: str
    tracker: AuthTracker = field(default_factory=AuthTracker)
    role: str | None = None
    connection_id: str = ""
    subscriptions: set[str] = field(default_factory=lambda: set[str](EVENT_CHANNELS))
    outbox: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=128))
    last_sections: dict[str, str] = field(default_factory=dict)
    last_event_tail: list[dict[str, Any]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.monotonic)
    last_ping: float = field(default_factory=time.monotonic)
    authed_at: float = 0.0
    authed_iso: str = ""
    needs_resync: bool = False
    initial_snapshot_sent: bool = False
    initial_snapshot_inflight: bool = False
    last_snapshot_at: float = 0.0
    init_snapshot_build_ms: float | None = None
    init_snapshot_send_ms: float | None = None
    disconnect_reason: str = ""
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    writer_thread: threading.Thread | None = None
    closed: bool = False


@dataclass(frozen=True, slots=True)
class _IdempotentResult:
    payload: dict[str, Any]
    stored_at: float


@dataclass(slots=True)
class _RequestStats:
    """Bounded latency/error counters for one command name."""

    count: int = 0
    failures: int = 0
    deadlines: int = 0
    durations_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_LATENCY_SAMPLE_CAP))

    def record(self, duration_ms: float, ok: bool, over_deadline: bool) -> None:
        self.count += 1
        self.failures += 0 if ok else 1
        self.deadlines += int(over_deadline)
        self.durations_ms.append(round(max(0.0, duration_ms), 3))

    def summary(self) -> dict[str, Any]:
        if not self.durations_ms:
            return {"count": self.count, "failures": self.failures}
        ordered = sorted(self.durations_ms)

        def _pct(fraction: float) -> float:
            idx = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
            return ordered[idx]

        return {
            "count": self.count,
            "failures": self.failures,
            "deadlines": self.deadlines,
            "p50_ms": _pct(0.50),
            "p95_ms": _pct(0.95),
            "p99_ms": _pct(0.99),
        }


@dataclass(slots=True)
class _CommandCircuit:
    """Per-action failure breaker for DEFERRED backend commands.

    Broker adapters own their own venue circuits; this one protects the
    transport from a backend that is failing fast on every call (a storm of
    doomed broker REST attempts starves the connection threads). It refuses
    NEW intents only, is bounded by a cooldown, and never blocks a retry that
    follows an explicit operator action once HALF_OPEN.
    """

    failures: int = 0
    opened_at: float = 0.0

    def state(self, *, threshold: int, cooldown_s: float, now: float) -> str:
        if self.failures < threshold:
            return "CLOSED"
        if now - self.opened_at < cooldown_s:
            return "OPEN"
        return "HALF_OPEN"

    def record_ok(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def record_failure(self, *, threshold: int, now: float) -> None:
        self.failures += 1
        if self.failures >= threshold and not self.opened_at:
            self.opened_at = now


class RemoteServer:
    """One controlled WebSocket endpoint over the backend gateway.

    Owns sockets, heartbeats, subscriptions, and duplicate suppression —
    never trading decisions. All backend reads/writes go through the
    injected gateway (fakes in tests, :class:`HeadlessGateway` live).
    """

    def __init__(
        self,
        gateway: BackendGateway,
        token_map: dict[str, TokenEntry],
        config: RemoteConfig | None = None,
    ) -> None:
        self._gateway = gateway
        self._tokens = dict(token_map)
        self._config = config or RemoteConfig()
        self._config.check()
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._connections: dict[str, _Connection] = {}
        self._connection_seq = 0
        self._message_seq = 0
        self._event_ring: deque[tuple[int, dict[str, Any]]] = deque(maxlen=256)
        self._seen_commands: OrderedDict[str, _IdempotentResult] = OrderedDict()
        self._dropped_messages = 0
        # Async lifecycle state (START/STOP run on workers so the
        # connection thread never blocks behind a START build and STOP can
        # preempt it). _start_cancel aborts the in-flight START build;
        # _lifecycle_threads bounds concurrent workers (one START + one
        # STOP max — STOP wins via the service lifecycle gen).
        self._start_cancel = threading.Event()
        self._start_inflight: threading.Thread | None = None
        self._inflight_lifecycle: dict[str, threading.Event] = {}
        self._lifecycle_started: dict[str, float] = {}
        self._lifecycle_workers: list[threading.Thread] = []
        # Observability + load shedding: bounded in-flight deferred commands,
        # per-action latency percentiles and a failure breaker. A slow broker
        # REST call can never occupy the connection read thread (and therefore
        # can never cause an 8s "gateway did not respond" at the desktop).
        self._deferred_slots = threading.BoundedSemaphore(8)
        self._inflight_deferred = 0
        self._request_stats: dict[str, _RequestStats] = {}
        self._circuits: dict[str, _CommandCircuit] = {}
        self._started_at = time.monotonic()
        # Decoupled single-flight snapshot publisher + bounded worker pools so
        # slow backend calls or stalled clients can never wedge the gateway.
        self._snapshot_lock = threading.Lock()
        self._snapshot_inflight: _SnapshotFetch | None = None
        self._latest_bundle: _SnapshotBundle | None = None
        self._snapshot_seq = 0
        self._snapshot_build_errors = 0
        self._last_snapshot_error = ""
        self._last_poll_at = 0.0
        self._last_poll_delay_ms = 0.0
        self._last_published_seq = 0
        self._snapshot_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="remote-snap-build"
        )
        self._delivery_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="remote-snap-send"
        )

    # ── lifecycle ──

    @property
    def port(self) -> int:
        """Bound port (useful when the OS picks an ephemeral one)."""
        if self._listener is not None:
            with contextlib.suppress(OSError):
                return self._listener.getsockname()[1]
        return self._config.port

    @property
    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def start(self) -> None:
        """Bind, listen, and start accept/poll threads (non-blocking)."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._config.host, self._config.port))
        listener.listen(16)
        listener.settimeout(1.0)
        if self._config.tls_cert_file:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self._config.tls_cert_file, self._config.tls_key_file)
            listener = context.wrap_socket(listener, server_side=True)
        self._listener = listener
        self._stop.clear()
        accept = threading.Thread(target=self._accept_loop, name="remote-accept", daemon=True)
        poll = threading.Thread(target=self._poll_loop, name="remote-poll", daemon=True)
        accept.start()
        poll.start()
        self._threads = [accept, poll]
        logger.info("remote transport listening on %s:%s", self._config.host, self.port)

    def stop(self) -> None:
        """Shut down listeners and connections (never touches trading)."""
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()
        with self._lock:
            connections = list(self._connections.values())
        for conn in connections:
            self._close_connection(conn, _CLOSE_NORMAL, reason="server_stop")
        for thread in self._threads:
            thread.join(timeout=5.0)
        for conn in connections:
            writer = conn.writer_thread
            if writer is not None and writer is not threading.current_thread():
                writer.join(timeout=1.0)
        self._snapshot_executor.shutdown(wait=False, cancel_futures=True)
        self._delivery_executor.shutdown(wait=False, cancel_futures=True)

    # ── accept ──

    def _reap_stale(self) -> None:
        """Close connections that outlived the idle policy.

        A connection normally unregisters itself from its own thread, but a
        thread wedged in a blocked write leaves a dead socket registered
        forever. Eight such entries permanently fill ``max_connections``: the
        gateway then refuses every client, the tunnel answers 502, and the
        desktop reports that the gateway did not respond. This runs on the
        accept thread, takes no per-connection lock (``send_lock`` may be
        held by the wedged writer), and makes the state self-healing.
        """
        now = time.monotonic()
        with self._lock:
            stale = [
                conn
                for conn in self._connections.values()
                if now - conn.last_activity > self._config.idle_timeout_s
            ]
        for conn in stale:
            elapsed_s = int(now - conn.last_activity)
            conn.disconnect_reason = conn.disconnect_reason or f"idle_timeout_{elapsed_s}s"
            conn.closed = True
            logger.warning(
                "remote: reaping stale connection (%s, idle %.0fs)",
                conn.connection_id,
                now - conn.last_activity,
            )
            with contextlib.suppress(queue.Full):
                conn.outbox.put_nowait(None)
            with contextlib.suppress(OSError):
                conn.sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                conn.sock.close()
            self._unregister(conn)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            self._reap_stale()
            try:
                sock, addr = self._listener.accept()
            except (OSError, ssl.SSLError):
                continue
            with self._lock:
                over_limit = len(self._connections) >= self._config.max_connections
            if over_limit:
                logger.warning("remote: connection limit reached; refusing %s", addr[0])
                with contextlib.suppress(OSError):
                    sock.close()
                continue
            worker = threading.Thread(
                target=self._serve_connection,
                args=(sock, addr[0]),
                name=f"remote-conn-{addr[0]}",
                daemon=True,
            )
            worker.start()

    def _register(self, conn: _Connection) -> str:
        with self._lock:
            self._connection_seq += 1
            conn.connection_id = f"c{self._connection_seq}"
            self._connections[conn.connection_id] = conn
            return conn.connection_id

    def _unregister(self, conn: _Connection) -> None:
        with self._lock:
            self._connections.pop(conn.connection_id, None)

    def _start_writer(self, conn: _Connection) -> None:
        writer = threading.Thread(
            target=self._writer_loop,
            args=(conn,),
            name=f"remote-write-{conn.connection_id}",
            daemon=True,
        )
        conn.writer_thread = writer
        writer.start()

    def _writer_loop(self, conn: _Connection) -> None:
        while not self._stop.is_set() and not conn.closed:
            try:
                item = conn.outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if conn.closed or self._stop.is_set():
                break
            try:
                with conn.send_lock:
                    if conn.closed:
                        break
                    if item.opcode == 0x1:
                        send_text(conn.sock, item.payload)
                    else:
                        write_frame(conn.sock, item.opcode, item.payload)
                if item.is_init_snapshot:
                    sent_at = time.monotonic()
                    send_ms = max(0.0, (sent_at - item.enqueued_at) * 1000.0)
                    with conn.state_lock:
                        if conn.init_snapshot_send_ms is None:
                            conn.init_snapshot_send_ms = send_ms
                    logger.info(
                        "remote: initial snapshot sent connection_id=%s seq=%d "
                        "build_ms=%.1f send_ms=%.1f queue_depth=%d",
                        conn.connection_id,
                        item.snapshot_seq,
                        item.build_ms or 0.0,
                        send_ms,
                        conn.outbox.qsize(),
                    )
            except OSError as exc:
                self._close_connection(
                    conn, _CLOSE_POLICY, reason=f"write_failed:{type(exc).__name__}", force=True
                )
                break

    # ── per-connection serve ──

    def _serve_connection(self, sock: socket.socket, address: str) -> None:
        conn = _Connection(sock=sock, address=address)
        try:
            perform_handshake(sock, self._config.path)
        except (ProtocolError, ConnectionError, OSError, ssl.SSLError) as exc:
            logger.warning("remote: handshake rejected for %s: %s", address, exc)
            with contextlib.suppress(OSError):
                sock.close()
            return
        self._register(conn)
        self._start_writer(conn)
        logger.info("remote: client connected (%s)", conn.connection_id)
        sock.settimeout(min(0.25, max(0.05, self._config.auth_timeout_s / 4)))
        deadline = time.monotonic() + self._config.auth_timeout_s
        try:
            while not self._stop.is_set() and not conn.closed:
                if not conn.tracker.authenticated and time.monotonic() > deadline:
                    self._send_error(conn, "NOT_AUTHENTICATED", "authentication timed out")
                    self._close_connection(conn, _CLOSE_POLICY, reason="auth_timeout")
                    return
                try:
                    opcode, payload = read_frame(sock)
                except TimeoutError:
                    self._check_idle(conn)
                    continue
                except (ConnectionError, ProtocolError, OSError, struct.error) as exc:
                    if not conn.closed:
                        logger.warning("remote: read failed (%s): %s", conn.connection_id, exc)
                    self._close_connection(
                        conn, _CLOSE_POLICY, reason=conn.disconnect_reason or "peer_closed"
                    )
                    return
                conn.last_activity = time.monotonic()
                if opcode == 0x8:
                    self._close_connection(conn, _CLOSE_NORMAL, reason="client_close")
                    return
                if opcode == 0x9:
                    self._enqueue_frame(
                        conn, _OutboundFrame(opcode=0xA, payload=payload[:125]), drop_ok=True
                    )
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x2:
                    self._send_error(conn, "BAD_MESSAGE", "binary frames are not supported")
                    continue
                if opcode != 0x1:
                    continue
                if self._handle_text(conn, payload):
                    return
        finally:
            self._close_connection(
                conn, _CLOSE_NORMAL, reason=conn.disconnect_reason or "connection_closed"
            )
            self._unregister(conn)
            writer = conn.writer_thread
            if writer is not None and writer is not threading.current_thread():
                writer.join(timeout=0.5)
            logger.info(
                "remote: client disconnected (%s) reason=%s",
                conn.connection_id,
                conn.disconnect_reason or "closed",
            )

    def _handle_text(self, conn: _Connection, payload: bytes) -> bool:
        """Handle one text frame; True means the connection is done."""
        try:
            message = decode_message(payload, from_client=True)
        except ProtocolError as exc:
            self._send_error(conn, exc.code, exc.detail)
            return False
        if not conn.tracker.authenticated:
            return self._handle_hello(conn, message)
        handler = {
            "ping": self._on_ping,
            "subscribe": self._on_subscribe,
            "unsubscribe": self._on_unsubscribe,
            "command": self._on_command,
            "hello": self._on_rehello,
        }.get(message.msg_type)
        if handler is None:
            self._send_error(conn, "BAD_MESSAGE", f"unexpected {message.msg_type!r}")
            return False
        handler(conn, message)
        return False

    # ── authentication ──

    def _handle_hello(self, conn: _Connection, message: Any) -> bool:
        if message.msg_type != "hello":
            self._send_error(conn, "NOT_AUTHENTICATED", "first message must be 'hello'")
            return False
        token = message.payload.get("token", "")
        entry = verify_token(str(token) if isinstance(token, str) else "", self._tokens)
        if entry is None:
            may_retry = conn.tracker.note_attempt(None)
            self._send_error(conn, "AUTH_FAILED", "invalid remote token")
            if not may_retry:
                self._close_connection(conn, _CLOSE_POLICY, reason="auth_failed")
                return True
            return False
        conn.tracker.note_attempt(entry)
        conn.role = entry.role
        conn.authed_at = time.monotonic()
        conn.authed_iso = utcnow_iso()
        conn.last_activity = conn.authed_at
        logger.info(
            "remote: client authenticated connection_id=%s role=%s authed_at=%s",
            conn.connection_id,
            entry.role,
            conn.authed_iso,
        )
        welcome = make_envelope(
            "welcome",
            {
                "connection_id": conn.connection_id,
                "role": entry.role,
                "server_time": utcnow_iso(),
                "schema_version": SCHEMA_VERSION,
                "endpoint": self._config.path,
            },
        )
        self._send_raw(conn, encode_message(welcome))
        self._schedule_initial_snapshot(conn, resync=False, min_started_at=conn.authed_at)
        return False

    def _on_rehello(self, conn: _Connection, _message: Any) -> None:
        self._send_error(conn, "BAD_MESSAGE", "already authenticated")

    # ── subscriptions / heartbeat ──

    def _on_ping(self, conn: _Connection, _message: Any) -> None:
        # Health rides on the heartbeat so a slow command is visible as
        # in-flight work instead of a dead gateway (no secret is ever sent).
        self._send(
            conn,
            make_envelope(
                "pong",
                {"server_time": utcnow_iso(), "health": self.health_snapshot()},
            ),
        )

    def _on_subscribe(self, conn: _Connection, message: Any) -> None:
        channels = message.payload.get("channels", [])
        last_seq = message.payload.get("last_seq")
        if not isinstance(channels, list) or not channels:
            wanted: set[str] = set(EVENT_CHANNELS)
        else:
            wanted = {str(item) for item in channels if isinstance(item, str)}
            unknown = wanted - set(EVENT_CHANNELS)
            if unknown:
                self._send_error(conn, "BAD_MESSAGE", f"unknown channels: {sorted(unknown)}")
                return
        with conn.state_lock:
            conn.subscriptions = wanted
        if isinstance(last_seq, int) and last_seq >= 0:
            replayed = self._replay_missed(conn, last_seq)
            if not replayed:
                self._schedule_initial_snapshot(conn, resync=True, min_started_at=time.monotonic())
        else:
            self._schedule_initial_snapshot(conn, resync=False, min_started_at=time.monotonic())

    def _on_unsubscribe(self, conn: _Connection, message: Any) -> None:
        channels = message.payload.get("channels", [])
        if isinstance(channels, list):
            for item in channels:
                if isinstance(item, str):
                    conn.subscriptions.discard(item)

    # ── commands (delegated, idempotent) ──

    def _on_command(self, conn: _Connection, message: Any) -> None:
        assert conn.role is not None
        action = message.payload.get("action", "")
        params = message.payload.get("params", {})
        if not isinstance(action, str) or action not in MUTATING_ACTIONS:
            self._send_error(
                conn,
                "UNKNOWN_COMMAND",
                f"unknown action {action!r}",
                request_id=message.request_id,
            )
            return
        if not RemoteRole.allows_command(conn.role):
            self._send_error(
                conn,
                "NOT_AUTHORIZED",
                "read-only credential cannot send control commands",
                request_id=message.request_id,
            )
            return
        if not message.request_id:
            self._send_error(
                conn,
                "BAD_MESSAGE",
                "control commands require a 'request_id'",
                request_id=None,
            )
            return
        cached = self._recall_command(message.request_id)
        if cached is not None:
            replay = dict(cached.payload)
            replay["cached"] = True
            self._send(
                conn,
                make_envelope("command_result", replay, request_id=message.request_id),
            )
            return
        started = time.monotonic()
        if action == "server_op":
            from remote.server_ops import execute_server_op

            op_params = params if isinstance(params, dict) else {}
            op_name = str(op_params.get("op", "") or "")
            data_dir = getattr(self._gateway, "_data_dir", "/home/ubuntu/.vayren/data")
            strat_dir = getattr(self._gateway, "_strategy_dir", "/home/ubuntu/.vayren/strategies")
            caller = f"{conn.role}:{conn.connection_id}"

            def _server_op() -> dict[str, Any]:
                res = execute_server_op(
                    op_name=op_name,
                    params=op_params,
                    data_dir=str(data_dir),
                    strategy_dir=str(strat_dir),
                    caller=caller,
                )
                if isinstance(res, dict):
                    res.setdefault("action", "server_op")
                    res.setdefault("op", op_name)
                return res if isinstance(res, dict) else {"action": "server_op", "ok": False}

            if self._admit_deferred(conn, action, message.request_id):
                self._run_deferred(
                    conn,
                    action,
                    message.request_id,
                    started,
                    _server_op,
                    progress=True,
                    needs_resync=True,
                )
            return
        headless_action = self._translate_command(action, params, message.request_id)
        if headless_action is None:
            self._send_error(
                conn,
                "COMMAND_REJECTED",
                "command refused before reaching the backend",
                request_id=message.request_id,
            )
            return
        if action in ("start", "stop"):
            # Async lifecycle: immediate ack, then a worker runs the backend
            # so START builds never block this connection thread (and never
            # queue STOP behind them). STOP sets the START cancel event
            # first thing — preemption, not queuing.
            self._dispatch_lifecycle(conn, action, headless_action, message.request_id)
            return
        if action in _DEFERRED_ACTIONS:
            # Broker auth / REST / provisioning work is network-bound. Running
            # it inline on the connection thread starved ping/pong and made the
            # desktop fire its 8s deadline even when the backend eventually
            # succeeded. Bounded worker + progress + result keeps the link
            # responsive and the operator honestly informed.
            if not self._admit_deferred(conn, action, message.request_id):
                return
            self._run_deferred(
                conn,
                action,
                message.request_id,
                started,
                lambda: self._gateway.apply_command(headless_action),
                progress=action in _PROGRESS_ACTIONS,
                needs_resync=action != "broker_status",
            )
            return
        try:
            fresh = self._gateway.apply_command(headless_action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: backend command failed: %s", exc)
            self._record_request(action, time.monotonic() - started, ok=False)
            self._send_error(
                conn, "SERVER_ERROR", "backend command failed", request_id=message.request_id
            )
            return
        result = self._command_result(action, fresh)
        self._remember_command(message.request_id, result)
        self._send(conn, make_envelope("command_result", result, request_id=message.request_id))
        self._record_request(action, time.monotonic() - started, ok=bool(result.get("ok", True)))
        conn.needs_resync = True

    # ── deferred command workers, metrics, circuit ──

    def _admit_deferred(self, conn: _Connection, action: str, request_id: str) -> bool:
        """Bound the deferred fan-out and shed load honestly (never silently)."""
        now = time.monotonic()
        err_code: str | None = None
        err_detail = ""
        with self._lock:
            circuit = self._circuits.setdefault(action, _CommandCircuit())
            state = circuit.state(
                threshold=self._config.circuit_failure_threshold,
                cooldown_s=self._config.circuit_cooldown_s,
                now=now,
            )
            if state == "OPEN":
                err_code = "CIRCUIT_OPEN"
                err_detail = (
                    f"{action} temporarily unavailable after repeated backend failures; "
                    f"retry in {int(self._config.circuit_cooldown_s - (now - circuit.opened_at))}s"
                )
            elif not self._deferred_slots.acquire(blocking=False):
                err_code = "SERVER_BUSY"
                err_detail = "gateway is busy with other broker operations; retry shortly"
            else:
                self._inflight_deferred += 1
        if err_code is not None:
            self._send_error(conn, err_code, err_detail, request_id=request_id)
            return False
        return True

    def _release_deferred(self) -> None:
        with self._lock:
            self._inflight_deferred = max(0, self._inflight_deferred - 1)
        self._deferred_slots.release()

    def _record_request(self, action: str, duration_s: float, *, ok: bool) -> None:
        over = duration_s > self._config.command_deadline_s
        with self._lock:
            stats = self._request_stats.setdefault(action, _RequestStats())
        stats.record(duration_s * 1000.0, ok, over)
        circuit = self._circuits.get(action)
        if circuit is not None:
            if ok:
                circuit.record_ok()
            else:
                circuit.record_failure(
                    threshold=self._config.circuit_failure_threshold, now=time.monotonic()
                )
        if over:
            logger.warning(
                "remote: command deadline exceeded action=%s elapsed_ms=%s deadline_s=%s",
                action,
                round(duration_s * 1000.0, 1),
                self._config.command_deadline_s,
            )
        logger.info(
            "remote: command action=%s ok=%s elapsed_ms=%s",
            action,
            ok,
            round(duration_s * 1000.0, 1),
        )

    def _run_deferred(
        self,
        conn: _Connection,
        action: str,
        request_id: str,
        started: float,
        work: Any,
        *,
        progress: bool,
        needs_resync: bool,
    ) -> None:
        """Execute one slow command on a worker; always answer exactly once."""

        def _stage(stage: str, detail: str) -> None:
            if not progress:
                return
            self._send(
                conn,
                make_envelope(
                    "command_progress",
                    {
                        "action": action,
                        "command_id": request_id,
                        "stage": stage,
                        "detail": detail,
                    },
                    request_id=request_id,
                ),
            )

        def _execute() -> None:
            ok = False
            try:
                _stage("running", f"{action} in progress")
                fresh = work()
                if action == "server_op":
                    result = dict(fresh) if isinstance(fresh, dict) else {}
                else:
                    result = self._command_result(action, fresh)
                result["command_id"] = request_id
                ok = bool(result.get("ok", True))
                # Record BEFORE the result is observable: a health probe sent
                # immediately after the answer must already see this command's
                # latency sample (the post-deploy smoke test caught this race).
                self._record_request(action, time.monotonic() - started, ok=ok)
                self._remember_command(request_id, result)
                _stage("done", "backend answered")
                self._send(conn, make_envelope("command_result", result, request_id=request_id))
                if needs_resync:
                    conn.needs_resync = True
            except Exception as exc:  # noqa: BLE001 - never leak a traceback/secret
                logger.warning("remote: deferred command failed action=%s: %s", action, exc)
                self._record_request(action, time.monotonic() - started, ok=False)
                self._send_error(
                    conn,
                    "SERVER_ERROR",
                    f"{action} failed at the backend; retry or check Gateway Health",
                    request_id=request_id,
                )
            finally:
                self._release_deferred()

        worker = threading.Thread(
            target=_execute,
            name=f"remote-deferred-{action}-{request_id}",
            daemon=True,
        )
        worker.start()

    def health_snapshot(self) -> dict[str, Any]:
        """Heartbeat health: worker load, queue state, snapshot telemetry and latency."""
        now = time.monotonic()
        with self._lock:
            stats = {name: value.summary() for name, value in sorted(self._request_stats.items())}
            inflight = self._inflight_deferred
            circuits = {
                name: circuit.state(
                    threshold=self._config.circuit_failure_threshold,
                    cooldown_s=self._config.circuit_cooldown_s,
                    now=now,
                )
                for name, circuit in sorted(self._circuits.items())
            }
            conns = list(self._connections.values())
            queued = sum(conn.outbox.qsize() for conn in conns)
            dropped = self._dropped_messages
        with self._snapshot_lock:
            latest = self._latest_bundle
            snap_building = self._snapshot_inflight is not None
            snap_seq = self._snapshot_seq
            snap_errors = self._snapshot_build_errors
        client_diag: list[dict[str, Any]] = []
        for conn in conns:
            with conn.state_lock:
                client_diag.append(
                    {
                        "connection_id": conn.connection_id,
                        "role": conn.role or "",
                        "authed_at": conn.authed_iso,
                        "init_snapshot_build_ms": conn.init_snapshot_build_ms,
                        "init_snapshot_send_ms": conn.init_snapshot_send_ms,
                        "snapshot_age_ms": (
                            round(max(0.0, (now - conn.last_snapshot_at) * 1000.0), 1)
                            if conn.last_snapshot_at > 0.0
                            else None
                        ),
                        "queue_depth": conn.outbox.qsize(),
                        "needs_resync": conn.needs_resync,
                    }
                )
        return {
            "uptime_s": round(now - self._started_at, 1),
            "inflight_commands": inflight,
            "outbox_queued": queued,
            "queue_depth": queued,
            "dropped_messages": dropped,
            "connections": len(conns),
            "commands": stats,
            "circuits": circuits,
            "last_snapshot_at": latest.wall_time if latest is not None else "",
            "snapshot_age_ms": (
                round(max(0.0, (now - latest.built_at) * 1000.0), 1) if latest is not None else None
            ),
            "snapshot_build_ms": round(latest.build_ms, 1) if latest is not None else None,
            "snapshot_seq": snap_seq,
            "snapshot_building": snap_building,
            "snapshot_build_errors": snap_errors,
            "poll_delay_ms": round(self._last_poll_delay_ms, 1),
            "clients": client_diag,
        }

    def _dispatch_lifecycle(
        self, conn: _Connection, action: str, headless_action: dict[str, Any], request_id: str
    ) -> None:
        """Immediate-ack + worker for START/STOP (STOP preempts START)."""
        if action == "stop":
            # Highest priority: abort the in-flight START build before this
            # STOP worker even starts. The service gen also aborts it.
            self._start_cancel.set()
        done_event = threading.Event()
        with self._lock:
            self._inflight_lifecycle[request_id] = done_event
            self._lifecycle_started[request_id] = time.monotonic()
            if action == "start":
                # Clear the cancel flag for the fresh START. A STOP that
                # arrives afterwards sets it again (preemption).
                self._start_cancel.clear()
        self._send(
            conn,
            make_envelope(
                "command_accepted",
                {"action": action, "command_id": request_id, "status": "accepted"},
                request_id=request_id,
            ),
        )
        cancel_event = self._start_cancel if action == "start" else None

        def _on_progress(stage: str, detail: str, done: Any, total: Any) -> None:
            payload: dict[str, Any] = {
                "action": action,
                "command_id": request_id,
                "stage": str(stage),
                "detail": str(detail or ""),
            }
            if done is not None:
                payload["done"] = int(done)
            if total is not None:
                payload["total"] = int(total)
            self._send(conn, make_envelope("command_progress", payload, request_id=request_id))

        worker_action = dict(headless_action)
        worker_action["_progress_cb"] = _on_progress
        worker_action["_cancel_event"] = cancel_event
        worker = threading.Thread(
            target=self._execute_lifecycle,
            args=(conn, action, worker_action, request_id, done_event),
            name=f"remote-lifecycle-{action}-{request_id}",
            daemon=True,
        )
        with self._lock:
            self._lifecycle_workers.append(worker)
            if action == "start":
                self._start_inflight = worker
        worker.start()

    def _execute_lifecycle(
        self,
        conn: _Connection,
        action: str,
        worker_action: dict[str, Any],
        request_id: str,
        done_event: threading.Event,
    ) -> None:
        """Run one lifecycle command on a worker; always emits a result."""
        started = self._lifecycle_started.pop(request_id, time.monotonic())
        try:
            fresh = self._gateway.apply_command(worker_action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: lifecycle backend failed: %s", exc)
            result = {
                "ok": False,
                "action": action,
                "cached": False,
                "command_id": request_id,
                "session_status": "STOPPED" if action == "stop" else "ERROR",
                "status_reason": "backend command failed",
                "start_blockers": [],
                "detail": "backend command failed",
            }
            self._remember_command(request_id, result)
            self._send(conn, make_envelope("command_result", result, request_id=request_id))
            done_event.set()
            with self._lock:
                self._inflight_lifecycle.pop(request_id, None)
            self._record_request(action, time.monotonic() - started, ok=False)
            return
        result = self._command_result(action, fresh, include_snapshot=False)
        # Carry the command id so the client can correlate acks strictly.
        result["command_id"] = request_id
        if isinstance(fresh, dict) and isinstance(fresh.get("action_note"), str):
            result.setdefault("detail", str(fresh["action_note"]))
        self._remember_command(request_id, result)
        self._send(conn, make_envelope("command_result", result, request_id=request_id))
        conn.needs_resync = True
        done_event.set()
        with self._lock:
            self._inflight_lifecycle.pop(request_id, None)
        self._record_request(action, time.monotonic() - started, ok=bool(result.get("ok", True)))

    def _translate_command(
        self, action: str, params: Any, request_id: str
    ) -> dict[str, Any] | None:
        """Map a remote command onto the existing headless action shape."""
        args = dict(params) if isinstance(params, dict) else {}
        if action == "refresh":
            return {"action": "refresh", "request_id": request_id}
        if action == "start":
            if bool(args.get("confirmed", False)) is not True:
                return None
            return {"action": "start", "confirmed": True, "request_id": request_id}
        if action == "setup":
            allowed = ("strategy_name", "strategy", "symbols", "timeframe", "mode", "quantity")
            return {"action": "setup", **{k: v for k, v in args.items() if k in allowed}}
        if action == "mode":
            if args.get("mode") not in ("BACKTEST", "LIVE"):
                return None
            return {"action": "mode", "mode": args["mode"]}
        if action in ("stop", "tick", "confirm_live", "disarm_live"):
            return {"action": action}
        if action == "confirm_account":
            return {"action": "confirm_account", "account_id": args.get("account_id", "")}
        if action == "select_broker":
            # SYSTEM's "make this the default broker" action. Translated here
            # so the gateway's OWN selection store is updated — without this a
            # desktop SYSTEM change never reached the gateway and LIVE kept
            # resolving a different broker. The backend still validates the
            # name against the registry and refuses a mid-session switch.
            broker = args.get("broker", "")
            if not isinstance(broker, str) or not broker.strip():
                return None
            return {"action": "select_broker", "broker": broker.strip().lower()}
        if action == "select_symbol":
            symbol = args.get("symbol", "")
            if not isinstance(symbol, str) or not symbol.strip():
                return None
            return {"action": "select_symbol", "symbol": symbol.strip()}
        if action == "safe_disconnect":
            broker = args.get("broker_id", args.get("broker", ""))
            if not isinstance(broker, str) or not broker.strip():
                return None
            out: dict[str, Any] = {
                "action": "safe_disconnect",
                "broker_id": broker.strip().lower(),
                "request_id": request_id,
            }
            # Explicit, audited operator authorization for remaining exposure.
            # Never defaults to True: open positions always require action.
            if isinstance(args.get("acknowledge_exposure"), bool):
                out["acknowledge_exposure"] = bool(args["acknowledge_exposure"])
            return out
        if action in (
            "connect_broker",
            "disconnect_broker",
            "revoke_session",
            "broker_status",
            "save_broker_credentials",
        ):
            broker = args.get("broker_id", args.get("broker", ""))
            if not isinstance(broker, str) or not broker.strip():
                return None
            out: dict[str, Any] = {
                "action": action,
                "broker_id": broker.strip().lower(),
                "request_id": request_id,
            }
            if isinstance(args.get("credentials"), dict):
                out["credentials"] = dict(args["credentials"])
            if isinstance(args.get("revoke"), bool):
                out["revoke"] = bool(args["revoke"])
            return out
        if action == "refresh_capital":
            # Read-only funds re-read. The backend resolves the venue itself
            # from the authoritative selection, so no broker id is needed
            # (and none is invented here).
            return {"action": "refresh_capital"}
        if action == "sync_candles":
            # Populates the local candle store from the venue's own history.
            # Operator-triggered on purpose: it is network work against a live
            # account, and it rewrites the bars the readiness gate reads. Only
            # the window may be overridden — the instrument list is the
            # backend's own configured universe, never a client's.
            allowed = ("days", "timeframe")
            return {
                "action": "sync_candles",
                **{k: v for k, v in args.items() if k in allowed},
            }
        if action == "order_test":
            # Only the four inputs the backend acts on are forwarded. The
            # instrument must already be the configured universe and the
            # quantity must equal the configured one, so a client cannot widen
            # the test beyond what the operator configured.
            allowed = ("side", "symbol", "quantity", "stop_price", "authorize")
            return {
                "action": "order_test",
                **{k: v for k, v in args.items() if k in allowed},
            }
        if action == "download_action":
            # Only one `dl:*` wire is forwarded. The engine itself resolves
            # the connected broker from the gateway's authoritative manager.
            wire = args.get("wire")
            if not isinstance(wire, str) or not wire.strip():
                return None
            return {
                "action": "download_action",
                "wire": wire.strip()[:200],
                "request_id": request_id,
            }
        return None

    def _command_result(
        self, action: str, fresh: dict[str, Any], *, include_snapshot: bool = True
    ) -> dict[str, Any]:
        """Wrap the backend's own post-command snapshot as the result.

        ``include_snapshot=False`` (async START/STOP): the result carries
        only the ack fields. A full snapshot build here doubled backend
        work per lifecycle command (plus the poll build it forced via
        ``needs_resync``). The poll ``state_update`` delivers the truth
        within one poll tick — the client never needs the embedded copy.
        """
        if not isinstance(fresh, dict):
            return {"ok": False, "action": action, "detail": "backend gave no snapshot"}
        blockers = list(fresh.get("start_blockers", []) or [])
        status = str(fresh.get("session_status", "") or "").upper()
        running = status == "RUNNING"
        starting = status == "STARTING"
        if action == "start":
            ok = running or starting or bool(fresh.get("action_ok", False))
        elif action == "refresh_capital":
            # `action_ok` is False when the backend refused the read (see
            # headless `_live_unavailable`); its `action_note` carries the
            # real reason, so a failed funds read never reports success.
            ok = bool(fresh.get("action_ok", True))
        elif action in (
            "connect_broker",
            "disconnect_broker",
            "revoke_session",
            "save_broker_credentials",
            "safe_disconnect",
        ):
            ok = bool(fresh.get("action_ok", True))
        elif action == "broker_status":
            # Read-only: the refresh succeeds when the snapshot carries the
            # authoritative session; action_ok is not part of that command.
            ok = "broker_session" in fresh
        elif action == "download_action":
            # The gateway runs the preserved engine and reports its own verdict;
            # never acknowledge a blocked/unauthenticated download as success.
            ok = bool(fresh.get("ok", True))
        else:
            ok = True
        out: dict[str, Any] = {
            "ok": ok,
            "action": action,
            "cached": False,
            "session_status": str(fresh.get("session_status", "") or ""),
            "status_reason": str(fresh.get("status_reason", "") or ""),
            "start_blockers": blockers,
        }
        if include_snapshot:
            out["snapshot"] = build_snapshot(fresh)
        # The gateway's download command result IS the authoritative download
        # snapshot (selection, plan count, broker gating, progress). Without
        # it the desktop only has optimistic checkboxes and a stale plan while
        # it waits for the next periodic state_update.
        if isinstance(fresh.get("download"), dict):
            out["download"] = dict(fresh["download"])
        if isinstance(fresh.get("broker_session"), dict):
            out["broker_session"] = dict(fresh["broker_session"])
        if isinstance(fresh.get("action_note"), str):
            out["detail"] = fresh["action_note"]
        return out

    # ── idempotency store (bounded) ──

    def _recall_command(self, request_id: str) -> _IdempotentResult | None:
        with self._lock:
            entry = self._seen_commands.get(request_id)
            if entry is None:
                return None
            if time.monotonic() - entry.stored_at > 600.0:
                self._seen_commands.pop(request_id, None)
                return None
            self._seen_commands.move_to_end(request_id)
            return entry

    def _remember_command(self, request_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._seen_commands[request_id] = _IdempotentResult(
                payload=dict(result), stored_at=time.monotonic()
            )
            while len(self._seen_commands) > 512:
                self._seen_commands.popitem(last=False)

    # ── publish path (decoupled snapshot builder + non-blocking fanout) ──

    def _build_snapshot_bundle(self) -> _SnapshotBundle:
        t0 = time.monotonic()
        live = self._gateway.get_snapshot()
        if not isinstance(live, dict):
            raise RuntimeError("backend snapshot returned non-dict")
        raw_events = live.get("events")
        events: list[Any] = list(raw_events) if isinstance(raw_events, list) else []
        remote = self._with_download_section(build_snapshot(live))
        hashes = {key: _stable_hash(value) for key, value in remote.items()}
        built_at = time.monotonic()
        build_ms = max(0.0, (built_at - t0) * 1000.0)
        wall = utcnow_iso()
        with self._snapshot_lock:
            self._snapshot_seq += 1
            seq = self._snapshot_seq
            bundle = _SnapshotBundle(
                seq=seq,
                remote=remote,
                hashes=hashes,
                events=events,
                built_at=built_at,
                wall_time=wall,
                build_ms=build_ms,
            )
            self._latest_bundle = bundle
            return bundle

    def _start_snapshot_fetch(self, *, min_started_at: float | None = None) -> _SnapshotFetch:
        with self._snapshot_lock:
            inflight = self._snapshot_inflight
            if inflight is not None and (
                min_started_at is None or inflight.started_at >= min_started_at - 0.005
            ):
                return inflight
            if inflight is not None:
                # An older wedged build is already occupying a worker slot;
                # join it if it wasn't stale by much or launch an independent
                # ticket only when idle. Joining avoids leaking duplicate builds.
                return inflight
            fetch = _SnapshotFetch()
            self._snapshot_inflight = fetch

        def _worker() -> None:
            try:
                bundle = self._build_snapshot_bundle()
                fetch.bundle = bundle
            except Exception as exc:  # noqa: BLE001
                fetch.error = exc
                with self._snapshot_lock:
                    self._snapshot_build_errors += 1
                    self._last_snapshot_error = str(exc)
                logger.warning("remote: snapshot build failed: %s", exc)
            finally:
                with self._snapshot_lock:
                    if self._snapshot_inflight is fetch:
                        self._snapshot_inflight = None
                fetch.event.set()

        self._snapshot_executor.submit(_worker)
        return fetch

    def _obtain_snapshot(
        self,
        *,
        timeout_s: float,
        min_started_at: float | None = None,
        allow_stale_fallback: bool = True,
    ) -> tuple[_SnapshotBundle | None, bool, str]:
        """Acquire a fresh snapshot within ``timeout_s`` or honest stale fallback."""
        with self._snapshot_lock:
            latest = self._latest_bundle
            if (
                latest is not None
                and min_started_at is not None
                and latest.built_at >= min_started_at
            ):
                return latest, False, ""

        fetch = self._start_snapshot_fetch(min_started_at=min_started_at)
        finished = fetch.event.wait(timeout=max(0.05, timeout_s))
        if finished and fetch.bundle is not None:
            return fetch.bundle, False, ""

        if fetch.error is not None:
            return None, False, f"SERVER_ERROR:{fetch.error}"

        with self._snapshot_lock:
            latest = self._latest_bundle
        if (
            allow_stale_fallback
            and latest is not None
            and (time.monotonic() - latest.built_at) <= self._config.snapshot_cache_max_age_s
        ):
            return latest, True, "backend snapshot refresh timed out — serving last known state"
        return None, False, "DEADLINE_EXCEEDED:backend snapshot did not complete within deadline"

    def _schedule_initial_snapshot(
        self, conn: _Connection, *, resync: bool, min_started_at: float | None = None
    ) -> None:
        with conn.state_lock:
            if conn.closed or self._stop.is_set():
                return
            conn.initial_snapshot_inflight = True

        def _deliver() -> None:
            try:
                if conn.closed or self._stop.is_set():
                    return
                bundle, is_stale, err_or_reason = self._obtain_snapshot(
                    timeout_s=self._config.snapshot_timeout_s,
                    min_started_at=min_started_at,
                    allow_stale_fallback=not resync,
                )
                if conn.closed or self._stop.is_set():
                    return
                if bundle is None:
                    code, _, detail = err_or_reason.partition(":")
                    self._send_error(
                        conn,
                        code or "DEADLINE_EXCEEDED",
                        detail or "backend snapshot failed",
                    )
                    logger.warning(
                        "remote: initial snapshot failed connection_id=%s code=%s",
                        conn.connection_id,
                        code or "DEADLINE_EXCEEDED",
                    )
                    return
                self._push_bundle(
                    conn,
                    bundle,
                    resync=resync,
                    stale=is_stale,
                    stale_reason=err_or_reason if is_stale else "",
                    track_init=not resync,
                )
            finally:
                with conn.state_lock:
                    conn.initial_snapshot_inflight = False

        try:
            self._delivery_executor.submit(_deliver)
        except RuntimeError:
            with conn.state_lock:
                conn.initial_snapshot_inflight = False

    def _poll_loop(self) -> None:
        interval = max(0.05, self._config.poll_interval_s)
        expected = time.monotonic() + interval
        while not self._stop.is_set():
            now = time.monotonic()
            if expected > now:
                if self._stop.wait(timeout=expected - now):
                    break
                now = time.monotonic()
            self._last_poll_at = now
            self._last_poll_delay_ms = max(0.0, (now - expected) * 1000.0)
            expected = now + interval
            try:
                with self._lock:
                    has_conns = bool(self._connections)
                fetch = self._start_snapshot_fetch()
                wait_s = min(interval, self._config.snapshot_timeout_s) if has_conns else interval
                fetch.event.wait(timeout=wait_s)
                if self._stop.is_set():
                    break
                bundle = fetch.bundle
                with self._snapshot_lock:
                    latest = bundle or self._latest_bundle
                if latest is not None and latest.seq > self._last_published_seq:
                    self._last_published_seq = latest.seq
                    self._publish_bundle(latest)
                else:
                    self._maintain_connections()
            except Exception as exc:  # noqa: BLE001
                logger.warning("remote: poll cycle failed: %s", exc)

    def _with_download_section(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Attach the preserved engine's console facts to the remote snapshot.

        Kept at the transport layer so the gateway's own live snapshot stays
        minimal and its tests/contract are unchanged.
        """
        from app.headless import _download_snapshot, _ensure_download_service

        try:
            manager = getattr(self._gateway, "_ensure_manager", lambda: None)()
            _ensure_download_service(getattr(self._gateway, "_data_dir", ""), manager)
            out = dict(snapshot)
            out["download"] = _download_snapshot()
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: download section unavailable: %s", exc)
            return snapshot

    def _maintain_connections(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
        now = time.monotonic()
        for conn in connections:
            if conn.closed or not conn.tracker.authenticated:
                continue
            self._check_idle(conn)
            if conn.closed:
                continue
            if now - conn.last_ping >= self._config.heartbeat_interval_s:
                conn.last_ping = now
                self._send(
                    conn,
                    make_envelope(
                        "pong",
                        {"server_time": utcnow_iso(), "health": self.health_snapshot()},
                    ),
                )

    def _publish(self, live: dict[str, Any]) -> None:
        """Legacy direct publish helper preserved for unit tests."""
        with self._lock:
            connections = list(self._connections.values())
        if not connections:
            return
        raw_events = live.get("events")
        events: list[Any] = list(raw_events) if isinstance(raw_events, list) else []
        try:
            remote = self._with_download_section(build_snapshot(live))
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: snapshot projection failed: %s", exc)
            return
        hashes = {key: _stable_hash(value) for key, value in remote.items()}
        with self._snapshot_lock:
            self._snapshot_seq += 1
            bundle = _SnapshotBundle(
                seq=self._snapshot_seq,
                remote=remote,
                hashes=hashes,
                events=events,
                built_at=time.monotonic(),
                wall_time=utcnow_iso(),
                build_ms=0.0,
            )
            self._latest_bundle = bundle
        self._publish_bundle(bundle)

    def _publish_bundle(self, bundle: _SnapshotBundle) -> None:
        with self._lock:
            connections = list(self._connections.values())
        if not connections:
            return
        remote_snapshot = bundle.remote
        section_hashes = bundle.hashes
        events = bundle.events
        now = time.monotonic()
        for conn in connections:
            if conn.closed or not conn.tracker.authenticated:
                continue
            with conn.state_lock:
                not_initialized = not conn.initial_snapshot_sent
                init_inflight = conn.initial_snapshot_inflight
            if not_initialized:
                if not init_inflight:
                    # Recovered after an initial cold SNAPSHOT_TIMEOUT: send now!
                    self._push_bundle(conn, bundle, resync=False, track_init=True)
                continue
            if conn.needs_resync:
                conn.needs_resync = False
                with conn.state_lock:
                    conn.last_sections = {}
            with conn.state_lock:
                subs = set(conn.subscriptions)
                prev_hashes = conn.last_sections
                prev_tail = list(conn.last_event_tail)
            changed = {
                key: remote_snapshot[key]
                for key in sorted(remote_snapshot)
                if key in subs and section_hashes.get(key) != prev_hashes.get(key)
            }
            if changed:
                with conn.state_lock:
                    for key in changed:
                        conn.last_sections[key] = section_hashes[key]
                self._send(conn, make_envelope("state_update", {"sections": changed}))
            if "events" in subs:
                fresh, resync = new_entries(prev_tail, list(events))
                with conn.state_lock:
                    conn.last_event_tail = list(events[-50:])
                if resync and fresh:
                    self._push_bundle(conn, bundle, resync=True, track_init=False)
                for entry in fresh:
                    projected = project_event(entry)
                    seq = self._record_event(projected)
                    self._send(conn, make_envelope("event", projected, seq=seq), drop_ok=True)
            self._check_idle(conn)
            if not conn.closed and (now - conn.last_ping >= self._config.heartbeat_interval_s):
                conn.last_ping = now
                self._send(
                    conn,
                    make_envelope(
                        "pong",
                        {"server_time": utcnow_iso(), "health": self.health_snapshot()},
                    ),
                )

    def _record_event(self, projected: dict[str, Any]) -> int:
        with self._lock:
            self._message_seq += 1
            seq = self._message_seq
            self._event_ring.append((seq, projected))
            return seq

    def _replay_missed(self, conn: _Connection, last_seq: int) -> bool:
        """Replay ring events after ``last_seq``; False when a resync is due."""
        with self._lock:
            ring = list(self._event_ring)
        if not ring:
            return True
        if last_seq < ring[0][0] - 1:
            return False
        for seq, projected in ring:
            if seq > last_seq:
                self._send(conn, make_envelope("event", projected, seq=seq), drop_ok=True)
        return True

    def _push_bundle(
        self,
        conn: _Connection,
        bundle: _SnapshotBundle,
        *,
        resync: bool,
        stale: bool = False,
        stale_reason: str = "",
        track_init: bool = False,
    ) -> None:
        remote = bundle.remote
        raw_events = bundle.events
        with conn.state_lock:
            subs = set(conn.subscriptions)
            wanted = {key: remote[key] for key in sorted(remote) if key in subs}
            conn.last_sections = dict(bundle.hashes)
            conn.last_event_tail = list(raw_events[-50:]) if isinstance(raw_events, list) else []
            conn.initial_snapshot_sent = True
            conn.last_snapshot_at = bundle.built_at
            if track_init and conn.init_snapshot_build_ms is None:
                conn.init_snapshot_build_ms = round(bundle.build_ms, 1)
            if stale:
                conn.needs_resync = True
        payload: dict[str, Any] = {
            "snapshot": wanted,
            "server_time": bundle.wall_time or utcnow_iso(),
            "snapshot_seq": bundle.seq,
            "snapshot_age_ms": round(max(0.0, (time.monotonic() - bundle.built_at) * 1000.0), 1),
        }
        if stale:
            payload["stale"] = True
            if stale_reason:
                payload["stale_reason"] = stale_reason
        if resync:
            payload["resync"] = True
        try:
            raw = encode_message(make_envelope("snapshot", payload))
        except ProtocolError as exc:
            logger.warning("remote: snapshot encode failed: %s", exc)
            return
        self._enqueue_frame(
            conn,
            _OutboundFrame(
                opcode=0x1,
                payload=raw,
                enqueued_at=time.monotonic(),
                is_init_snapshot=track_init,
                build_ms=bundle.build_ms,
                snapshot_seq=bundle.seq,
            ),
            drop_ok=False,
        )

    def _push_snapshot(
        self, conn: _Connection, *, resync: bool, snapshot: dict | None = None
    ) -> None:
        """Backward-compatible synchronous push helper used in tests."""
        if snapshot is not None:
            bundle = _SnapshotBundle(
                seq=self._snapshot_seq,
                remote=snapshot,
                hashes={key: _stable_hash(value) for key, value in snapshot.items()},
                events=[],
                built_at=time.monotonic(),
                wall_time=utcnow_iso(),
                build_ms=0.0,
            )
        else:
            try:
                bundle = self._build_snapshot_bundle()
            except Exception as exc:  # noqa: BLE001
                logger.warning("remote: snapshot failed: %s", exc)
                self._send_error(conn, "SERVER_ERROR", "backend snapshot failed")
                return
        self._push_bundle(conn, bundle, resync=resync, track_init=not resync)

    # ── send helpers ──

    def _next_seq(self) -> int:
        with self._lock:
            self._message_seq += 1
            return self._message_seq

    def _enqueue_frame(
        self, conn: _Connection, frame: _OutboundFrame, *, drop_ok: bool = False
    ) -> None:
        if conn.closed:
            return
        try:
            conn.outbox.put_nowait(frame)
        except queue.Full:
            self._dropped_messages += 1
            conn.needs_resync = True
            if not drop_ok:
                logger.warning("remote: slow client (%s); marked for resync", conn.connection_id)

    def _send(self, conn: _Connection, message: dict[str, Any], *, drop_ok: bool = False) -> None:
        if conn.closed:
            return
        if "seq" not in message and message.get("type") in ("event", "state_update"):
            message["seq"] = self._next_seq()
        try:
            raw = encode_message(message)
        except ProtocolError as exc:
            logger.warning("remote: encode failed: %s", exc)
            return
        self._enqueue_frame(conn, _OutboundFrame(opcode=0x1, payload=raw), drop_ok=drop_ok)

    def _send_raw(self, conn: _Connection, raw: bytes) -> None:
        self._enqueue_frame(conn, _OutboundFrame(opcode=0x1, payload=raw), drop_ok=False)

    def _flush_outbox(self, conn: _Connection) -> None:
        """Retained for compatibility; writer thread drains ``conn.outbox``."""
        if conn.closed:
            return
        failed = False
        with conn.send_lock:
            while True:
                try:
                    item = conn.outbox.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    continue
                payload = item.payload if isinstance(item, _OutboundFrame) else item
                opcode = item.opcode if isinstance(item, _OutboundFrame) else 0x1
                try:
                    if opcode == 0x1:
                        send_text(conn.sock, payload)
                    else:
                        write_frame(conn.sock, opcode, payload)
                except OSError:
                    failed = True
                    break
        if failed:
            self._close_connection(conn, _CLOSE_POLICY, reason="flush_failed")

    def _send_error(
        self, conn: _Connection, code: str, detail: str, *, request_id: str | None = None
    ) -> None:
        try:
            raw = encode_message(error_envelope(code, detail, request_id=request_id))
        except ProtocolError:
            return
        if conn.writer_thread is not None and conn.writer_thread.is_alive() and not conn.closed:
            self._enqueue_frame(conn, _OutboundFrame(opcode=0x1, payload=raw), drop_ok=False)
            return
        if conn.send_lock.acquire(blocking=False):
            try:
                send_text(conn.sock, raw)
            except OSError:
                pass
            finally:
                conn.send_lock.release()

    def _check_idle(self, conn: _Connection) -> None:
        if time.monotonic() - conn.last_activity > self._config.idle_timeout_s:
            logger.warning("remote: idle timeout (%s)", conn.connection_id)
            self._close_connection(conn, _CLOSE_POLICY, reason="idle_timeout")

    def _close_connection(
        self, conn: _Connection, code: int, *, reason: str = "", force: bool = False
    ) -> None:
        if reason and not conn.disconnect_reason:
            conn.disconnect_reason = reason
        if conn.closed:
            return
        if not force and conn.writer_thread is not None and conn.writer_thread.is_alive():
            # Allow urgent queued frames (e.g. AUTH_FAILED / NOT_AUTHENTICATED) to
            # flush ahead of close without blocking forever on a stalled peer.
            writer = conn.writer_thread
            with contextlib.suppress(queue.Full):
                conn.outbox.put_nowait(_OutboundFrame(opcode=0x8, payload=struct.pack("!H", code)))
            with contextlib.suppress(queue.Full):
                conn.outbox.put_nowait(None)
            if writer is not threading.current_thread():
                writer.join(timeout=min(0.5, self._config.write_timeout_s))
            if conn.closed:
                return
        conn.closed = True
        with contextlib.suppress(queue.Full):
            conn.outbox.put_nowait(None)
        if not force and conn.send_lock.acquire(blocking=False):
            try:
                with contextlib.suppress(OSError):
                    write_frame(conn.sock, 0x8, struct.pack("!H", code))
            finally:
                conn.send_lock.release()
        with contextlib.suppress(OSError):
            conn.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            conn.sock.close()


def _stable_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def entry_tail_signature(entries: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """Test hook exposing the cursor identity used for event diffing."""
    return [entry_signature(item) for item in entries]
