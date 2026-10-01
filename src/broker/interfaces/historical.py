"""Historical data face and provider contract vocabulary.

Broker-independent canonical vocabulary for historical market data access:
- Sentinels: TOKEN_EXPIRED, RATE_LIMITED
- Intervals: CANONICAL_INTERVALS
- Errors: ProviderError with normalized codes
- Interface: HistoricalFace protocol
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

TOKEN_EXPIRED = object()  # session/token invalid → renew and retry
RATE_LIMITED = object()  # consecutive rate-limit hits → emergency stop

# Normalized provider error codes — the only error vocabulary the engine sees.
ERR_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
ERR_INVALID_SYMBOL = "INVALID_SYMBOL"
ERR_NETWORK_ERROR = "NETWORK_ERROR"
ERR_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"

# Canonical interval ids — broker-agnostic, used by the engine end to end.
CANONICAL_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h")

Sentinel = Any


class ProviderError(RuntimeError):
    """A normalized provider failure with a broker-agnostic error code."""

    def __init__(self, message: str, code: str = ERR_UNKNOWN_PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.code = code


HistoricalProviderError = ProviderError


@runtime_checkable
class HistoricalFace(Protocol):
    """Canonical-vocabulary historical candle access (Provider contract)."""

    def available(self) -> tuple[bool, str]:
        """(ready, reason) — SDKs present and credentials configured?"""
        ...

    def symbols(self) -> set[str]:
        """Canonical symbols the provider can resolve (lazily fetched, cached)."""
        ...

    def fetch_candles(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict] | Sentinel:
        """Normalized candles for one canonical symbol/interval/range."""
        ...

    def new_session(self) -> None:
        """Start a fresh fetch session (rate-limit bookkeeping reset)."""
        ...

    def renew(self) -> None:
        """Discard the current session and re-authenticate."""
        ...


Provider = HistoricalFace

__all__ = [
    "CANONICAL_INTERVALS",
    "ERR_AUTHENTICATION_FAILED",
    "ERR_INVALID_REQUEST",
    "ERR_INVALID_SYMBOL",
    "ERR_NETWORK_ERROR",
    "ERR_PROVIDER_UNAVAILABLE",
    "ERR_UNKNOWN_PROVIDER_ERROR",
    "HistoricalFace",
    "HistoricalProviderError",
    "Provider",
    "ProviderError",
    "RATE_LIMITED",
    "Sentinel",
    "TOKEN_EXPIRED",
]
