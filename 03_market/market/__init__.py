"""Market domain — candle payload plus the live Rust-bridge surface.

Aggregation, timeframe-ladder and single-candle-metric authority are
Rust-owned (``rust/vayren-core`` ``market`` + ``aggregate``) and reached only
through the bridges re-exported here: `native_aggregate`, `native_bar` and
`native_timeframe` (fail-closed FFI projections, no logic of their own).
``Bar`` stays as the zero-logic validated payload shape consumed by
Python-owned Strategy code.
"""

from market import native_aggregate, native_bar, native_timeframe
from market.models.bar import Bar

__all__ = ["Bar", "native_aggregate", "native_bar", "native_timeframe"]
