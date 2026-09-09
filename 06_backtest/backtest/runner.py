"""BacktestRunner — orchestrates replay → Python strategy → execution → journal → metrics.

Canonical path: Python Strategy (strategy.strategies.base.PythonStrategy) → Signal.

No DSL, no IR, no VM. Python is the ONLY strategy execution path.
Registry is kept only for backward compat (legacy definitions) but not used for
Python strategies. New code should use Python path (data_dir + StrategyRecord).
"""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Any

from strategy import BarView, SignalKind, StrategyState

from backtest.engine.journal import TradeJournal
from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.engine.positions import PositionManager
from backtest.engine.replay import slice_bars
from backtest.engine.simulator import ExecutionSimulator
from backtest.models.config import BacktestConfig
from backtest.models.result import BacktestResult, StrategyResult

logger = getLogger(__name__)


class BacktestRunner:
    """End-to-end backtest for one request (multiple strategies isolated).

    No thread, no bus. The :class:`BacktestWorker` calls :meth:`run` off the
    UI thread and bridges results via Qt signals. Market data is reused
    through the given :class:`SymbolRepository`.

    Execution is Python-only: Python Strategy source → PythonStrategy. No DSL, no IR, no VM.
    """

    def __init__(
        self,
        repository: Any,
        registry: Any | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        # data_dir for Python strategy lookup; if not given, try to derive from repository
        if data_dir is None:
            try:
                d = getattr(repository, "_directory", None)
                data_dir = str(d) if d is not None else None
            except Exception:
                data_dir = None
        self._data_dir = data_dir

    def run(
        self,
        config: BacktestConfig,
        strategy_ids: tuple[str, ...],
        on_progress: Any | None = None,
    ) -> BacktestResult:
        """Execute the backtest and return per-strategy results."""
        results: list[StrategyResult] = []
        errors: list[str] = []

        for strategy_id in strategy_ids:
            result = self._run_one_by_id(strategy_id, config, on_progress)
            if result is not None:
                results.append(result)
            else:
                # Determine if it was unknown strategy vs no bars
                # Try to give useful error
                if self._is_unknown_strategy(strategy_id):
                    errors.append(f"unknown strategy {strategy_id}")
                else:
                    errors.append(
                        f"{strategy_id}: no bars in requested range or compilation failed"
                    )

        if errors and not results:
            return BacktestResult(
                results=(),
                has_error=True,
                error="no_results",
                error_detail="; ".join(errors),
            )
        return BacktestResult(
            results=tuple(results),
            has_error=bool(errors),
            error_detail="; ".join(errors) if errors else None,
        )

    def _is_unknown_strategy(self, strategy_id: str) -> bool:
        # Check storage first
        try:
            from strategy.language.storage import get_strategy_by_id, load_strategy_record

            if get_strategy_by_id(strategy_id, self._data_dir) is not None:
                return False
            if load_strategy_record(strategy_id, self._data_dir) is not None:
                return False
        except Exception:
            pass
        # Check registry if present
        if self._registry is not None:
            try:
                if self._registry.contains(strategy_id):
                    return False
            except Exception:
                pass
        return True

    def _run_one_by_id(
        self, strategy_id: str, config: BacktestConfig, on_progress: Any | None
    ) -> StrategyResult | None:
        # Try Python path first: load Python strategy by id or name
        try:
            from strategy.language.storage import get_strategy_by_id, load_strategy_record

            rec = get_strategy_by_id(strategy_id, self._data_dir)
            if rec is None:
                rec = load_strategy_record(strategy_id, self._data_dir)
            if rec is not None:
                return self._run_one_vm(rec, config, on_progress)
        except Exception as exc:  # noqa: BLE001
            logger.warning("VM load failed for %s: %s", strategy_id, exc)
            return None

        # Fallback: legacy registry path (for backward compat, will be removed)
        if self._registry is not None:
            try:
                definition = self._registry.get(strategy_id)
                if not definition.enabled:
                    return None
                return self._run_one_legacy(definition, config, on_progress)
            except Exception:
                pass
        return None

    def _run_one_vm(
        self, rec: Any, config: BacktestConfig, on_progress: Any | None
    ) -> StrategyResult | None:  # type: ignore[no-untyped-def]
        try:
            bars = self._repository.get_candles_timeframe(config.symbol, config.timeframe, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Market fetch failed for %s %s: %s", config.symbol, config.timeframe, exc
            )
            return None
        window = slice_bars(bars, config.start_date, config.end_date)
        if not window:
            return None

        # Compile Python Strategy (only path, fail loudly)
        try:
            from strategy import StrategyParameters
            from strategy.language import compile_strategy

            compiled = compile_strategy(rec.code)
            # Python-native — owner-aware for chart lifecycle
            logic = compiled.create_logic(
                StrategyParameters(compiled.param_defaults), owner_id=rec.name
            )
            warmup = max(0, logic.warmup())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strategy compilation failed for %s: %s", rec.name, exc)
            # Fail loudly — no fallback
            raise

        journal = TradeJournal()
        positions = PositionManager()
        simulator = ExecutionSimulator(
            slippage_pct=config.slippage_pct, commission_pct=config.commission_pct
        )

        for index in range(len(window)):
            bar = window[index]
            if on_progress is not None and index % 500 == 0:
                try:  # noqa: SIM105
                    on_progress(index, len(window))
                except Exception:  # noqa: BLE001, SIM105
                    pass

            # Warmup: run VM for chart/indicator history only, skip trading
            if index < warmup:
                from strategy import StrategyParameters as SP  # noqa: N817

                view_warm = BarView(
                    bars=window,
                    index=index,
                    params=SP(compiled.param_defaults),
                    state=StrategyState(),
                )
                logic.on_bar(view_warm)
                continue

            if not positions.flat:
                trade = positions.try_close(
                    exit_index=index,
                    exit_time=bar.timestamp,
                    bar_open=bar.open,
                    bar_high=bar.high,
                    bar_low=bar.low,
                    bar_close=bar.close,
                    commission_pct=config.commission_pct,
                )
                if trade is not None:
                    journal.record(trade)
                    continue

            state = StrategyState()
            if positions.open_position is not None:
                op = positions.open_position
                state = StrategyState.open(
                    side=op.side, entry_price=op.entry_price, entry_index=op.entry_index
                )
            from strategy.models.parameters import StrategyParameters as SP  # noqa: N817

            view = BarView(
                bars=window, index=index, params=SP(compiled.param_defaults), state=state
            )

            signal = logic.on_bar(view)
            if signal is None:
                continue

            if positions.flat and signal.kind == SignalKind.BUY:
                equity = config.initial_capital + sum(t.pnl for t in journal.trades)
                fill = simulator.fill("LONG", bar.close, equity)
                if fill is None:
                    continue
                positions.open_long(
                    symbol=config.symbol,
                    entry_index=index,
                    entry_time=bar.timestamp,
                    fill_price=fill.fill_price,
                    quantity=fill.quantity,
                    commission=fill.commission,
                    sl_price=signal.stop_loss,
                    tp_price=signal.take_profit,
                )
            elif positions.flat and signal.kind == SignalKind.SELL:
                equity = config.initial_capital + sum(t.pnl for t in journal.trades)
                fill = simulator.fill("SHORT", bar.close, equity)
                if fill is None:
                    continue
                positions.open_short(
                    symbol=config.symbol,
                    entry_index=index,
                    entry_time=bar.timestamp,
                    fill_price=fill.fill_price,
                    quantity=fill.quantity,
                    commission=fill.commission,
                    sl_price=signal.stop_loss,
                    tp_price=signal.take_profit,
                )
            elif not positions.flat:
                is_long = (
                    positions.open_position is not None and positions.open_position.side == "LONG"
                )
                should_close = (is_long and signal.kind == SignalKind.SELL) or (
                    not is_long and signal.kind == SignalKind.BUY
                )
                if should_close:
                    fill_price = bar.close - bar.close * (config.slippage_pct / 100.0)
                    if not is_long:
                        fill_price = bar.close + bar.close * (config.slippage_pct / 100.0)
                    trade = positions.close_signal(
                        exit_index=index,
                        exit_time=bar.timestamp,
                        exit_price=fill_price,
                        commission_pct=config.commission_pct,
                    )
                    if trade is not None:
                        journal.record(trade)

        if not positions.flat and window:
            last = window[-1]
            last_price = last.close - last.close * (config.slippage_pct / 100.0)
            trade = positions.close_end(
                exit_index=len(window) - 1,
                exit_time=last.timestamp,
                exit_price=last_price,
                commission_pct=config.commission_pct,
            )
            if trade is not None:
                journal.record(trade)

        trades = journal.trades
        curve = compute_equity_curve(
            trades, config.initial_capital, window[0].timestamp if window else None
        )
        metrics = compute_metrics(trades, curve, config.initial_capital)
        # Collect chart series from VM — owner-aware (strategy instance, title)
        chart_series: tuple[Any, ...] = ()
        try:
            from backtest.models.result import ChartSeries

            raw_series: dict[Any, Any] = {}
            raw_meta: dict[Any, Any] = {}
            if hasattr(logic, "get_chart_series_with_owner"):
                raw_series = logic.get_chart_series_with_owner()  # type: ignore[attr-defined]
                try:
                    raw_meta = logic.get_chart_series_meta_with_owner()  # type: ignore[attr-defined]
                except Exception:
                    raw_meta = {}
            elif hasattr(logic, "get_chart_series"):
                raw_series = logic.get_chart_series()  # type: ignore[attr-defined]
                try:
                    raw_meta = logic.get_chart_series_meta()  # type: ignore[attr-defined]
                except Exception:
                    raw_meta = {}
            series_list: list[ChartSeries] = []
            for key, values in raw_series.items():
                if not isinstance(values, dict):
                    continue
                # key may be (owner_id, title) or title
                if isinstance(key, tuple) and len(key) == 2:
                    owner_id, title = str(key[0]), str(key[1])
                else:
                    owner_id, title = str(getattr(rec, "id", "")), str(key)
                pts = tuple((int(k), float(v)) for k, v in sorted(values.items()) if v is not None)
                if not pts:
                    continue
                meta = raw_meta.get(key, {}) if isinstance(key, tuple) else raw_meta.get(title, {})
                if not isinstance(meta, dict):
                    meta = {}
                series_list.append(
                    ChartSeries(
                        title=title,
                        values=pts,
                        style=str(meta.get("style", "line")),
                        extend=str(meta.get("extend", "session")),
                        strategy=str(owner_id or getattr(rec, "name", "")),
                    )
                )
            chart_series = tuple(series_list)
        except Exception:
            chart_series = ()
        # Universal strategy-owned plot events — same contract live/backtest/
        # replay. Duck-typed (no strategy import): PlotEvent objects or dicts.
        chart_plots: tuple[Any, ...] = ()
        try:
            getter = getattr(logic, "get_plot_events", None)
            if callable(getter):
                plots = getter()
                if isinstance(plots, (tuple, list)):
                    chart_plots = tuple(plots)
        except Exception:
            chart_plots = ()
        muted_bars: tuple[int, ...] = ()
        try:
            mute_getter = getattr(logic, "get_muted_signal_bars", None)
            if callable(mute_getter):
                muted = mute_getter()
                if isinstance(muted, (tuple, list)):
                    muted_bars = tuple(
                        int(bar)
                        for bar in muted
                        if isinstance(bar, int) and not isinstance(bar, bool) and bar >= 0
                    )
        except Exception:
            muted_bars = ()
        # Use rec.id as canonical strategy_id, rec.name as display
        # For label, mimic StrategyDefinition.label: "NAME v1.0"
        label = f"{rec.name} v{rec.version}" if hasattr(rec, "version") else rec.name
        return StrategyResult(
            strategy_id=rec.id,
            name=label,
            config=config,
            trades=trades,
            equity_curve=curve,
            metrics=metrics,
            bars_used=len(window),
            period_start=window[0].timestamp if window else None,
            period_end=window[-1].timestamp if window else None,
            chart_series=chart_series,
            chart_plots=chart_plots,
            muted_bars=muted_bars,
        )

    def _run_one_legacy(
        self, definition: Any, config: BacktestConfig, on_progress: Any | None
    ) -> StrategyResult | None:  # type: ignore[no-untyped-def]
        # Legacy registry factory path — kept only for backward compat, will be removed after migration  # noqa: E501
        try:
            bars = self._repository.get_candles_timeframe(config.symbol, config.timeframe, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Market fetch failed for %s %s: %s", config.symbol, config.timeframe, exc
            )
            return None
        window = slice_bars(bars, config.start_date, config.end_date)
        if not window:
            return None

        # This path uses StrategyRegistry factories (builtin Python strategies)
        # It is deprecated and will be removed; VM path should be used
        try:
            factory = self._registry.factory(definition.kind)  # type: ignore[union-attr]
            logic = factory(definition.params)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Legacy factory failed for %s: %s", definition.id, exc)
            raise

        warmup = max(0, logic.warmup())

        journal = TradeJournal()
        positions = PositionManager()
        simulator = ExecutionSimulator(
            slippage_pct=config.slippage_pct, commission_pct=config.commission_pct
        )

        for index in range(warmup, len(window)):
            bar = window[index]
            if on_progress is not None and index % 500 == 0:
                try:  # noqa: SIM105
                    on_progress(index, len(window))
                except Exception:  # noqa: BLE001, SIM105
                    pass

            if not positions.flat:
                trade = positions.try_close(
                    exit_index=index,
                    exit_time=bar.timestamp,
                    bar_open=bar.open,
                    bar_high=bar.high,
                    bar_low=bar.low,
                    bar_close=bar.close,
                    commission_pct=config.commission_pct,
                )
                if trade is not None:
                    journal.record(trade)
                    continue

            state = StrategyState()
            if positions.open_position is not None:
                op = positions.open_position
                state = StrategyState.open(
                    side=op.side, entry_price=op.entry_price, entry_index=op.entry_index
                )
            view = BarView(bars=window, index=index, params=definition.params, state=state)  # type: ignore[attr-defined]

            signal = logic.on_bar(view)
            if signal is None:
                continue

            if positions.flat and signal.kind == SignalKind.BUY:
                equity = config.initial_capital + sum(t.pnl for t in journal.trades)
                fill = simulator.fill("LONG", bar.close, equity)
                if fill is None:
                    continue
                positions.open_long(
                    symbol=config.symbol,
                    entry_index=index,
                    entry_time=bar.timestamp,
                    fill_price=fill.fill_price,
                    quantity=fill.quantity,
                    commission=fill.commission,
                    sl_price=signal.stop_loss,
                    tp_price=signal.take_profit,
                )
            elif positions.flat and signal.kind == SignalKind.SELL:
                equity = config.initial_capital + sum(t.pnl for t in journal.trades)
                fill = simulator.fill("SHORT", bar.close, equity)
                if fill is None:
                    continue
                positions.open_short(
                    symbol=config.symbol,
                    entry_index=index,
                    entry_time=bar.timestamp,
                    fill_price=fill.fill_price,
                    quantity=fill.quantity,
                    commission=fill.commission,
                    sl_price=signal.stop_loss,
                    tp_price=signal.take_profit,
                )
            elif not positions.flat:
                is_long = (
                    positions.open_position is not None and positions.open_position.side == "LONG"
                )
                should_close = (is_long and signal.kind == SignalKind.SELL) or (
                    not is_long and signal.kind == SignalKind.BUY
                )
                if should_close:
                    fill_price = bar.close - bar.close * (config.slippage_pct / 100.0)
                    if not is_long:
                        fill_price = bar.close + bar.close * (config.slippage_pct / 100.0)
                    trade = positions.close_signal(
                        exit_index=index,
                        exit_time=bar.timestamp,
                        exit_price=fill_price,
                        commission_pct=config.commission_pct,
                    )
                    if trade is not None:
                        journal.record(trade)

        if not positions.flat and window:
            last = window[-1]
            last_price = last.close - last.close * (config.slippage_pct / 100.0)
            trade = positions.close_end(
                exit_index=len(window) - 1,
                exit_time=last.timestamp,
                exit_price=last_price,
                commission_pct=config.commission_pct,
            )
            if trade is not None:
                journal.record(trade)

        trades = journal.trades
        curve = compute_equity_curve(
            trades, config.initial_capital, window[0].timestamp if window else None
        )
        metrics = compute_metrics(trades, curve, config.initial_capital)
        return StrategyResult(
            strategy_id=definition.id,  # type: ignore[attr-defined]
            name=definition.label,  # type: ignore[attr-defined]
            config=config,
            trades=trades,
            equity_curve=curve,
            metrics=metrics,
            bars_used=len(window),
            period_start=window[0].timestamp if window else None,
            period_end=window[-1].timestamp if window else None,
        )


def run_variant_backtest(
    repository,
    registry,
    dataset,
    execution_id: str,
    variant_params: dict[str, float],
):
    """Run a single parameter-variant backtest, return a ResearchDataset.

    Moved here from ``strategy.research.robustness`` so that strategy never
    imports backtest (allowed graph: backtest → strategy only). Research
    passes this function as ``variant_executor`` to
    ``run_parameter_sensitivity``. Logic is verbatim the moved helper.
    """
    from strategy import ResearchDataset

    # Build backtest config from the dataset's data identity
    ident = dataset.data_identity
    config = BacktestConfig(
        symbol=str(ident.get("symbol", "UNKNOWN")),
        timeframe=str(ident.get("timeframe", "1D")),
        start_date=str(ident.get("start_date", "2000-01-01")),
        end_date=str(ident.get("end_date", "2025-12-31")),
        slippage_pct=float(ident.get("slippage_pct", 0)),
        commission_pct=float(ident.get("commission_pct", 0)),
        initial_capital=float(getattr(dataset, "_initial_capital", 10000.0)),
    )

    # Get the strategy definition
    try:
        from strategy import StrategyRegistry  # noqa: F401

        definition = registry.get(str(dataset.strategy_id))
    except Exception:
        # Fallback to the shared test-strategy key. (Note: there is no
        # importable TestStrategy symbol — the registry itself must provide
        # this key; a previous revision imported a non-existent name here,
        # which masked the real error with an ImportError.)
        definition = registry.get("TestStrategy")

    runner = BacktestRunner(repository, registry)
    result = runner.run(config, (str(definition.id),), on_progress=None)

    # Collect trades from the result
    all_trades: list = []
    for sr in result.results:
        all_trades.extend(list(sr.trades))

    return ResearchDataset(
        strategy_id=str(dataset.strategy_id),
        version_id=str(dataset.version_id),
        execution_ids=(execution_id,) + dataset.execution_ids,
        trades=tuple(all_trades) if all_trades else (),
        signals=dataset.signals,
        parameters=variant_params,
        data_identity=dataset.data_identity,
        metadata={
            "variant": dataset.metadata.get("variant", None),
            "param": dataset.metadata.get("param", None),
            "execution_id": execution_id,
            "backtest_executed": True,
            "strategy_id": str(dataset.strategy_id),
            "version_id": str(dataset.version_id),
        },
    )
