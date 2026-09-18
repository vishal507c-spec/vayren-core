"""Broker management vocabulary — the generic spec one venue provides.

A venue joins SYSTEM → BROKERS by supplying one :class:`BrokerSpec`
(stdlib dataclass: strings + callables, no SDK here). The composition
root (``02_data/data/provider/factory.py`` for the bundled venue) wires
the concrete adapter/auth modules; the manager stays broker-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrokerSpec:
    """One venue's management wiring (broker-agnostic by construction)."""

    broker_id: str
    display_name: str
    config_service: str
    session_service: str
    required_config: tuple[str, ...]
    masked_config: tuple[str, ...] = ()
    build_adapter: Callable[[str, str], Any] | None = None  # (primary_id, token)
    build_market_data: Callable[[str, str], Any] | None = None
    build_flow: Callable[[], Any] | None = None
    build_session_store: Callable[[Any, str], Any] | None = None
    venue_register: Callable[[Any, Any], tuple[bool, str]] | None = None
    venue_unregister: Callable[[], None] | None = None
    interactive_login: Callable[..., tuple[bool, str]] | None = None
    callback_port: int = 0
    callback_url: str = ""  # the redirect URL the venue app must register
    extra: dict[str, Any] = field(default_factory=dict)
    # ── credential-shape generalization (universal auth; Zerodha-shaped default) ──
    # ``key_field``/``secret_field`` name the STORAGE keys holding the venue's
    # primary id + secret (Zerodha: api_key/api_secret; FYERS: app_id/secret).
    # ``config_key_map`` translates UI-submitted keys to storage keys so the
    # generic two-field configure form keeps working for every venue.
    # ``redirect_uri_field`` (when non-empty) makes the manager pass
    # ``redirect_uri=<stored value or callback_url>`` into interactive_login.
    key_field: str = "api_key"
    secret_field: str = "api_secret"
    redirect_uri_field: str = ""
    config_key_map: dict[str, str] = field(default_factory=dict)
    # ── automatic authentication (Zerodha-experience parity) ──
    # Optional ``(config, session_store) -> (ok, message)`` hook the manager
    # runs on its worker thread when no live session exists (missing or
    # expired) at startup/check time. ``None`` (Zerodha default) keeps the
    # legacy path: LOGIN_REQUIRED until the user presses CONNECT. The hook
    # must be secret-free in its messages and idempotent.
    auto_authenticate: Callable[[dict[str, str], Any], tuple[bool, str]] | None = None


__all__ = ["BrokerSpec"]
