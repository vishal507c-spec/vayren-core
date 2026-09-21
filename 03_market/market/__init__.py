"""Market domain — Python retains the candle payload shape only.

Storage, repository, loading, aggregation and timeframe authority are
Rust-owned (``rust/vayren-core`` ``market`` + ``aggregate``); their Python
twins were removed. ``Bar`` stays as the zero-logic payload shape consumed
by Python-owned Strategy code.
"""

from market.models.bar import Bar

__all__ = ["Bar"]
