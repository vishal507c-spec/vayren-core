"""Backtest orchestration — drives Python strategies over canonical bars.

Composition-root glue: the per-bar execution loop mirrors the Rust
``backtest_engine::execute_bars`` semantics (warmup feed, SL/TP first with a
consumed bar, signal entries at full available equity, opposite-signal
exits, final-bar END close). Every numeric decision crosses the FFI
boundary instead: entries/exits through ``backtest.native_positions``,
aggregates through ``backtest.native_metrics``. No Python math, no
fabricated results — insufficient data fails closed with an actionable
error.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_SLIPPAGE_PCT = 0.02
_DEFAULT_COMMISSION_PCT = 0.03


class BacktestError(Exception):
    """Actionable backtest failure (message is safe to show in the UI)."""


def _strategy_source(name: str, strategy_dir=None) -> tuple[str, str]:
    """Resolve `name` to ``(kind, code)``: library file wins over built-in."""
    from strategy.language.storage import load_strategy

    code = load_strategy(name, strategy_dir)
    if code is not None:
        return "library", code
    from strategy import builtins

    try:
        return "built-in", builtins.builtin_source(name)
    except KeyError:
        raise BacktestError(
            f"Unknown strategy {name!r}: no library file and no built-in by that name"
        ) from None


def describe_strategy(name: str, strategy_dir=None) -> dict:
    """Parameter specs, defaults and source for the Lab workspace echo."""
    from strategy.language.compiler import compile_strategy

    kind, code = _strategy_source(name, strategy_dir)
    try:
        compiled = compile_strategy(code)
    except Exception as exc:
        raise BacktestError(f"Strategy failed: {name}: {exc}") from exc
    params = [
        {
            "key": spec.key,
            "label": spec.label,
            "value": str(spec.default),
        }
        for spec in compiled.param_specs
    ]
    return {
        "name": name,
        "kind": kind,
        "code": code,
        "params": params,
        "defaults": dict(compiled.param_defaults),
    }


def _make_logic(name: str, strategy_dir=None):
    """Instantiate strategy logic with validated default parameters."""
    from strategy.language.compiler import compile_strategy
    from strategy.models.parameters import StrategyParameters

    kind, code = _strategy_source(name, strategy_dir)
    try:
        compiled = compile_strategy(code)
    except Exception as exc:
        raise BacktestError(f"Strategy failed: {name}: {exc}") from exc
    params = StrategyParameters.from_specs(compiled.param_specs).validated(compiled.param_specs)
    try:
        return compiled.create_logic(params, owner_id=name), kind
    except Exception as exc:
        raise BacktestError(f"Strategy failed: {name}: {exc}") from exc


def run_backtest(
    strategy_name: str,
    symbols: list[str],
    timeframe: str | None,
    start: str | None,
    end: str | None,
    capital: float,
    mode: str = "buy",
    data_dir=None,
    strategy_dir=None,
    market=None,
    cost_pct: float | None = None,
) -> dict:
    """Execute one backtest over real historical bars.

    ``cost_pct`` is the UI transaction-cost echo in percent-per-side (the
    same units as ``_DEFAULT_COMMISSION_PCT``). ``None`` keeps the default;
    negative values fail closed. Threaded into the native fill/close path,
    never applied as a post-hoc haircut.

    Returns the Lab ``results`` block (metrics/ranking/trades/equity_curve/
    risk_notes) in the exact bridge contract the native Lab projection reads.
    """
    from backtest import native_metrics, native_positions

    from app.services.market_data_service import MarketDataError, MarketDataService

    direction = (mode or "buy").strip().lower()
    if direction not in ("buy", "sell"):
        raise BacktestError(f"Invalid backtest mode {mode!r} (use buy or sell)")
    if capital is None or not capital > 0:
        raise BacktestError("Initial capital must be positive")
    if not symbols:
        raise BacktestError("No universe: select at least one symbol")
    if start is not None and end is not None and start > end:
        raise BacktestError(f"Start date is after end date ({start} > {end})")
    if cost_pct is None:
        commission_pct = _DEFAULT_COMMISSION_PCT
    else:
        try:
            commission_pct = float(cost_pct)
        except (TypeError, ValueError) as exc:
            raise BacktestError(f"Invalid transaction cost {cost_pct!r}") from exc
        if not commission_pct >= 0:
            raise BacktestError(f"Transaction cost must be non-negative ({cost_pct!r})")

    if market is None:
        try:
            market = MarketDataService(data_dir)
        except MarketDataError as exc:
            raise BacktestError(str(exc)) from exc

    want_long = direction == "buy"
    all_trades: list[dict] = []
    ranking: list[dict] = []
    for symbol in symbols:
        try:
            bars = market.get_bars(symbol, timeframe, None, start, end)
        except MarketDataError as exc:
            raise BacktestError(str(exc)) from exc
        logic, _kind = _make_logic(strategy_name, strategy_dir)
        warmup = max(0, int(logic.warmup()))
        if len(bars) < warmup + 2:
            raise BacktestError(
                f"Insufficient data for {symbol}: {len(bars)} bars "
                f"(strategy needs more than {warmup} warmup bars)"
            )
        trades = _run_symbol(
            logic,
            bars,
            symbol,
            capital + sum(t["pnl"] for t in all_trades),
            warmup,
            want_long,
            native_positions,
            commission_pct,
        )
        all_trades.extend(trades)
        ranking.append(_rank_row(symbol, trades, capital))
    ranking.sort(key=lambda row: row["net_profit"], reverse=True)
    for position, row in enumerate(ranking, start=1):
        row["rank"] = position
    metrics = _aggregate_metrics(all_trades, capital, native_metrics)
    curve = [{"equity": capital, "drawdown_pct": 0.0}]
    curve.extend(
        {"equity": equity, "drawdown_pct": dd}
        for equity, dd in native_metrics.equity_curve_points(
            capital, [t["pnl"] for t in all_trades]
        )
    )
    notes: list[str] = []
    if metrics["total_trades"] > 0:
        notes.append(
            f"Max DD -{metrics['max_drawdown_pct']:.2f}% over {len(all_trades)} closed trades"
        )
    return {
        "metrics": metrics,
        "ranking": ranking,
        "trades": all_trades,
        "equity_curve": curve,
        "risk_notes": notes,
    }


def _run_symbol(
    logic,
    bars,
    symbol: str,
    equity: float,
    warmup: int,
    want_long: bool,
    native_positions,
    commission_pct: float = _DEFAULT_COMMISSION_PCT,
) -> list[dict]:
    """Single-symbol execution pass (mirrors ``execute_bars`` bar order)."""
    from strategy.models.parameters import StrategyParameters
    from strategy.models.signal import SignalKind
    from strategy.models.state import StrategyState
    from strategy.runtime import BarView

    params = StrategyParameters(getattr(logic, "params", {}) or {})
    flat_state = StrategyState()
    for index in range(min(warmup, len(bars))):
        try:
            logic.on_bar(BarView(bars=bars, index=index, params=params, state=flat_state))
        except Exception as exc:
            raise BacktestError(f"Strategy failed: {exc}") from exc
    trades: list[dict] = []
    realized = 0.0
    side: str | None = None
    entry_price = 0.0
    entry_index = 0
    entry_time = ""
    quantity = 0.0
    commission_entry = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    for index in range(warmup, len(bars)):
        bar = bars[index]
        if side is not None:
            try:
                closed = native_positions.try_close(
                    side,
                    stop_loss,
                    take_profit,
                    bar.high,
                    bar.low,
                    bar.close,
                    entry_price,
                    quantity,
                    commission_entry,
                    commission_pct,
                    False,
                )
            except Exception as exc:
                raise BacktestError(f"Strategy failed: {exc}") from exc
            if closed is not None:
                realized += closed.pnl
                trades.append(
                    _trade(
                        symbol,
                        side,
                        entry_index,
                        entry_time,
                        entry_price,
                        index,
                        bar.timestamp,
                        closed,
                    )
                )
                side = None
                continue
            state = StrategyState.open(side, entry_price, entry_index)
        else:
            state = flat_state
        try:
            signal = logic.on_bar(BarView(bars=bars, index=index, params=params, state=state))
        except Exception as exc:
            raise BacktestError(f"Strategy failed: {exc}") from exc
        if signal is None:
            continue
        is_buy = signal.kind == SignalKind.BUY
        if side is None:
            if is_buy != want_long:
                continue
            leg = "LONG" if is_buy else "SHORT"
            try:
                filled = native_positions.fill(
                    leg,
                    bar.close,
                    equity + realized,
                    _DEFAULT_SLIPPAGE_PCT,
                    commission_pct,
                )
            except Exception as exc:
                raise BacktestError(f"Strategy failed: {exc}") from exc
            if filled is None:
                continue
            side = filled.side
            entry_price = filled.fill_price
            entry_index = index
            entry_time = bar.timestamp
            quantity = filled.quantity
            commission_entry = filled.commission
            stop_loss = signal.stop_loss
            take_profit = signal.take_profit
        else:
            try:
                exit_px = native_positions.signal_exit(
                    side, is_buy, bar.close, _DEFAULT_SLIPPAGE_PCT
                )
            except Exception as exc:
                raise BacktestError(f"Strategy failed: {exc}") from exc
            if exit_px is None:
                continue
            try:
                closed = native_positions.close_trade(
                    side,
                    "SIGNAL",
                    entry_price,
                    quantity,
                    commission_entry,
                    exit_px,
                    commission_pct,
                    stop_loss,
                )
            except Exception as exc:
                raise BacktestError(f"Strategy failed: {exc}") from exc
            realized += closed.pnl
            trades.append(
                _trade(
                    symbol,
                    side,
                    entry_index,
                    entry_time,
                    entry_price,
                    index,
                    bar.timestamp,
                    closed,
                )
            )
            side = None
    if side is not None and bars:
        last = bars[-1]
        try:
            exit_px = native_positions.exit_price(side, last.close, _DEFAULT_SLIPPAGE_PCT)
            closed = native_positions.close_trade(
                side,
                "END",
                entry_price,
                quantity,
                commission_entry,
                exit_px,
                commission_pct,
                stop_loss,
            )
        except Exception as exc:
            raise BacktestError(f"Strategy failed: {exc}") from exc
        trades.append(
            _trade(
                symbol,
                side,
                entry_index,
                entry_time,
                entry_price,
                len(bars) - 1,
                last.timestamp,
                closed,
            )
        )
    return trades


def _trade(symbol, side, entry_index, entry_time, entry_price, exit_index, exit_time, closed):
    """One closed trade in the Lab bridge contract."""
    return {
        "symbol": symbol,
        "side": side,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_px": entry_price,
        "exit_px": closed.exit_price,
        "pnl": closed.pnl,
        "r_multiple": closed.r_multiple,
        "bars": exit_index - entry_index,
        "reason": closed.exit_reason,
        "winning": closed.pnl > 0,
    }


def _rank_row(symbol: str, trades: list[dict], capital: float) -> dict:
    """Per-symbol ranking row (metrics recomputed on the symbol's trades)."""
    from backtest import native_metrics

    pnls = [t["pnl"] for t in trades if t["symbol"] == symbol]
    held = [float(t["bars"]) for t in trades if t["symbol"] == symbol]
    equities = _running_equities(capital, pnls)
    report = native_metrics.report(pnls, held, equities, capital)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "rank": 0,
        "symbol": symbol,
        "status": "ranked" if pnls else "no-trades",
        "net_profit": report.net_profit,
        "return_pct": report.net_profit / capital * 100.0 if capital else 0.0,
        "total_trades": report.total_trades,
        "win_rate": report.win_rate * 100.0 if report.win_rate is not None else None,
        "profit_factor": report.profit_factor,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "winning_trades": wins,
        "losing_trades": len(pnls) - wins,
    }


def _running_equities(capital: float, pnls: list[float]) -> list[float]:
    """Running equity after each trade (the kernel's curve input)."""
    equities: list[float] = []
    running = capital
    for pnl in pnls:
        running += pnl
        equities.append(running)
    return equities


def _aggregate_metrics(trades: list[dict], capital: float, native_metrics) -> dict:
    """Header KPIs over the combined trade stream (kernel-computed)."""
    pnls = [t["pnl"] for t in trades]
    held = [float(t["bars"]) for t in trades]
    report = native_metrics.report(pnls, held, _running_equities(capital, pnls), capital)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "net_profit": report.net_profit,
        "net_profit_pct": report.net_profit_pct,
        "total_trades": report.total_trades,
        "winning_trades": wins,
        "losing_trades": len(pnls) - wins,
        "win_rate": report.win_rate * 100.0 if report.win_rate is not None else None,
        "profit_factor": report.profit_factor,
        "expectancy": report.expectancy,
        "avg_trade": report.avg_trade,
        "max_drawdown_pct": report.max_drawdown_pct,
        "max_drawdown_abs": report.max_drawdown_abs,
        "sharpe_ratio": report.sharpe_ratio,
        "return_pct": report.net_profit / capital * 100.0 if capital else 0.0,
    }
