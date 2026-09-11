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
    build_adapter: Callable[[str, str], Any] | None = None  # (api_key, token)
    build_market_data: Callable[[str, str], Any] | None = None
    build_flow: Callable[[], Any] | None = None
    build_session_store: Callable[[Any, str], Any] | None = None
    venue_register: Callable[[Any, Any], tuple[bool, str]] | None = None
    venue_unregister: Callable[[], None] | None = None
    interactive_login: Callable[..., tuple[bool, str]] | None = None
    callback_port: int = 0
    callback_url: str = ""  # the redirect URL the venue app must register
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = ["BrokerSpec"]
