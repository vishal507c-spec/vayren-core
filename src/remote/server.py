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

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_HANDSHAKE_LIMIT = 16_384
_FRAME_LIMIT = 1_000_000
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

#: Remote command names accepted (each maps 1:1 to the headless action path).
MUTATING_ACTIONS = ("setup", "mode", "start", "stop", "halt", "arm", "select_symbol", "tick")

#: Event channels a client may subscribe to (snapshot sections + event stream).
EVENT_CHANNELS = (
    "broker",
    "market",
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
    "events",
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
    tls_cert_file: str = ""
    tls_key_file: str = ""
    allow_plaintext_non_loopback: bool = False

    def check(self) -> None:
        """Reject unsafe bindings before any socket is opened."""
        if self.port < 0 or self.port > 65535:
            raise ConfigError(f"invalid port {self.port}")
        if self.max_connections < 1:
            raise ConfigError("max_connections must be at least 1")
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
    needs_resync: bool = False
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False


@dataclass(frozen=True, slots=True)
class _IdempotentResult:
    payload: dict[str, Any]
    stored_at: float


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
            self._close_connection(conn, _CLOSE_NORMAL)
        for thread in self._threads:
            thread.join(timeout=5.0)

    # ── accept ──

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
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
        logger.info("remote: client connected (%s)", conn.connection_id)
        sock.settimeout(1.0)
        deadline = time.monotonic() + self._config.auth_timeout_s
        try:
            while not self._stop.is_set():
                if not conn.tracker.authenticated and time.monotonic() > deadline:
                    self._send_error(conn, "NOT_AUTHENTICATED", "authentication timed out")
                    self._close_connection(conn, _CLOSE_POLICY)
                    return
                try:
                    opcode, payload = read_frame(sock)
                except TimeoutError:
                    self._check_idle(conn)
                    continue
                except (ConnectionError, ProtocolError, OSError, struct.error) as exc:
                    logger.warning("remote: read failed (%s): %s", conn.connection_id, exc)
                    self._close_connection(conn, _CLOSE_POLICY)
                    return
                conn.last_activity = time.monotonic()
                if opcode == 0x8:
                    self._close_connection(conn, _CLOSE_NORMAL)
                    return
                if opcode == 0x9:
                    write_frame(sock, 0xA, payload[:125])
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
            self._unregister(conn)
            with contextlib.suppress(OSError):
                sock.close()
            logger.info("remote: client disconnected (%s)", conn.connection_id)

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
                self._close_connection(conn, _CLOSE_POLICY)
                return True
            return False
        conn.tracker.note_attempt(entry)
        conn.role = entry.role
        conn.authed_at = time.monotonic()
        conn.last_activity = conn.authed_at
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
        self._push_snapshot(conn, resync=False)
        return False

    def _on_rehello(self, conn: _Connection, _message: Any) -> None:
        self._send_error(conn, "BAD_MESSAGE", "already authenticated")

    # ── subscriptions / heartbeat ──

    def _on_ping(self, conn: _Connection, _message: Any) -> None:
        self._send(conn, make_envelope("pong", {"server_time": utcnow_iso()}))

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
        conn.subscriptions = wanted
        if isinstance(last_seq, int) and last_seq >= 0:
            replayed = self._replay_missed(conn, last_seq)
            if not replayed:
                self._push_snapshot(conn, resync=True)
        else:
            self._push_snapshot(conn, resync=False)

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
        headless_action = self._translate_command(action, params, message.request_id)
        if headless_action is None:
            self._send_error(
                conn,
                "COMMAND_REJECTED",
                "command refused before reaching the backend",
                request_id=message.request_id,
            )
            return
        try:
            fresh = self._gateway.apply_command(headless_action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: backend command failed: %s", exc)
            self._send_error(
                conn, "SERVER_ERROR", "backend command failed", request_id=message.request_id
            )
            return
        result = self._command_result(action, fresh)
        self._remember_command(message.request_id, result)
        self._send(conn, make_envelope("command_result", result, request_id=message.request_id))
        conn.needs_resync = True

    def _translate_command(
        self, action: str, params: Any, request_id: str
    ) -> dict[str, Any] | None:
        """Map a remote command onto the existing headless action shape."""
        args = dict(params) if isinstance(params, dict) else {}
        if action == "start":
            if args.get("confirmed") is not True:
                return None
            return {"action": "start", "confirmed": True, "request_id": request_id}
        if action == "setup":
            allowed = ("strategy_name", "strategy", "symbols", "timeframe", "mode", "quantity")
            return {"action": "setup", **{k: v for k, v in args.items() if k in allowed}}
        if action == "mode":
            if args.get("mode") not in ("PAPER", "LIVE"):
                return None
            return {"action": "mode", "mode": args["mode"]}
        if action in ("stop", "halt", "arm", "tick"):
            return {"action": action}
        if action == "select_symbol":
            symbol = args.get("symbol", "")
            if not isinstance(symbol, str) or not symbol.strip():
                return None
            return {"action": "select_symbol", "symbol": symbol.strip()}
        return None

    def _command_result(self, action: str, fresh: dict[str, Any]) -> dict[str, Any]:
        """Wrap the backend's own post-command snapshot as the result."""
        if not isinstance(fresh, dict):
            return {"ok": False, "action": action, "detail": "backend gave no snapshot"}
        blockers = list(fresh.get("start_blockers", []) or [])
        running = str(fresh.get("session_status", "") or "").upper() == "RUNNING"
        ok = running if action == "start" else True
        return {
            "ok": ok,
            "action": action,
            "cached": False,
            "session_status": str(fresh.get("session_status", "") or ""),
            "status_reason": str(fresh.get("status_reason", "") or ""),
            "start_blockers": blockers,
            "snapshot": build_snapshot(fresh),
        }

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

    # ── publish path (poll thread) ──

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(max(0.1, self._config.poll_interval_s))
            if self._stop.is_set():
                break
            try:
                live = self._gateway.get_snapshot()
            except Exception as exc:  # noqa: BLE001
                logger.warning("remote: backend snapshot failed: %s", exc)
                continue
            if not isinstance(live, dict):
                continue
            self._publish(live)

    def _publish(self, live: dict[str, Any]) -> None:
        with self._lock:
            connections = list(self._connections.values())
        if not connections:
            return
        raw_events = live.get("events")
        events: list[Any] = list(raw_events) if isinstance(raw_events, list) else []
        try:
            remote_snapshot = build_snapshot(live)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: snapshot projection failed: %s", exc)
            return
        section_hashes = {key: _stable_hash(value) for key, value in remote_snapshot.items()}
        for conn in connections:
            if not conn.tracker.authenticated:
                continue
            if conn.needs_resync:
                conn.last_sections = {}
                conn.needs_resync = False
            changed = {
                key: remote_snapshot[key]
                for key in sorted(remote_snapshot)
                if key in conn.subscriptions
                and section_hashes.get(key) != conn.last_sections.get(key)
            }
            for key in changed:
                conn.last_sections[key] = section_hashes[key]
            if changed:
                self._send(conn, make_envelope("state_update", {"sections": changed}))
            if "events" in conn.subscriptions:
                fresh, resync = new_entries(conn.last_event_tail, list(events))
                conn.last_event_tail = list(events[-50:])
                if resync and fresh:
                    self._push_snapshot(conn, resync=True, snapshot=remote_snapshot)
                for entry in fresh:
                    projected = project_event(entry)
                    seq = self._record_event(projected)
                    self._send(conn, make_envelope("event", projected, seq=seq), drop_ok=True)
            self._check_idle(conn)
            if time.monotonic() - conn.last_ping >= self._config.heartbeat_interval_s:
                conn.last_ping = time.monotonic()
                self._send(conn, make_envelope("pong", {"server_time": utcnow_iso()}))

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

    def _push_snapshot(
        self, conn: _Connection, *, resync: bool, snapshot: dict | None = None
    ) -> None:
        try:
            if snapshot is not None:
                remote = snapshot
            else:
                remote = build_snapshot(self._gateway.get_snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote: snapshot failed: %s", exc)
            self._send_error(conn, "SERVER_ERROR", "backend snapshot failed")
            return
        wanted = {key: remote[key] for key in sorted(remote) if key in conn.subscriptions}
        conn.last_sections = {key: _stable_hash(value) for key, value in remote.items()}
        try:
            raw_events = self._gateway.get_snapshot().get("events", [])
        except Exception:  # noqa: BLE001
            raw_events = []
        conn.last_event_tail = list(raw_events[-50:]) if isinstance(raw_events, list) else []
        payload: dict[str, Any] = {"snapshot": wanted, "server_time": utcnow_iso()}
        if resync:
            payload["resync"] = True
        self._send(conn, make_envelope("snapshot", payload))

    # ── send helpers ──

    def _next_seq(self) -> int:
        with self._lock:
            self._message_seq += 1
            return self._message_seq

    def _send(self, conn: _Connection, message: dict[str, Any], *, drop_ok: bool = False) -> None:
        if "seq" not in message and message.get("type") in ("event", "state_update"):
            message["seq"] = self._next_seq()
        try:
            raw = encode_message(message)
        except ProtocolError as exc:
            logger.warning("remote: encode failed: %s", exc)
            return
        try:
            conn.outbox.put_nowait(raw)
        except queue.Full:
            self._dropped_messages += 1
            conn.needs_resync = True
            if not drop_ok:
                logger.warning("remote: slow client (%s); marked for resync", conn.connection_id)
        else:
            self._flush_outbox(conn)

    def _send_raw(self, conn: _Connection, raw: bytes) -> None:
        try:
            conn.outbox.put_nowait(raw)
        except queue.Full:
            conn.needs_resync = True
        else:
            self._flush_outbox(conn)

    def _flush_outbox(self, conn: _Connection) -> None:
        while True:
            try:
                raw = conn.outbox.get_nowait()
            except queue.Empty:
                return
            try:
                with conn.send_lock:
                    send_text(conn.sock, raw)
            except OSError:
                self._close_connection(conn, _CLOSE_POLICY)
                return

    def _send_error(
        self, conn: _Connection, code: str, detail: str, *, request_id: str | None = None
    ) -> None:
        try:
            raw = encode_message(error_envelope(code, detail, request_id=request_id))
        except ProtocolError:
            return
        try:
            with conn.send_lock:
                send_text(conn.sock, raw)
        except OSError:
            pass

    def _check_idle(self, conn: _Connection) -> None:
        if time.monotonic() - conn.last_activity > self._config.idle_timeout_s:
            logger.warning("remote: idle timeout (%s)", conn.connection_id)
            self._close_connection(conn, _CLOSE_POLICY)

    def _close_connection(self, conn: _Connection, code: int) -> None:
        if conn.closed:
            return
        conn.closed = True
        try:
            with conn.send_lock:
                write_frame(conn.sock, 0x8, struct.pack("!H", code))
        except OSError:
            pass
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
