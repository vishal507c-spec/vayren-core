"""Market domain — candle payload plus the live Rust-bridge surface.

Aggregation, timeframe-ladder and single-candle-metric authority are
Rust-owned (``vayren-core`` ``market`` + ``aggregate``) and reached only
through the bridges re-exported here: `native_aggregate`, `native_bar` and
`native_timeframe` (fail-closed FFI projections, no logic of their own).
``Bar`` stays as the zero-logic validated payload shape consumed by
Python-owned Strategy code.

The flat names below (``Bucket``, ``bucket_start``, ``fold_tick``,
``session_anchor_seconds``, ``closed_count``, ``timeframe_seconds``) are
re-exports of those same bridge functions, so a caller can import the
kernel vocabulary from one place without any second authority appearing.
"""

from market import native_aggregate, native_bar, native_timeframe
from market.models.bar import Bar
from market.repository.symbol_repository import SymbolRepository

#: Kernel aggregation vocabulary (Rust-owned, re-exported unchanged).
Bucket = native_aggregate.Bucket
bucket_start = native_aggregate.bucket_start
fold_tick = native_aggregate.fold_tick
session_anchor_seconds = native_aggregate.session_anchor_seconds
closed_count = native_aggregate.closed_count

#: Timeframe label → seconds. The kernel's own resolver, re-exported under
#: the name the restored execution engine imports.
timeframe_seconds = native_timeframe.seconds_of

__all__ = [
    "Bar",
    "Bucket",
    "SymbolRepository",
    "bucket_start",
    "closed_count",
    "fold_tick",
    "session_anchor_seconds",
    "timeframe_seconds",
    "native_aggregate",
    "native_bar",
    "native_timeframe",
]
