"""Behavioral migration-unit registry (seed definitions).

One file is NOT one migration unit: units are behaviors (functions, state
machines, kernels, execution paths) with stable IDs, explicit dependencies
and a named Rust target. The scanner cross-checks these against the real
repository; the planner orders them by dependency.
"""

from __future__ import annotations

from .config import LIVE_CRITICAL_PREFIXES
from .models import UnitDefinition


def _live(unit_id: str) -> bool:
    return unit_id.startswith(LIVE_CRITICAL_PREFIXES)


def seed_units() -> list[UnitDefinition]:
    units = [
        UnitDefinition(
            unit_id="execution.order_lifecycle",
            description="Order lifecycle transition table and terminal set.",
            python_source="08_execution/execution/models/order.py",
            rust_target="rust/vayren-core/src/order_state.rs",
            dependencies=(),
            public_api=("TRANSITIONS", "TERMINAL_STATES", "transition_allowed"),
            side_effects="none (pure table lookup)",
            numerical=False,
            execution_path="execution.engine -> native_order_state -> cdylib",
        ),
        UnitDefinition(
            unit_id="backtest.metrics.drawdown",
            description="Peak-to-trough drawdown (pct, abs) over an equity curve.",
            python_source="06_backtest/backtest/engine/metrics.py",
            rust_target="rust/vayren-core/src/metrics.rs",
            dependencies=(),
            public_api=("max_drawdown",),
            side_effects="none",
            numerical=True,
            execution_path="backtest.runner -> native_metrics -> cdylib",
        ),
        UnitDefinition(
            unit_id="backtest.metrics.equity_curve",
            description="Running equity + per-point drawdown from trade PnLs.",
            python_source="06_backtest/backtest/engine/metrics.py",
            rust_target="rust/vayren-core/src/metrics.rs",
            dependencies=(),
            public_api=("equity_curve_points",),
            side_effects="none",
            numerical=True,
            execution_path="backtest.runner -> native_metrics -> cdylib",
        ),
        UnitDefinition(
            unit_id="backtest.metrics.sharpe",
            description="Annualized Sharpe of per-trade equity returns.",
            python_source="06_backtest/backtest/engine/metrics.py",
            rust_target="rust/vayren-core/src/metrics.rs",
            dependencies=("backtest.metrics.equity_curve",),
            public_api=("sharpe",),
            side_effects="none",
            numerical=True,
            execution_path="backtest.runner -> native_metrics -> cdylib",
        ),
        UnitDefinition(
            unit_id="market.timeframe.aggregate",
            description="Session-anchored OHLCV bucket aggregation kernel.",
            python_source="03_market/market/timeframe/aggregate.py",
            rust_target="rust/vayren-core/src/aggregate.rs",
            dependencies=("market.timeframe.mode",),
            public_api=("aggregate_bars",),
            side_effects="none",
            numerical=True,
            execution_path="market.loader -> aggregate -> native_aggregate -> cdylib",
        ),
        UnitDefinition(
            unit_id="market.timeframe.mode",
            description="Most-frequent value with first-seen tie-break.",
            python_source="03_market/market/timeframe/aggregate.py",
            rust_target="rust/vayren-core/src/stats.rs",
            dependencies=(),
            public_api=("detect_bar_duration", "detect_session_start", "mode"),
            side_effects="none",
            numerical=True,
            execution_path="market.loader -> native_aggregate.mode -> cdylib",
        ),
        UnitDefinition(
            unit_id="risk.engine.evaluate",
            description="Fail-closed pre-order policy evaluation with audit trail.",
            python_source="07_risk/risk/engine.py",
            rust_target="rust/vayren-core/src/risk.rs",
            dependencies=(),
            public_api=("RiskEngine.evaluate",),
            side_effects="seen-intent memory; kill-switch file",
            numerical=False,
            execution_path="execution.session -> risk.engine (Python-authoritative)",
        ),
        UnitDefinition(
            unit_id="execution.session.lifecycle",
            description="Live/paper session startup order and order state machine.",
            python_source="08_execution/execution/runtime/session.py",
            rust_target="rust/vayren-core/src/session.rs",
            dependencies=("execution.order_lifecycle", "risk.engine.evaluate"),
            public_api=("LiveSession",),
            side_effects="broker fills; journal; checkpoints",
            numerical=False,
            execution_path="execution.session (Python-authoritative)",
        ),
        UnitDefinition(
            unit_id="market.storage.sqlite",
            description="Per-symbol SQLite candle storage and schema.",
            python_source="03_market/market/database/sqlite.py",
            rust_target="rust/vayren-core/src/market_storage.rs",
            dependencies=(),
            public_api=("SqliteCandleDatabase", "fetch_candles"),
            side_effects="SQLite files under data dir",
            numerical=False,
            execution_path="market.repository -> database (Python IO glue)",
        ),
        UnitDefinition(
            unit_id="data.download.orchestration",
            description="Historical download worker/threading orchestration.",
            python_source="02_data/data/downloader/engine.py",
            rust_target="",
            dependencies=(),
            public_api=("HistoricalDownloadEngine",),
            side_effects="network via provider boundary; SQLite writes",
            numerical=False,
            execution_path="data.worker -> engine (Python IO glue)",
        ),
        UnitDefinition(
            unit_id="chart.viewport.math",
            description="Chart viewport/window projection math (future Rust slice).",
            python_source="04_chart/chart/models/chart_viewport.py",
            rust_target="",
            dependencies=("market.timeframe.aggregate",),
            public_api=("ChartViewport",),
            side_effects="none",
            numerical=True,
            execution_path="chart.widgets (Python presentation)",
        ),
        UnitDefinition(
            unit_id="core.event_bus.dispatch",
            description="Synchronous exact-type event dispatch foundation.",
            python_source="01_core/core/event_bus/event_bus.py",
            rust_target="",
            dependencies=(),
            public_api=("EventBus.publish", "EventBus.subscribe"),
            side_effects="handler side effects by design",
            numerical=False,
            execution_path="every module via bootstrap (Python foundation glue)",
        ),
    ]
    flagged: list[UnitDefinition] = []
    for unit in units:
        if _live(unit.unit_id) and not unit.live_critical:
            flagged.append(
                UnitDefinition(
                    unit_id=unit.unit_id,
                    description=unit.description,
                    python_source=unit.python_source,
                    rust_target=unit.rust_target,
                    dependencies=unit.dependencies,
                    public_api=unit.public_api,
                    side_effects=unit.side_effects,
                    numerical=unit.numerical,
                    execution_path=unit.execution_path,
                    live_critical=True,
                    python_glue=unit.python_glue,
                )
            )
        else:
            flagged.append(unit)
    glue_ids = {"data.download.orchestration", "core.event_bus.dispatch", "market.storage.sqlite"}
    out: list[UnitDefinition] = []
    for unit in flagged:
        if unit.unit_id in glue_ids and not unit.python_glue:
            out.append(
                UnitDefinition(
                    unit_id=unit.unit_id,
                    description=unit.description,
                    python_source=unit.python_source,
                    rust_target=unit.rust_target,
                    dependencies=unit.dependencies,
                    public_api=unit.public_api,
                    side_effects=unit.side_effects,
                    numerical=unit.numerical,
                    execution_path=unit.execution_path,
                    live_critical=unit.live_critical,
                    python_glue=True,
                )
            )
        else:
            out.append(unit)
    return out


def unit_by_id(unit_id: str) -> UnitDefinition | None:
    for unit in seed_units():
        if unit.unit_id == unit_id:
            return unit
    return None


__all__ = ["seed_units", "unit_by_id"]
