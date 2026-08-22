"""BacktestRunner — orchestrates replay → strategy → execution → journal → metrics."""

from __future__ import annotations

from logging import getLogger

from strategy.models.signal import SignalKind
from strategy.models.state import StrategyState
from strategy.registry import StrategyRegistry
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
    """

    def __init__(self, repository, registry: StrategyRegistry) -> None:
        self._repository = repository
        self._registry = registry

    def run(
        self,
        config: BacktestConfig,
        strategy_ids: tuple[str, ...],
        on_progress=None,  # type: ignore[no-untyped-def]
    ) -> BacktestResult:
        """Execute the backtest and return per-strategy results."""
        results: list[StrategyResult] = []
        errors: list[str] = []

        for strategy_id in strategy_ids:
            try:
                definition = self._registry.get(strategy_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"unknown strategy {strategy_id}: {exc}")
                continue
            if not definition.enabled:
                continue
            result = self._run_one(definition, config, on_progress)
            if result is not None:
                results.append(result)
            else:
                errors.append(f"{definition.label}: no bars in requested range")

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

    def _run_one(self, definition, config: BacktestConfig, on_progress) -> StrategyResult | None:  # type: ignore[no-untyped-def]
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

        factory = self._registry.factory(definition.kind)
        logic = factory(definition.params)
        warmup = max(0, logic.warmup())

        journal = TradeJournal()
        positions = PositionManager()
        simulator = ExecutionSimulator(
            slippage_pct=config.slippage_pct, commission_pct=config.commission_pct
        )

        for index in range(warmup, len(window)):
            bar = window[index]
            if on_progress is not None and index % 500 == 0:
                try:
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
            view = BarView(bars=window, index=index, params=definition.params, state=state)

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
                # opposite signal closes position
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
            strategy_id=definition.id,
            name=definition.label,
            config=config,
            trades=trades,
            equity_curve=curve,
            metrics=metrics,
            bars_used=len(window),
            period_start=window[0].timestamp if window else None,
            period_end=window[-1].timestamp if window else None,
        )
