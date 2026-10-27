"""FYERS LIVE activation — venue registration for real order execution.

Supports both:
  1. Environment-based factory registration (for tests and headless runners).
  2. Instance-based registration via BrokerManager (the live app path).
"""

from __future__ import annotations

import contextlib
import os
import threading

from broker.capabilities import CapabilitySet, Domain
from broker.faces import FactoryPlugin, StaticPlugin
from broker.providers.fyers.live_market_data import (
    MARKET_DATA_CAPABILITIES,
    FyersLiveMarketData,
)
from broker.providers.fyers.session_adapter import (
    TRADING_CAPABILITIES,
    FyersSessionAdapter,
)
from broker.registry import BrokerRecord, default_registry

LIVE_VENUE_ID = "fyers-live"
LIVE_DISPLAY_NAME = "Fyers Live"
ENABLE_ENV = "VAYREN_ENABLE_LIVE_VENUE"
API_KEY_ENV = "VAYREN_FYERS_APP_ID"
ACCESS_TOKEN_ENV = "VAYREN_FYERS_ACCESS_TOKEN"


def _combined_capabilities() -> CapabilitySet:
    items = set(TRADING_CAPABILITIES.items) | set(MARKET_DATA_CAPABILITIES.items)
    domains = tuple(sorted({Domain.TRADING, Domain.MARKET_DATA}, key=lambda d: d.value))
    return CapabilitySet(domains=domains, items=frozenset(items))


def live_requirements() -> tuple[str, ...]:
    """Missing activation pieces. No network, safe for per-second UI."""
    missing = []
    if os.environ.get(ENABLE_ENV, "").strip().lower() != "true":
        missing.append(f"{ENABLE_ENV}=true")
    if not os.environ.get(API_KEY_ENV, ""):
        missing.append(API_KEY_ENV)
    if not os.environ.get(ACCESS_TOKEN_ENV, ""):
        missing.append(ACCESS_TOKEN_ENV)
    return tuple(missing)


_shared_lock = threading.Lock()
_shared_adapter: FyersSessionAdapter | None = None
_shared_market_data: FyersLiveMarketData | None = None


def _get_shared_trading_face(app_id: str, access_token: str) -> FyersSessionAdapter:
    global _shared_adapter
    with _shared_lock:
        if (
            _shared_adapter is None
            or getattr(_shared_adapter, "_app_id", None) != app_id
            or getattr(_shared_adapter, "_token", None) != access_token
        ):
            if _shared_adapter is not None:
                with contextlib.suppress(Exception):
                    _shared_adapter.disconnect()
            _shared_adapter = FyersSessionAdapter(app_id=app_id, access_token=access_token)
        return _shared_adapter


def _get_shared_market_data_face(app_id: str, access_token: str) -> FyersLiveMarketData:
    global _shared_market_data
    with _shared_lock:
        if (
            _shared_market_data is None
            or getattr(_shared_market_data, "_app_id", None) != app_id
            or getattr(_shared_market_data, "_token", None) != access_token
        ):
            if _shared_market_data is not None:
                with contextlib.suppress(Exception):
                    _shared_market_data.disconnect()
            _shared_market_data = FyersLiveMarketData(app_id=app_id, access_token=access_token)
        return _shared_market_data


def _reset_shared_instances() -> None:
    """Reset cached singleton instances (for test isolation)."""
    global _shared_adapter, _shared_market_data
    with _shared_lock:
        if _shared_adapter is not None:
            with contextlib.suppress(Exception):
                _shared_adapter.disconnect()
            _shared_adapter = None
        if _shared_market_data is not None:
            with contextlib.suppress(Exception):
                _shared_market_data.disconnect()
            _shared_market_data = None


def register_fyers_live() -> tuple[bool, str]:
    """Register the live venue when explicitly activated via environment. Idempotent."""
    missing = live_requirements()
    if missing:
        return False, f"live venue not activated (missing: {', '.join(missing)})"
    app_id = os.environ.get(API_KEY_ENV, "")
    access_token = os.environ.get(ACCESS_TOKEN_ENV, "")
    registry = default_registry()
    try:
        record = BrokerRecord(
            name=LIVE_VENUE_ID,
            display_name=LIVE_DISPLAY_NAME,
            plugin=FactoryPlugin(
                name=LIVE_VENUE_ID,
                display_name=LIVE_DISPLAY_NAME,
                factories={
                    Domain.TRADING: lambda: _get_shared_trading_face(app_id, access_token),
                    Domain.MARKET_DATA: lambda: _get_shared_market_data_face(app_id, access_token),
                },
                capabilities=_combined_capabilities(),
            ),
            capabilities=_combined_capabilities(),
            faces=(Domain.TRADING, Domain.MARKET_DATA),
        )
    except Exception as exc:
        return False, f"live venue record invalid: {exc}"
    try:
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)
        registry.register(record)
    except Exception as exc:
        return False, f"live venue registration failed: {exc}"
    return True, f"registered {LIVE_VENUE_ID} (trading + market-data)"


def register_fyers_live_instance(
    adapter: object, market_data: object | None = None
) -> tuple[bool, str]:
    """Register an ALREADY-AUTHENTICATED adapter instance as the venue.

    The BrokerManager builds the adapter from the securely stored session
    and hands it here (StaticPlugin): ``resolve_broker`` then returns the
    exact same authenticated object to every LiveSession — one token, one
    venue, no per-session re-auth. Idempotent (unregister-then-register).
    """
    if adapter is None:
        return False, "no authenticated adapter provided"
    global _shared_adapter, _shared_market_data
    with _shared_lock:
        if isinstance(adapter, FyersSessionAdapter):
            _shared_adapter = adapter
        if isinstance(market_data, FyersLiveMarketData):
            _shared_market_data = market_data
    face_map: dict[Domain, object] = {Domain.TRADING: adapter}
    if market_data is not None:
        face_map[Domain.MARKET_DATA] = market_data
    registry = default_registry()
    try:
        record = BrokerRecord(
            name=LIVE_VENUE_ID,
            display_name=LIVE_DISPLAY_NAME,
            plugin=StaticPlugin(
                name=LIVE_VENUE_ID,
                display_name=LIVE_DISPLAY_NAME,
                face_map=face_map,
                capabilities=_combined_capabilities(),
            ),
            capabilities=_combined_capabilities(),
            faces=tuple(face_map),
        )
    except Exception as exc:
        return False, f"live venue record invalid: {exc}"
    try:
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)
        registry.register(record)
    except Exception as exc:
        return False, f"live venue registration failed: {exc}"
    return True, f"registered {LIVE_VENUE_ID} (authenticated instance)"


def unregister_fyers_live() -> None:
    """Unregister the live venue (SYSTEM -> BROKERS disconnect or session reset)."""
    _reset_shared_instances()
    registry = default_registry()
    if LIVE_VENUE_ID in registry:
        registry.unregister(LIVE_VENUE_ID)


__all__ = [
    "LIVE_VENUE_ID",
    "LIVE_DISPLAY_NAME",
    "ENABLE_ENV",
    "API_KEY_ENV",
    "ACCESS_TOKEN_ENV",
    "live_requirements",
    "register_fyers_live",
    "register_fyers_live_instance",
    "unregister_fyers_live",
    "_reset_shared_instances",
]
