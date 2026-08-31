"""Provider contract — the engine ↔ provider boundary.

The core download engine consumes ONLY this module. Concrete providers
(``data.provider.zerodha.adapter.ZerodhaProvider`` and future adapters)
implement the :class:`Provider` protocol; ``data.provider.factory.build_provider``
resolves ``settings.provider`` to an instance.

The engine's vocabulary is canonical and broker-free:

- **Symbols** are plain tickers (``SBIN``, ``RELIANCE``, ``TCS``). Broker
  instrument/token resolution is the provider's job — tokens never reach the
  engine.
- **Intervals** are canonical ids (:data:`CANONICAL_INTERVALS`). Each adapter
  maps them to its own API interval ids.
- **Errors** are normalized :class:`ProviderError` codes
  (``AUTHENTICATION_FAILED``, ``RATE_LIMITED``, ``INVALID_SYMBOL``,
  ``NETWORK_ERROR``, ``PROVIDER_UNAVAILABLE``, ``INVALID_REQUEST``,
  ``UNKNOWN_PROVIDER_ERROR``).
- **Candles** are ``dict`` rows with keys ``date`` (datetime), ``open``,
  ``high``, ``low``, ``close``, ``volume`` — the CandleDB upsert contract.

The sentinels are opaque control-flow tokens: a fetch result that ``is`` one
of them stops the current sweep and drives the engine's renewal/retry path —
their meaning is provider-owned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

TOKEN_EXPIRED = object()  # session/token invalid → renew and retry
RATE_LIMITED = object()  # consecutive rate-limit hits → emergency stop

# Normalized provider error codes — the only error vocabulary the engine sees.
ERR_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_INVALID_SYMBOL = "INVALID_SYMBOL"
ERR_NETWORK_ERROR = "NETWORK_ERROR"
ERR_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"

# Canonical interval ids — broker-agnostic, used by the engine end to end.
CANONICAL_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h")


class ProviderError(RuntimeError):
    """A normalized provider failure with a broker-agnostic error code."""

    def __init__(self, message: str, code: str = ERR_UNKNOWN_PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.code = code


class Provider(Protocol):
    """The minimal surface the download engine needs from a provider.

    Implementations translate broker SDKs, credentials, intervals, tokens,
    exceptions and rate limits into this canonical vocabulary.
    """

    def available(self) -> tuple[bool, str]:
        """(ready, reason) — SDKs present and credentials configured?"""
        ...

    def symbols(self) -> set[str]:
        """Canonical symbols the provider can resolve (lazily fetched, cached)."""
        ...

    def fetch_candles(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict] | object:
        """Normalized candles for one canonical symbol/interval/range.

        Returns a list of candle dicts (date/open/high/low/close/volume) or
        the :data:`TOKEN_EXPIRED` / :data:`RATE_LIMITED` sentinel. Raises
        :class:`ProviderError` with a normalized code for hard failures.
        """
        ...

    def new_session(self) -> None:
        """Start a fresh fetch session (rate-limit bookkeeping reset).

        Cheap and local: no re-authentication. The engine calls it at the
        same points the original engine created a fresh fetch client.
        """
        ...

    def renew(self) -> None:
        """Discard the current session and re-authenticate."""
        ...
