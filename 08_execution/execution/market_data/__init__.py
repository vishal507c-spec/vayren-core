"""Live market-data intake — provider boundary + stream normalization."""

from execution.market_data.broker_feed import BrokerFeedProvider
from execution.market_data.normalizer import (
    NormalizedBatch,
    NormalizerConfig,
    StreamNormalizer,
    StreamStats,
)
from execution.market_data.provider import MarketDataError, MarketDataProvider
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.market_data.sqlite_tail import SqliteTailProvider

__all__ = [
    "MarketDataProvider",
    "MarketDataError",
    "ReplayProvider",
    "bars_to_candles",
    "BrokerFeedProvider",
    "SqliteTailProvider",
    "StreamNormalizer",
    "NormalizerConfig",
    "NormalizedBatch",
    "StreamStats",
]
