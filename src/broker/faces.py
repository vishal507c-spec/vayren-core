"""The three protocol faces + BrokerPlugin (design §4.3).

Re-exports the interface definitions from ``broker.interfaces.faces``.
"""

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

__all__ = [
    "BrokerPlugin",
    "FactoryPlugin",
    "HistoricalFace",
    "MarketDataFace",
    "PluginLike",
    "Sentinel",
    "StaticPlugin",
    "TradingFace",
]
