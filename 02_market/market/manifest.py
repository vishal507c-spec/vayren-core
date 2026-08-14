"""Market component manifest — the real market component, described factually.

The manifest describes what already exists: SQLite candle storage and loading.
It never changes runtime behavior; the ComponentRegistry and SystemModel read
it at startup for discovery and AI-readable architecture context.
"""

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
)
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.contract import ComponentContract
from core.contracts.manifest import ComponentManifest


def market_manifest() -> ComponentManifest:
    """Return the manifest of the real market component."""
    return ComponentManifest(
        identity=ComponentId("market"),
        version=ComponentVersion.parse("1.0.0"),
        type="storage",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("data.query.candles"),
                description="fetch OHLCV candles for a symbol",
                inputs=("symbol: str", "limit: int | None"),
                outputs=("bars: tuple[Bar, ...]",),
                contract=CapabilityContract(
                    capability=CapabilityId("data.query.candles"),
                    inputs=("symbol: str", "limit: int | None"),
                    outputs=("bars: tuple[Bar, ...]",),
                    rules=BehavioralRules(
                        guarantees=("ascending timestamps", "no fabricated bars"),
                        failure_modes=("missing database file skipped",),
                    ),
                ),
            ),
            CapabilityDecl(
                id=CapabilityId("data.query.timeframes"),
                description="detect available timeframes from real rows",
                inputs=("symbol: str",),
                outputs=("timeframes: tuple[str, ...]",),
            ),
            CapabilityDecl(
                id=CapabilityId("data.query.quotes"),
                description="latest candle per symbol as a quote",
                inputs=("symbols: tuple[str, ...]",),
                outputs=("quotes: tuple[SymbolQuote, ...]",),
            ),
            CapabilityDecl(
                id=CapabilityId("data.transform.aggregate"),
                description="aggregate bars into a requested timeframe",
                inputs=("symbol: str", "timeframe: str", "limit: int | None"),
                outputs=("bars: tuple[Bar, ...]",),
            ),
        ),
        inputs=("symbol: str",),
        outputs=("bars: tuple[Bar, ...]", "quotes: tuple[SymbolQuote, ...]"),
        dependencies=(ComponentId("core"),),
        events_consumed=("LoadSymbol", "TimeframeChanged", "ListSymbols", "ListTimeframes"),
        events_produced=("SymbolsListed", "QuotesLoaded", "DataLoaded", "TimeframesListed"),
        resource_requirements=("sqlite", "file-system"),
        side_effects=("reads-sqlite-files", "publishes-events"),
        contract=ComponentContract(
            component=ComponentId("market"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=("no duplicate candle timestamps", "bars ascending by timestamp"),
        ),
    )
