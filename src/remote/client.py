"""WSS client transport for graphical EXE/APK clients (schema v1, read-first).

Smallest compatible transport over the existing versioned protocol
(:mod:`remote.protocol`): ``hello`` first, then ``welcome`` + ``snapshot``,
incremental ``state_update`` / ``event`` frames ordered by ``seq``,
``subscribe{channels, last_seq}`` resubscription, ``resync`` full-replace,
and caller-owned ``request_id`` reuse for command retries.

Transport only: this module never starts trading and never sends a command
on its own — commands leave only via an explicit :meth:`RemoteClient.send_command`
call, and every result still passes through the backend safety gates.
Read-only clients (snapshot / events / ping / subscribe) never mutate state.

Token handling (never hardcoded, never logged):

- ``VAYREN_REMOTE_TOKEN`` environment value (preferred — the EXE injects
  it at launch from the platform secure store: Windows Credential
  Manager / DPAPI on the EXE side, ``/etc/vayren/remote.env`` mode 0600
  on EC2; the client itself never persists the token anywhere).
- ``VAYREN_REMOTE_CLIENT_TOKEN_FILE`` — file holding exactly the raw
  token (mode 0600 recommended) for hosts without an env injector.

TLS is always verified against system CAs; there is no insecure flag.
For ``wss`` endpoints always use the DNS name from the served URL
(TLS certificates never cover bare IPs).
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from remote.auth import MIN_TOKEN_LENGTH
from remote.protocol import Envelope, decode_message

logger = logging.getLogger(__name__)

_HANDSHAKE_LIMIT = 16_384
_FRAME_LIMIT = 1_000_000


class ClientConfigError(Exception):
    """Client misconfigured — refuse to connect (fail closed, no token logged)."""


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Where the gateway lives (values only; the gateway bind never changes)."""

    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/vayren/v1"
    use_tls: bool = False
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 30.0
    auto_ping_interval_s: float = 20.0


def load_client_token(env: dict[str, str] | None = None) -> str:
    """Load this client's bearer token (value never logged by this module).

    Sources: ``VAYREN_REMOTE_TOKEN`` first, then the raw-token file at
    ``VAYREN_REMOTE_CLIENT_TOKEN_FILE``. Short/blank values fail closed.
    """
    source = env if env is not None else os.environ
    candidate = (source.get("VAYREN_REMOTE_TOKEN", "") or "").strip()
    if len(candidate) >= MIN_TOKEN_LENGTH:
        return candidate
    token_file = (source.get("VAYREN_REMOTE_CLIENT_TOKEN_FILE", "") or "").strip()
    if token_file:
        try:
            with open(token_file, encoding="utf-8") as handle:
                candidate = handle.read().strip()
        except OSError as exc:
            raise ClientConfigError(f"cannot read VAYREN_REMOTE_CLIENT_TOKEN_FILE: {exc}") from exc
        if len(candidate) >= MIN_TOKEN_LENGTH:
            return candidate
    raise ClientConfigError(
        "no client token configured (set VAYREN_REMOTE_TOKEN or VAYREN_REMOTE_CLIENT_TOKEN_FILE)"
    )


def new_request_id() -> str:
    """Fresh idempotency key; callers reuse the SAME value across retries."""
    return uuid.uuid4().hex


def _send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    """Write one masked client frame (server frames are never masked)."""
    if len(payload) >= 126:
        header = bytes([0x80 | (opcode & 0x0F), 0x80 | 126]) + struct.pack("!H", len(payload))
    else:
        header = bytes([0x80 | (opcode & 0x0F), 0x80 | len(payload)])
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    sock.sendall(header + mask + masked)


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


