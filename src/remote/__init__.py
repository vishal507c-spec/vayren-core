"""VAYREN remote communication layer — broker-neutral EC2 ↔ EXE/APK transport.

Adapter only: the existing backend (``app.headless`` stdio protocol,
``LiveTradingService`` snapshots, execution safety gates) stays untouched.
This package projects that state over one controlled WebSocket endpoint
using versioned, authenticated, broker-neutral messages. No strategy,
risk, execution, or broker logic lives here.
"""

from __future__ import annotations

from remote.auth import RemoteRole, load_token_map
from remote.client import (
    ClientConfig,
    ClientConfigError,
    RemoteClient,
    load_client_token,
    new_request_id,
)
from remote.gateway import BackendGateway, HeadlessGateway, new_entries
from remote.protocol import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    Envelope,
    decode_message,
    encode_message,
    make_envelope,
    utcnow_iso,
)
from remote.server import RemoteConfig, RemoteServer
from remote.snapshot import build_snapshot, classify_event

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "BackendGateway",
    "ClientConfig",
    "ClientConfigError",
    "Envelope",
    "HeadlessGateway",
    "RemoteClient",
    "RemoteConfig",
    "RemoteRole",
    "RemoteServer",
    "build_snapshot",
    "classify_event",
    "decode_message",
    "encode_message",
    "load_client_token",
    "load_token_map",
    "make_envelope",
    "new_entries",
    "new_request_id",
    "utcnow_iso",
]
