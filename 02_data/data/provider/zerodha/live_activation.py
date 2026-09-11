"""Zerodha LIVE activation — the ONLY path from history-only to real trading.

EXACT external configuration required (nothing else enables real orders):

  1. A Zerodha trading account with Kite Connect API access enabled
     (api_key = the Kite Connect "API key" from https://developers.kite.trade).
  2. Environment variable ``VAYREN_ENABLE_LIVE_VENUE`` set to the exact
     string ``true`` (any other value, including ``1``/``yes``, stays OFF).
  3. Environment variable ``VAYREN_ZERODHA_API_KEY`` = the Kite API key.
  4. Environment variable ``VAYREN_ZERODHA_ACCESS_TOKEN`` = a fresh daily
     access token. Tokens expire every morning; bootstrap one with:
       a. :func:`login_url` → open in a browser → log in to Zerodha
          (password + TOTP, on the Zerodha site itself, never in VAYREN);
       b. copy the ``request_token`` from the redirected URL;
       c. :func:`exchange_request_token` with the API key + request_token +
          API secret (typed at runtime, never persisted by this package);
       d. export the returned access token as ``VAYREN_ZERODHA_ACCESS_TOKEN``.
  5. In VAYREN: select broker ``Zerodha``, mode LIVE, explicit START
     confirmation. :func:`register_zerodha_live` runs inside the START
     path only — never at import, never silently.

What registration does: adds a SEPARATE venue id ``"zerodha-live"``
(TRADING + MARKET_DATA faces). The ``"zerodha"`` history record is
untouched, so historical downloads keep resolving exactly as today.
"""

from __future__ import annotations

import os

from broker.capabilities import CapabilitySet, Domain
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord, default_registry

from data.provider.zerodha.live_market_data import MARKET_DATA_CAPABILITIES
from data.provider.zerodha.live_trading import ACCESS_TOKEN_ENV, API_KEY_ENV, TRADING_CAPABILITIES

LIVE_VENUE_ID = "zerodha-live"
LIVE_DISPLAY_NAME = "Zerodha Live"
ENABLE_ENV = "VAYREN_ENABLE_LIVE_VENUE"


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


def login_url(api_key: str) -> str:
    """Operator browser-login URL for the request_token step."""
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:
        raise RuntimeError("kiteconnect SDK not installed (pip install kiteconnect>=5)") from exc
    return str(KiteConnect(api_key=str(api_key)).login_url())


def exchange_request_token(api_key: str, request_token: str, api_secret: str) -> str:
    """One-shot request_token → access_token exchange.

    The secret is used once inside this call and never stored, logged or
    returned — only the access token comes back (the operator exports it).
    """
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:
        raise RuntimeError("kiteconnect SDK not installed (pip install kiteconnect>=5)") from exc
    session = KiteConnect(api_key=str(api_key)).generate_session(
        str(request_token), api_secret=str(api_secret)
    )
    token = ""
    if isinstance(session, dict):
        token = str(session.get("access_token", "") or "")
    if not token:
        raise RuntimeError("venue returned no access token")
    return token


def register_zerodha_live() -> tuple[bool, str]:
    """Register the live venue when explicitly activated. Idempotent.

    Returns (registered, reason). ``False`` with an exact reason is the
    normal OFF state — never an exception, never a partial registration.
    """
    missing = live_requirements()
    if missing:
        return False, f"live venue not activated (missing: {', '.join(missing)})"
    try:
        from data.provider.zerodha.live_market_data import ZerodhaMarketDataFace
        from data.provider.zerodha.live_trading import ZerodhaTradingAdapter
    except ImportError as exc:
        return False, f"live venue unavailable: {exc}"
    registry = default_registry()
    try:
        record = BrokerRecord(
            name=LIVE_VENUE_ID,
            display_name=LIVE_DISPLAY_NAME,
            plugin=FactoryPlugin(
                name=LIVE_VENUE_ID,
                display_name=LIVE_DISPLAY_NAME,
                factories={
                    Domain.TRADING: ZerodhaTradingAdapter,
                    Domain.MARKET_DATA: ZerodhaMarketDataFace,
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


def register_zerodha_live_instance(
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
    from broker.faces import StaticPlugin

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


__all__ = [
    "LIVE_VENUE_ID",
    "LIVE_DISPLAY_NAME",
    "ENABLE_ENV",
    "live_requirements",
    "login_url",
    "exchange_request_token",
    "register_zerodha_live",
    "register_zerodha_live_instance",
]
