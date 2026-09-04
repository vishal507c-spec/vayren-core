"""Multi-symbol batch coordinator: chaining, merge honesty, error isolation."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from backtest.events import BacktestCompleted, BacktestFailed
from backtest.models.config import BacktestConfig
from backtest.models.result import BacktestResult, StrategyResult
from backtest.models.trade import TradeRecord
from PySide6.QtWidgets import QApplication

from app.services.multi_symbol_backtest import (
    MERGED_SYMBOL,
    BatchRequest,
    MultiSymbolBacktestCoordinator,
    merge_results,
)


class _Bus:
    def __init__(self) -> None:
        self.published: list = []

    def publish(self, event) -> None:  # noqa: ANN001
        self.published.append(event)


def _request() -> BatchRequest:
    return BatchRequest(
        strategy_id="s",
        timeframe="15m",
        start_date="2023-01-01",
        end_date="2023-03-10",
        initial_capital=1_000_000.0,
        slippage_pct=0.02,
        commission_pct=0.03,
    )


def _result(symbol: str, day: int, pnl: float) -> StrategyResult:
    trade = TradeRecord(
        symbol=symbol,
        side="LONG",
        entry_index=day,
        exit_index=day + 1,
        entry_time=f"2023-01-{day:02d} 10:00:00",
        exit_time=f"2023-01-{day:02d} 10:15:00",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=10.0,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
        commission=1.0,
        bars_held=1,
        exit_reason="SIGNAL",
    )
    config = BacktestConfig(
        symbol=symbol,
        timeframe="15m",
        start_date="2023-01-01",
        end_date="2023-01-10",
        initial_capital=1_000_000.0,
    )
    from backtest.engine.metrics import compute_equity_curve, compute_metrics

    curve = compute_equity_curve((trade,), 1_000_000.0, f"2023-01-{day:02d} 09:15:00")
    metrics = compute_metrics((trade,), curve, 1_000_000.0)
    return StrategyResult(
        strategy_id="s",
        name="S v1.0",
        config=config,
        trades=(trade,),
        equity_curve=curve,
        metrics=metrics,
        bars_used=100,
        period_start=f"2023-01-{day:02d} 09:15:00",
        period_end="2023-01-10 15:15:00",
    )


def _complete(
    coord: MultiSymbolBacktestCoordinator, request_id: str, result: StrategyResult
) -> None:
    # The fake bus records only; drive the coordinator handler directly,
    # exactly as the real bus subscription would.
    coord.on_completed(
        BacktestCompleted(request_id=request_id, result=BacktestResult(results=(result,)))
    )


def test_start_publishes_first_symbol_request(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    rid = coord.start(("AAA", "BBB"), _request())
    assert coord.active
    assert len(bus.published) == 1
    event = bus.published[0]
    assert event.symbol == "AAA"
    assert event.request_id == rid
    assert coord.owns(rid)
    assert coord.is_member(rid)


def test_batch_chains_and_merges(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    coord.start(("AAA", "BBB"), _request())
    first_id = coord._current_id
    assert first_id is not None
    _complete(coord, first_id, _result("AAA", 3, 50.0))
    # advanced to second symbol
    assert coord.active
    second_id = coord._current_id
    assert second_id is not None and second_id != first_id
    _complete(coord, second_id, _result("BBB", 5, -20.0))
    assert not coord.active
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.symbols == ("AAA", "BBB")
    assert outcome.errors == {}
    merged = outcome.merged
    assert merged is not None
    assert merged.config.symbol == MERGED_SYMBOL
    # trades keep their own symbol, ordered by entry time
    assert [t.symbol for t in merged.trades] == ["AAA", "BBB"]
    assert merged.metrics.total_trades == 2
    assert merged.metrics.net_profit == 30.0
    assert outcome.bars_by_symbol == {"AAA": 100, "BBB": 100}
    # final aggregate event published for the normal UI path
    final = bus.published[-1]
    assert isinstance(final, BacktestCompleted)
    assert not coord.is_member(final.request_id)


def test_failed_symbol_isolated_not_corrupting(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    coord.start(("AAA", "BBB", "CCC"), _request())
    rid_a = coord._current_id
    assert rid_a is not None
    _complete(coord, rid_a, _result("AAA", 3, 50.0))
    bus.published.clear()
    rid_b = coord._current_id
    assert rid_b is not None
    coord.on_failed(BacktestFailed(request_id=rid_b, reason="no bars"))
    rid_c = coord._current_id
    assert rid_c is not None
    _complete(coord, rid_c, _result("CCC", 7, 10.0))
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert set(outcome.errors) == {"BBB"}
    assert [r.config.symbol for r in outcome.results] == ["AAA", "CCC"]
    assert outcome.merged is not None
    assert outcome.merged.metrics.total_trades == 2


def test_all_failed_emits_batch_failed(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    reasons: list = []
    coord.batch_failed.connect(reasons.append)
    coord.start(("AAA", "BBB"), _request())
    rid1 = coord._current_id
    assert rid1 is not None
    coord.on_failed(BacktestFailed(request_id=rid1, reason="e1"))
    rid2 = coord._current_id
    assert rid2 is not None
    coord.on_failed(BacktestFailed(request_id=rid2, reason="e2"))
    assert reasons and "AAA: e1" in reasons[0]
    assert not coord.active


def test_cancel_drops_inflight(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    rid = coord.start(("AAA", "BBB"), _request())
    coord.cancel()
    assert not coord.active
    # stale completion is ignored by the coordinator but still member-flagged
    # so the regular UI handler skips it too
    coord.on_completed(
        BacktestCompleted(request_id=rid, result=BacktestResult(results=(_result("AAA", 3, 1.0),)))
    )
    assert coord.is_member(rid)


def test_merge_single_result_passthrough_shape() -> None:
    req = _request()
    merged, bars = merge_results([_result("AAA", 3, 50.0)], req)
    assert merged.metrics.total_trades == 1
    assert bars == {"AAA": 100}


def test_non_batch_events_pass_through(qt_app: QApplication) -> None:
    _ = qt_app
    bus = _Bus()
    coord = MultiSymbolBacktestCoordinator(bus)
    coord.on_completed(BacktestCompleted(request_id="other", result=BacktestResult(results=())))
    assert bus.published == []  # coordinator ignored it entirely
