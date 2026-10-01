"""Provider contract — the engine ↔ provider boundary.

The core download engine consumes ONLY this module. Concrete providers
(in ``broker.providers``) implement the :class:`Provider` protocol;
``data.provider.factory.build_provider`` resolves ``settings.provider`` to an instance.

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

from typing import Protocol, runtime_checkable

from broker.interfaces.historical import (
    CANONICAL_INTERVALS,
    ERR_AUTHENTICATION_FAILED,
    ERR_INVALID_REQUEST,
    ERR_INVALID_SYMBOL,
    ERR_NETWORK_ERROR,
    ERR_PROVIDER_UNAVAILABLE,
    ERR_UNKNOWN_PROVIDER_ERROR,
    RATE_LIMITED,
    TOKEN_EXPIRED,
    HistoricalFace,
    HistoricalProviderError,
)

ProviderError = HistoricalProviderError


@runtime_checkable
class Provider(HistoricalFace, Protocol):
    """The minimal surface the download engine needs from a provider."""


__all__ = [
    "CANONICAL_INTERVALS",
    "ERR_AUTHENTICATION_FAILED",
    "ERR_INVALID_REQUEST",
    "ERR_INVALID_SYMBOL",
    "ERR_NETWORK_ERROR",
    "ERR_PROVIDER_UNAVAILABLE",
    "ERR_UNKNOWN_PROVIDER_ERROR",
    "Provider",
    "ProviderError",
    "RATE_LIMITED",
    "TOKEN_EXPIRED",
]
