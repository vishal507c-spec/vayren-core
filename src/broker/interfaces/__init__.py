"""Broker protocol faces and contract interfaces."""

from __future__ import annotations

from broker.interfaces.faces import (
    BrokerPlugin,
    FactoryPlugin,
    HistoricalFace,
    MarketDataFace,
    PluginLike,
    Sentinel,
    StaticPlugin,
    TradingFace,
)
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
    HistoricalProviderError,
    Provider,
    ProviderError,
)

__all__ = [
    "BrokerPlugin",
    "CANONICAL_INTERVALS",
    "ERR_AUTHENTICATION_FAILED",
    "ERR_INVALID_REQUEST",
    "ERR_INVALID_SYMBOL",
    "ERR_NETWORK_ERROR",
    "ERR_PROVIDER_UNAVAILABLE",
    "ERR_UNKNOWN_PROVIDER_ERROR",
    "FactoryPlugin",
    "HistoricalFace",
    "HistoricalProviderError",
    "MarketDataFace",
    "PluginLike",
    "Provider",
    "ProviderError",
    "RATE_LIMITED",
    "Sentinel",
    "StaticPlugin",
    "TOKEN_EXPIRED",
    "TradingFace",
]
