"""BacktestRunner — orchestrates replay → VM strategy → execution → journal → metrics.

Canonical path: .vstrat → Parser → Compiler → IR → Universal VM → Signal.

No Python builtin factory, no exec fallback. VM is the ONLY strategy execution path.
Registry is kept only for backward compat (legacy definitions) but not used for
.vstrat strategies. If registry is provided and strategy is found there, it is
used only to preserve old tests; otherwise VM path is taken. New code should
use VM path (data_dir + StrategyRecord).
"""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Any

from strategy.models.signal import SignalKind
from strategy.models.state import StrategyState
from strategy.runtime import BarView

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

    Execution is VM-only: .vstrat source → IR → StrategyVM. No exec().
    """

    def __init__(
        self,
        repository: Any,
        registry: Any | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        # data_dir for .vstrat lookup; if not given, try to derive from repository
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
        # Try VM path first: load .vstrat by id or name
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

        # Compile .vstrat → IR → VM (only path, fail loudly)
        try:
            from strategy.language import compile_strategy
            from strategy.models.parameters import StrategyParameters

            compiled = compile_strategy(rec.code)
            # Use VM exclusively
            logic = compiled.create_logic(StrategyParameters(compiled.param_defaults))
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
            # VM expects BarView with params (use compiled defaults)
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
