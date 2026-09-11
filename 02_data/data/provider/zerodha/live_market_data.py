"""Zerodha Kite market-data face — poll-based live quotes (explicit activation).

Feeds the execution :class:`BrokerFeedProvider` with normalized quote dicts
``{symbol, price, timestamp[, volume]}``. This is venue REST polling, NOT
tick streaming: one batched ``quote()`` call per poll (throttled), so the
honest feed kind is "live quotes, poll cadence" — the UI labels it LIVE
only when this face streams; otherwise the local SQLite tail applies.

Same activation as trading (see :mod:`live`): without credentials this
face refuses to connect with exact reasons.
"""

from __future__ import annotations

import os
import time
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.vocab import Environment

from data.provider.zerodha.live_trading import ACCESS_TOKEN_ENV, API_KEY_ENV, BrokerError

MARKET_DATA_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.MARKET_DATA: (Caps.MD_QUOTES, Caps.MD_HEARTBEAT)}
)


class ZerodhaMarketDataFace:
    """Poll-based quote face over Kite Connect (no websocket dependency)."""

    name = "zerodha"
    environment = Environment.LIVE

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
        kite: Any | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self._access_token = (
            access_token if access_token is not None else os.environ.get(ACCESS_TOKEN_ENV, "")
        )
        self._kite = kite
        self._poll_interval_s = max(0.5, float(poll_interval_s))
        self._connected = False
        self._subscribed: tuple[str, ...] = ()
        self._last_poll_epoch = 0.0
        self._cached: tuple[dict[str, Any], ...] = ()
        self._last_error = ""

    def capabilities(self) -> CapabilitySet:
        return MARKET_DATA_CAPABILITIES

    def connect(self) -> None:
        """Establish transport without subscribing (idempotent)."""
        if not self._api_key or not self._access_token:
            raise BrokerError(
                f"zerodha live credentials missing ({API_KEY_ENV} + {ACCESS_TOKEN_ENV} required)",
                code="CREDENTIALS",
            )
        kite = self._kite_client()
        try:
            kite.profile()
        except Exception as exc:
            raise BrokerError(f"market-data authentication failed: {exc}", code="AUTH") from None
        self._connected = True
        self._last_error = ""

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        self.connect()
        self.subscribe(symbols)

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols to the quote set (idempotent)."""
        self._subscribed = tuple(dict.fromkeys((*self._subscribed, *symbols)))
        self._connected = self._connected or False
        if not self._connected:
            self.connect()

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        dropped = set(symbols)
        self._subscribed = tuple(s for s in self._subscribed if s not in dropped)

    def poll(self) -> tuple[dict[str, Any], ...]:
        """One batched venue quote call → normalized quote dicts."""
        if not self._connected:
            return ()
        now = time.time()
        if now - self._last_poll_epoch < self._poll_interval_s and self._cached:
            return self._cached
        self._last_poll_epoch = now
        if not self._subscribed:
            self._cached = ()
            return ()
        try:
            raw = self._kite_client().quote([f"NSE:{s}" for s in self._subscribed])
        except Exception as exc:
            self._last_error = f"quote poll failed: {exc}"
            return ()
        self._last_error = ""
        out: list[dict[str, Any]] = []
        for symbol in self._subscribed:
            row = (raw or {}).get(f"NSE:{symbol}", {})
            if not isinstance(row, dict):
                continue
            try:
                price = float(row.get("last_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            try:
                volume = int(row.get("volume_traded", 0) or 0)
            except (TypeError, ValueError):
                volume = 0
            stamp = str(row.get("timestamp", "") or "")[:19]
            out.append({"symbol": symbol, "price": price, "timestamp": stamp, "volume": volume})
        self._cached = tuple(out)
        return self._cached

    def health(self) -> tuple[bool, str]:
        if not self._connected:
            return False, self._last_error or "not connected"
        if self._last_error:
            return False, self._last_error
        if not self._subscribed:
            return False, "no symbols subscribed"
        return True, "zerodha quotes streaming"

    def disconnect(self) -> None:
        """Drop transport, keeping subscription memory for reconnect."""
        self._connected = False

    def reconnect(self) -> None:
        """Re-establish transport, resuming the subscription set."""
        self._last_error = ""
        self.connect()

    def close(self) -> None:
        self._connected = False
        self._subscribed = ()
        self._cached = ()

    # ── internals ───────────────────────────────────────────────

    def _kite_client(self) -> Any:
        if self._kite is not None:
            return self._kite
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise BrokerError(
                "kiteconnect SDK not installed (pip install kiteconnect>=5)",
                code="NOT_CONFIGURED",
            ) from exc
        kite = KiteConnect(api_key=self._api_key)
        try:
            kite.set_access_token(self._access_token)
        except Exception:
            raise BrokerError("failed to apply access token", code="CREDENTIALS") from None
        self._kite = kite
        return kite


__all__ = ["ZerodhaMarketDataFace", "MARKET_DATA_CAPABILITIES"]
