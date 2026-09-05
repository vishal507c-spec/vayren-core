"""Backtest component manifest."""

from core import (
    CapabilityDecl,
    CapabilityId,
    ComponentContract,
    ComponentId,
    ComponentManifest,
    ComponentVersion,
)


def backtest_manifest() -> ComponentManifest:
    """Return the manifest of the real backtest component."""
    return ComponentManifest(
        identity=ComponentId("backtest"),
        version=ComponentVersion.parse("1.0.0"),
        type="engine",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("backtest.run"),
                description="historical replay into strategy → fills → trades → metrics",
                inputs=("config: BacktestConfig", "strategy_ids: tuple[str, ...]"),
                outputs=("result: BacktestResult",),
            ),
            CapabilityDecl(
                id=CapabilityId("backtest.metrics"),
                description="compute 8 display metrics from trades and equity",
                inputs=("trades: tuple[TradeRecord, ...]", "curve: tuple[EquityPoint, ...]"),
                outputs=("metrics: PerformanceMetrics",),
            ),
        ),
        inputs=("config: BacktestConfig", "strategy_ids: tuple[str, ...]"),
        outputs=("result: BacktestResult",),
        dependencies=(ComponentId("core"), ComponentId("market"), ComponentId("strategy")),
        events_consumed=("RunBacktest",),
        events_produced=(
            "BacktestStarted",
            "BacktestProgress",
            "BacktestCompleted",
            "BacktestFailed",
        ),
        resource_requirements=("memory",),
        side_effects=("reads-sqlite-files", "publishes-events"),
        contract=ComponentContract(
            component=ComponentId("backtest"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=("deterministic for same inputs", "no fabricated trades"),
        ),
    )
