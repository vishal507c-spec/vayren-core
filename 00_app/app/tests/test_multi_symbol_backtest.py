"""Multi-symbol batch coordinator: single batch job, merge honesty, error isolation."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from backtest.events import BacktestCompleted, BacktestFailed, BacktestStarted
from backtest.models.config import BacktestConfig
from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord
from backtest.runner import SymbolBatchResult
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


class _Worker:
    def __init__(self) -> None:
        self.enqueued: list = []
        self.cancels = 0

    def enqueue_batch(self, job) -> None:  # noqa: ANN001
        self.enqueued.append(job)

    def cancel_batch(self) -> None:
        self.cancels += 1


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


def _coord() -> tuple[MultiSymbolBacktestCoordinator, _Bus, _Worker]:
    bus = _Bus()
    worker = _Worker()
    return MultiSymbolBacktestCoordinator(bus, worker), bus, worker


def test_start_enqueues_single_batch_job(qt_app: QApplication) -> None:
    _ = qt_app
    coord, bus, worker = _coord()
    rid = coord.start(("AAA", "BBB"), _request())
    assert coord.active
    assert worker.enqueued and len(worker.enqueued) == 1
    job = worker.enqueued[0]
    assert job.request_id == rid
    assert job.symbols == ("AAA", "BBB")
    assert job.strategy_id == "s"
    # one BacktestStarted engages the existing busy UI
    started = [e for e in bus.published if isinstance(e, BacktestStarted)]
    assert len(started) == 1 and started[0].request_id == rid


def test_batch_done_merges(qt_app: QApplication) -> None:
    _ = qt_app
    coord, bus, _ = _coord()
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    rid = coord.start(("AAA", "BBB"), _request())
    coord.on_batch_done(
        (
            rid,
            (
                SymbolBatchResult("AAA", _result("AAA", 3, 50.0), None),
                SymbolBatchResult("BBB", _result("BBB", 5, -20.0), None),
            ),
        )
    )
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


def test_batch_progress_forwarded(qt_app: QApplication) -> None:
    _ = qt_app
    coord, _, _ = _coord()
    seen: list = []
    coord.batch_progress.connect(seen.append)
    rid = coord.start(("AAA", "BBB"), _request())
    coord.on_batch_progress((rid, 1, 2))
    coord.on_batch_progress(("stale", 2, 2))
    assert seen == [(1, 2)]


def test_failed_symbol_isolated_not_corrupting(qt_app: QApplication) -> None:
    _ = qt_app
    coord, _, _ = _coord()
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    rid = coord.start(("AAA", "BBB", "CCC"), _request())
    coord.on_batch_done(
        (
            rid,
            (
                SymbolBatchResult("AAA", _result("AAA", 3, 50.0), None),
                SymbolBatchResult("BBB", None, "no bars"),
                SymbolBatchResult("CCC", _result("CCC", 7, 10.0), None),
            ),
        )
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert set(outcome.errors) == {"BBB"}
    assert [r.config.symbol for r in outcome.results] == ["AAA", "CCC"]
    assert outcome.merged is not None
    assert outcome.merged.metrics.total_trades == 2


def test_all_failed_emits_batch_failed(qt_app: QApplication) -> None:
    _ = qt_app
    coord, bus, _ = _coord()
    reasons: list = []
    coord.batch_failed.connect(reasons.append)
    rid = coord.start(("AAA", "BBB"), _request())
    coord.on_batch_done(
        (
            rid,
            (
                SymbolBatchResult("AAA", None, "e1"),
                SymbolBatchResult("BBB", None, "e2"),
            ),
        )
    )
    assert reasons and "AAA: e1" in reasons[0]
    assert not coord.active
    assert isinstance(bus.published[-1], BacktestFailed)


def test_worker_failure_surfaces_batch_failed(qt_app: QApplication) -> None:
    _ = qt_app
    coord, bus, _ = _coord()
    reasons: list = []
    coord.batch_failed.connect(reasons.append)
    rid = coord.start(("AAA",), _request())
    coord.on_batch_failed((rid, "boom"))
    assert reasons == ["boom"]
    assert not coord.active
    assert isinstance(bus.published[-1], BacktestFailed)


def test_cancel_drops_stale_done(qt_app: QApplication) -> None:
    _ = qt_app
    coord, _, worker = _coord()
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    rid = coord.start(("AAA", "BBB"), _request())
    coord.cancel()
    assert not coord.active
    assert worker.cancels >= 1
    # stale completion is ignored
    coord.on_batch_done((rid, (SymbolBatchResult("AAA", _result("AAA", 3, 1.0), None),)))
    assert outcomes == []


def test_start_without_worker_raises(qt_app: QApplication) -> None:
    _ = qt_app
    import pytest

    coord = MultiSymbolBacktestCoordinator(_Bus())
    with pytest.raises(RuntimeError):
        coord.start(("AAA",), _request())


def test_merge_single_result_passthrough_shape() -> None:
    req = _request()
    merged, bars = merge_results([_result("AAA", 3, 50.0)], req)
    assert merged.metrics.total_trades == 1
    assert bars == {"AAA": 100}


def test_stale_done_after_restart_ignored(qt_app: QApplication) -> None:
    _ = qt_app
    coord, _, _ = _coord()
    outcomes: list = []
    coord.batch_finished.connect(outcomes.append)
    rid1 = coord.start(("AAA",), _request())
    rid2 = coord.start(("BBB",), _request())
    assert rid1 != rid2
    coord.on_batch_done((rid1, (SymbolBatchResult("AAA", _result("AAA", 3, 1.0), None),)))
    assert outcomes == []


def test_finalizing_emitted_before_merge(qt_app: QApplication) -> None:
    _ = qt_app
    coord, _, _ = _coord()
    events: list = []
    coord.batch_finalizing.connect(lambda rid: events.append(("finalizing", rid)))
    coord.batch_finished.connect(lambda _o: events.append(("finished", None)))
    rid = coord.start(("AAA",), _request())
    coord.on_batch_done((rid, (SymbolBatchResult("AAA", _result("AAA", 3, 1.0), None),)))
    assert events[0] == ("finalizing", rid)
    assert events[-1][0] == "finished"
