"""QuotesLoaded — the watchlist quote snapshot result."""

from dataclasses import dataclass

from core.events.event import Event

from market.models.symbol_quote import SymbolQuote


@dataclass(frozen=True)
class QuotesLoaded(Event):
    """The latest real quote for each listed symbol, loaded once per universe."""

    quotes: tuple[SymbolQuote, ...]
