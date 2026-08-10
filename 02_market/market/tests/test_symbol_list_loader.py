"""SymbolListLoader tests."""

from pathlib import Path

from core.event_bus.event_bus import EventBus

from market.events.list_symbols import ListSymbols
from market.events.symbols_listed import SymbolsListed
from market.loader.symbol_list_loader import SymbolListLoader
from market.repository.symbol_repository import SymbolRepository


def test_on_list_symbols_publishes_symbols_listed(symbol_directory: Path) -> None:
    bus = EventBus()
    loader = SymbolListLoader(SymbolRepository(symbol_directory), bus)
    bus.subscribe(ListSymbols, loader.on_list_symbols)
    received: list[SymbolsListed] = []
    bus.subscribe(SymbolsListed, received.append)
    bus.publish(ListSymbols())
    assert len(received) == 1
    assert received[0].symbols == ("AMBUJACEM", "BPCL", "RELIANCE")


def test_missing_directory_publishes_nothing(tmp_path: Path) -> None:
    bus = EventBus()
    loader = SymbolListLoader(SymbolRepository(tmp_path / "nowhere"), bus)
    bus.subscribe(ListSymbols, loader.on_list_symbols)
    received: list[SymbolsListed] = []
    bus.subscribe(SymbolsListed, received.append)
    bus.publish(ListSymbols())
    assert len(received) == 1
    assert received[0].symbols == ()
