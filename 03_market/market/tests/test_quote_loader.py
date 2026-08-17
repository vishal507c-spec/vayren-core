"""QuoteLoader tests — one real quote per listed symbol, published once."""

from pathlib import Path

from core.event_bus.event_bus import EventBus

from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.loader.quote_loader import QuoteLoader
from market.repository.symbol_repository import SymbolRepository


def test_publishes_quotes_loaded_for_listed_symbols(symbol_directory: Path) -> None:
    bus = EventBus()
    received: list[QuotesLoaded] = []
    bus.subscribe(QuotesLoaded, received.append)
    loader = QuoteLoader(SymbolRepository(symbol_directory), bus)

    loader.on_symbols_listed(SymbolsListed(symbols=("BPCL", "RELIANCE")))

    assert len(received) == 1
    assert [quote.symbol for quote in received[0].quotes] == ["BPCL", "RELIANCE"]
    assert received[0].quotes[0].price == 109.0  # BPCL 8 bars: latest close 109.0


def test_same_universe_is_a_noop(symbol_directory: Path) -> None:
    bus = EventBus()
    received: list[QuotesLoaded] = []
    bus.subscribe(QuotesLoaded, received.append)
    loader = QuoteLoader(SymbolRepository(symbol_directory), bus)

    first = SymbolsListed(symbols=("BPCL",))
    loader.on_symbols_listed(first)
    loader.on_symbols_listed(first)

    assert len(received) == 1


def test_new_universe_publishes_again(symbol_directory: Path) -> None:
    bus = EventBus()
    received: list[QuotesLoaded] = []
    bus.subscribe(QuotesLoaded, received.append)
    loader = QuoteLoader(SymbolRepository(symbol_directory), bus)

    loader.on_symbols_listed(SymbolsListed(symbols=("BPCL",)))
    loader.on_symbols_listed(SymbolsListed(symbols=("BPCL", "RELIANCE")))

    assert len(received) == 2
    assert [quote.symbol for quote in received[1].quotes] == ["BPCL", "RELIANCE"]


def test_empty_universe_publishes_empty_quotes(symbol_directory: Path) -> None:
    bus = EventBus()
    received: list[QuotesLoaded] = []
    bus.subscribe(QuotesLoaded, received.append)
    loader = QuoteLoader(SymbolRepository(symbol_directory), bus)

    loader.on_symbols_listed(SymbolsListed(symbols=()))

    assert received[0].quotes == ()
