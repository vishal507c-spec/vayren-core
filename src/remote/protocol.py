"""Versioned broker-neutral message contract (remote transport).

Every message on the wire is one JSON object (one WebSocket text frame)::

    {"type": ..., "v": 1, "ts": ..., "seq": ..., "request_id": ..., "payload": {...}}

``type`` names the category, ``v`` pins the schema, ``ts`` is the sender's
UTC timestamp, ``seq`` orders server-to-client messages per connection,
``request_id`` correlates commands with their results. Nothing
broker-specific ever travels here — clients only see VAYREN-normalized
shapes built by :mod:`remote.snapshot`.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any

#: Current wire schema. Bump only with a documented migration; the server
#: keeps accepting every version in ``SUPPORTED_SCHEMA_VERSIONS``.
SCHEMA_VERSION = 1

#: Schema versions this backend speaks. Older clients get a clear
#: ``UNSUPPORTED_VERSION`` error, never a silent misparse.
SUPPORTED_SCHEMA_VERSIONS = (1,)

#: Largest single message accepted (1 MiB). Bounds memory per connection.
MAX_MESSAGE_BYTES = 1_000_000

#: Client → server categories.
CLIENT_MESSAGE_TYPES = ("hello", "ping", "subscribe", "unsubscribe", "command")

#: Server → client categories.
SERVER_MESSAGE_TYPES = (
    "welcome",
    "pong",
    "snapshot",
    "state_update",
    "event",
    "command_result",
    "error",
)

#: Machine-readable error codes carried in ``error`` payloads.
ERROR_CODES = (
    "NOT_AUTHENTICATED",
    "AUTH_FAILED",
    "NOT_AUTHORIZED",
    "BAD_MESSAGE",
    "UNSUPPORTED_VERSION",
    "UNKNOWN_COMMAND",
    "COMMAND_REJECTED",
    "RATE_LIMITED",
    "SERVER_ERROR",
)


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (wire timestamps)."""
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Envelope:
    """One validated wire message (immutable)."""

    msg_type: str
    payload: dict[str, Any]
    version: int = SCHEMA_VERSION
    timestamp: str = ""
    seq: int | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope as the exact wire object."""
        out: dict[str, Any] = {
            "type": self.msg_type,
            "v": self.version,
            "ts": self.timestamp or utcnow_iso(),
            "payload": dict(self.payload),
        }
        if self.seq is not None:
            out["seq"] = self.seq
        if self.request_id is not None:
            out["request_id"] = self.request_id
        return out

    @staticmethod
    def from_dict(raw: dict[str, Any], *, from_client: bool) -> Envelope:
        """Validate a decoded object; raises :class:`ProtocolError`."""
        if not isinstance(raw, dict):
            raise ProtocolError("BAD_MESSAGE", "message must be a JSON object")
        msg_type = raw.get("type")
        if not isinstance(msg_type, str) or not msg_type:
            raise ProtocolError("BAD_MESSAGE", "message needs a string 'type'")
        allowed = CLIENT_MESSAGE_TYPES if from_client else SERVER_MESSAGE_TYPES
        if msg_type not in allowed:
            raise ProtocolError("BAD_MESSAGE", f"unknown message type {msg_type!r}")
        version = raw.get("v", SCHEMA_VERSION)
        if not isinstance(version, int) or version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ProtocolError("UNSUPPORTED_VERSION", f"unsupported schema version {version!r}")
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            raise ProtocolError("BAD_MESSAGE", "'payload' must be an object")
        timestamp = raw.get("ts", "")
        if not isinstance(timestamp, str):
            raise ProtocolError("BAD_MESSAGE", "'ts' must be a string")
        seq = raw.get("seq")
        if seq is not None and (not isinstance(seq, int) or seq < 0):
            raise ProtocolError("BAD_MESSAGE", "'seq' must be a non-negative integer")
        request_id = raw.get("request_id")
        if request_id is not None and (
            not isinstance(request_id, str) or not request_id or len(request_id) > 128
        ):
            raise ProtocolError("BAD_MESSAGE", "'request_id' must be a short string")
        return Envelope(
            msg_type=msg_type,
            payload=payload,
            version=version,
            timestamp=timestamp,
            seq=seq,
            request_id=request_id,
        )


class ProtocolError(Exception):
    """A rejected wire message; carries the machine-readable error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code if code in ERROR_CODES else "BAD_MESSAGE"
        self.detail = str(detail)


def make_envelope(
    msg_type: str,
    payload: dict[str, Any] | None = None,
    *,
    seq: int | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build one server-side wire object (validated before it leaves)."""
    if msg_type not in SERVER_MESSAGE_TYPES:
        raise ProtocolError("BAD_MESSAGE", f"unknown server message type {msg_type!r}")
    return Envelope(
        msg_type=msg_type, payload=dict(payload or {}), seq=seq, request_id=request_id
    ).to_dict()


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize one wire object to UTF-8 bytes (size-bounded)."""
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("BAD_MESSAGE", "message exceeds size limit")
    return raw


def decode_message(data: bytes, *, from_client: bool) -> Envelope:
    """Parse and validate one inbound frame payload."""
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("BAD_MESSAGE", "message exceeds size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("BAD_MESSAGE", "message is not valid UTF-8") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ProtocolError("BAD_MESSAGE", f"message is not valid JSON: {exc}") from exc
    return Envelope.from_dict(raw, from_client=from_client)


def error_envelope(code: str, detail: str, *, request_id: str | None = None) -> dict[str, Any]:
    """Build one ``error`` wire object (always safe to send pre-auth)."""
    safe_code = code if code in ERROR_CODES else "SERVER_ERROR"
    return make_envelope("error", {"code": safe_code, "detail": str(detail)}, request_id=request_id)