def _client_message(msg_type: str, payload: dict[str, Any], request_id: str | None = None) -> bytes:
    """Serialize one client→server object (validated inbound by the gateway)."""
    wire = Envelope(msg_type=msg_type, payload=dict(payload), request_id=request_id).to_dict()
    return json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class RemoteClient:
    """One authenticated gateway connection (UI thread owns the callbacks).

    Callbacks receive decoded payloads only — the token is never passed to
    any callback and never appears in logs. ``last_seq`` tracks the newest
    seen ``event``/``state_update`` sequence for resubscription.
    """

    def __init__(
        self,
        config: ClientConfig,
        token: str,
        *,
        on_snapshot: Callable[[dict[str, Any], bool], None] | None = None,
        on_state_update: Callable[[dict[str, Any], int], None] | None = None,
        on_event: Callable[[dict[str, Any], int], None] | None = None,
        on_command_result: Callable[[dict[str, Any], str | None], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        if len((token or "").strip()) < MIN_TOKEN_LENGTH:
            raise ClientConfigError("refusing to connect without a usable client token")
        self._config = config
        self._token = token.strip()
        self._on_snapshot = on_snapshot
        self._on_state_update = on_state_update
        self._on_event = on_event
        self._on_command_result = on_command_result
        self._on_error = on_error
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._inbox: dict[str, list[dict[str, Any]]] = {}
        self._inbox_lock = threading.Lock()
        self._inbox_event = threading.Event()
        self.role: str | None = None
        self.connection_id: str | None = None
        self.snapshot: dict[str, Any] = {}
        self.last_seq: int = 0
        self.channels: set[str] = set()
        self._last_send = time.monotonic()

    def __repr__(self) -> str:
        return f"RemoteClient({self._config.host}:{self._config.port} token=REDACTED)"

    # ── connect / reconnect / close ──

    def connect(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Handshake + hello; returns (welcome, snapshot). No command is sent."""
        if self._sock is not None:
            raise ClientConfigError("already connected")
        sock = socket.create_connection(
            (self._config.host, self._config.port), timeout=self._config.connect_timeout_s
        )
        try:
            if self._config.use_tls:
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=self._config.host
                )
            self._handshake(sock)
            self._sock = sock
            self._stop.clear()
            self._reader = threading.Thread(
                target=self._recv_loop, name="vayren-ws-read", daemon=True
            )
            self._reader.start()
            self._send_text(_client_message("hello", {"token": self._token}))
            logger.info("remote client: hello sent (token withheld)")
            welcome = self._wait_for("welcome", timeout=self._config.read_timeout_s)
            snapshot = self._wait_for("snapshot", timeout=self._config.read_timeout_s)
        except Exception:
            with contextlib.suppress(OSError):
                sock.close()
            self._sock = None
            raise
        self.role = str(welcome.get("role", "") or "")
        self.connection_id = str(welcome.get("connection_id", "") or "")
        self._apply_snapshot(snapshot)
        return welcome, snapshot

    def reconnect(self, channels: Sequence[str] | None = None) -> dict[str, Any]:
        """Reconnect + resubscribe from ``last_seq``; resync replaces state."""
        self.close()
        _welcome, snapshot = self.connect()
        self.subscribe(channels, last_seq=self.last_seq)
        fresh = self._wait_for("snapshot", timeout=self._config.read_timeout_s)
        self._apply_snapshot(fresh)
        return fresh if fresh else snapshot

    def close(self) -> None:
        """Shut down the connection (never touches trading state)."""
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5.0)

    # ── read-only operations (never mutate backend state) ──

    def ping(self) -> None:
        """Heartbeat (server answers ``pong``)."""
        self._send_text(_client_message("ping", {}))

    def subscribe(self, channels: Sequence[str] | None = None, last_seq: int | None = None) -> None:
        """(Re)subscribe; ``last_seq`` replays missed events or triggers resync."""
        payload: dict[str, Any] = {}
        if channels:
            payload["channels"] = list(channels)
        if last_seq is not None and last_seq >= 0:
            payload["last_seq"] = int(last_seq)
        self._send_text(_client_message("subscribe", payload))

    def unsubscribe(self, channels: Sequence[str]) -> None:
        """Drop event channels."""
        self._send_text(_client_message("unsubscribe", {"channels": list(channels)}))

    # ── control path (explicit only; caller owns request_id retries) ──

    def send_command(
        self, action: str, params: dict[str, Any] | None = None, request_id: str | None = None
    ) -> str:
        """Send one control action; returns the idempotency key for retries.

        Retries MUST reuse the returned value — the gateway replays the
        cached result instead of executing twice.
        """
        key = request_id or new_request_id()
        self._send_text(
            _client_message("command", {"action": action, "params": dict(params or {})}, key)
        )
        return key

    def wait_for_result(self, request_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Block for one ``command_result``/``error`` correlated by id."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for kind in ("command_result", "error"):
                message = self._pop(kind, request_id)
                if message is not None:
                    return message
            time.sleep(0.02)
        return None

    # ── wire internals ──

    def _handshake(self, sock: socket.socket) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock.sendall(
            (
                f"GET {self._config.path} HTTP/1.1\r\nHost: {self._config.host}\r\n"
                f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("latin-1")
        )
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed by peer")
            data += chunk
            if len(data) > _HANDSHAKE_LIMIT:
                raise ConnectionError("handshake headers too large")
        status = data.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise ConnectionError(f"handshake rejected: {status!r}")

    def _send_text(self, payload: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise ClientConfigError("not connected")
        with self._send_lock:
            _send_frame(sock, 0x1, payload)
        self._last_send = time.monotonic()

    def _recv_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            while not self._stop.is_set():
                if time.monotonic() - self._last_send >= self._config.auto_ping_interval_s:
                    with contextlib.suppress(OSError, ClientConfigError):
                        self._send_text(_client_message("ping", {}))
                sock.settimeout(1.0)
                try:
                    opcode, payload = self._read_frame(sock)
                except TimeoutError:
                    continue
                except (ConnectionError, OSError, struct.error) as exc:
                    self._note_error("DISCONNECTED", f"connection lost: {exc}")
                    break
                if opcode == 0x8:
                    self._note_error("DISCONNECTED", "server closed the connection")
                    break
                if opcode == 0x9:
                    with contextlib.suppress(OSError):
                        self._send_pong(sock, payload[:125])
                    continue
                if opcode != 0x1:
                    continue
                self._dispatch(payload)
        finally:
            self._stop.set()

    def _read_frame(self, sock: socket.socket) -> tuple[int, bytes]:
        header = _recv_exact(sock, 2)
        length = header[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", _recv_exact(sock, 2))
        elif length == 127:
            (length,) = struct.unpack("!Q", _recv_exact(sock, 8))
        if length > _FRAME_LIMIT:
            raise ConnectionError("frame exceeds size limit")
        payload = _recv_exact(sock, length) if length else b""
        return header[0] & 0x0F, payload

    def _send_pong(self, sock: socket.socket, payload: bytes) -> None:
        with self._send_lock:
            header = bytes([0x8A, len(payload)])
            sock.sendall(header + payload)

    def _dispatch(self, payload: bytes) -> None:
        try:
            message = decode_message(payload, from_client=False)
        except Exception as exc:  # noqa: BLE001 - wire garbage must not kill the reader
            logger.warning("remote client: bad server frame: %s", exc)
            return
        kind = message.msg_type
        body = message.payload
        seq = message.seq or 0
        if kind in ("event", "state_update") and seq > 0:
            self.last_seq = max(self.last_seq, seq)
        if kind == "snapshot":
            self._apply_snapshot(body)
        elif kind == "state_update":
            for section in body.get("sections", {}):
                self.channels.add(str(section))
            if self._on_state_update is not None:
                self._on_state_update(body, seq)
        elif kind == "event" and self._on_event is not None:
            self._on_event(body, seq)
        elif kind == "command_result" and self._on_command_result is not None:
            self._on_command_result(body, message.request_id)
        elif kind == "error" and self._on_error is not None:
            self._on_error(str(body.get("code", "") or ""), str(body.get("detail", "") or ""))
        with self._inbox_lock:
            self._inbox.setdefault(kind, []).append(
                {"type": kind, "payload": body, "seq": seq, "request_id": message.request_id}
            )
            self._inbox_event.set()

    def _apply_snapshot(self, body: dict[str, Any]) -> None:
        resync = bool(body.get("resync", False))
        snapshot = body.get("snapshot", body)
        if isinstance(snapshot, dict):
            self.snapshot = dict(snapshot)
            self.channels = {str(key) for key in snapshot}
        if self._on_snapshot is not None:
            self._on_snapshot(dict(self.snapshot), resync)

    def _wait_for(self, kind: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._pop(kind)
            if message is not None:
                return dict(message["payload"])
            self._inbox_event.clear()
            self._inbox_event.wait(timeout=max(0.05, deadline - time.monotonic()))
        raise TimeoutError(f"timed out waiting for {kind}")

    def _pop(self, kind: str, request_id: str | None = None) -> dict[str, Any] | None:
        with self._inbox_lock:
            queue = self._inbox.get(kind, [])
            for index, message in enumerate(queue):
                if request_id is None or message.get("request_id") == request_id:
                    return dict(queue.pop(index))
        return None

    def _note_error(self, code: str, detail: str) -> None:
        with self._inbox_lock:
            self._inbox.setdefault("error", []).append(
                {
                    "type": "error",
                    "payload": {"code": code, "detail": detail},
                    "seq": 0,
                    "request_id": None,
                }
            )
            self._inbox_event.set()
        if self._on_error is not None:
            self._on_error(code, detail)


__all__ = [
    "ClientConfig",
    "ClientConfigError",
    "RemoteClient",
    "load_client_token",
    "new_request_id",
]
