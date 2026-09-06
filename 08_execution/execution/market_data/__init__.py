"""Live market-data intake — provider boundary + stream normalization."""

from execution.market_data.normalizer import (
    NormalizedBatch,
    NormalizerConfig,
    StreamNormalizer,
    StreamStats,
)
from execution.market_data.provider import MarketDataError, MarketDataProvider, provider_supports
from execution.market_data.replay import ReplayProvider, bars_to_candles

__all__ = [
    "MarketDataProvider",
    "MarketDataError",
    "provider_supports",
    "ReplayProvider",
    "bars_to_candles",
    "StreamNormalizer",
    "NormalizerConfig",
    "NormalizedBatch",
    "StreamStats",
]
