"""SymbolQuote — latest real market state for one symbol, read-only."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolQuote:
    """Latest close state for a symbol, derived from its last real candle.

    `price` is the latest close, `change_pct` is the intraday change of the
    latest candle (same (close − open) / open semantics the chart header
    uses), and `timestamp` is the time of that candle. Never fabricated:
    a quote exists only when the symbol's database has a candle.
    """

    symbol: str
    price: float
    change_pct: float
    timestamp: str
