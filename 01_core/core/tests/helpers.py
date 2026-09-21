"""Shared native fixtures for core tests.

Native-era demo system: ``market`` + ``historical_data`` (the legacy chart
component is gone - see the native migration). Same shape as the retired
``test_component_poc.build_system``: production manifests with class
references, used to exercise SystemModel/graph/AI logic.
"""

from data.manifest import data_manifest
from market.manifest import market_manifest
from market.repository.candle_repository import CandleRepository
from market.repository.symbol_repository import SymbolRepository

from core.registry.component_registry import ComponentRegistry


class _RustOwnedFlow:
    """Structural marker only — zero logic, zero responsibility.

    The historical-download flow mechanics are implemented by the Rust
    owner (``rust/vayren-core`` ``download`` + ``download_engine``).
    These markers let registry/graph tests build the demo system; they
    implement nothing and decide nothing.
    """


def build_system() -> ComponentRegistry:
    """Component registry with the real market + historical-data manifests."""
    registry = ComponentRegistry()
    registry.register(
        market_manifest(),
        implementations={
            "data.query.candles": CandleRepository,
            "data.query.timeframes": CandleRepository,
            "data.query.quotes": SymbolRepository,
            "data.transform.aggregate": CandleRepository,
        },
    )
    registry.register(
        data_manifest(),
        implementations={
            "historical_data.download": _RustOwnedFlow,
            "historical_data.coverage": _RustOwnedFlow,
            "historical_data.status": _RustOwnedFlow,
        },
    )
    return registry
