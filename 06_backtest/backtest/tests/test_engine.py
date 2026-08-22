"""Backtest engine tests."""

from market.models.bar import Bar

from backtest.engine.journal import TradeJournal
from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.engine.positions import PositionManager
from backtest.engine.replay import slice_bars
from backtest.engine.simulator import ExecutionSimulator
from backtest.models.trade import TradeRecord


def _bars(n: int = 5) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol="TEST",
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=1000,
            timestamp=f"2026-01-{i + 1:02d} 09:15:00",
        )
        for i in range(n)
    )


def test_slice_bars_range():
    bars = _bars(5)
    sliced = slice_bars(bars, "2026-01-02", "2026-01-04")
    assert len(sliced) == 3
    assert sliced[0].timestamp.startswith("2026-01-02")


def test_slice_bars_open_ended():
    bars = _bars(3)
    assert len(slice_bars(bars, None, None)) == 3
    assert slice_bars((), "2026-01-01", "2026-01-02") == ()


def test_simulator_fill():
    sim = ExecutionSimulator(slippage_pct=0.02, commission_pct=0.03)
    fill = sim.fill("LONG", 100.0, 10000.0)
    assert fill is not None
    assert fill.quantity > 0
    assert fill.commission > 0


def test_position_manager_open_and_close():
    pm = PositionManager()
    assert pm.flat
    pm.open_long("TEST", 0, "2026-01-01 09:15:00", 100.0, 10.0, 1.0)
    assert not pm.flat
    trade = pm.close_signal(1, "2026-01-02 09:15:00", 110.0, 0.03)
    assert trade is not None
    assert trade.pnl > 0
    assert pm.flat


def test_trade_journal():
    j = TradeJournal()
    assert len(j) == 0
    trade = TradeRecord(
        symbol="T",
        side="LONG",
        entry_index=0,
        exit_index=1,
        entry_time="2026-01-01 09:15:00",
        exit_time="2026-01-02 09:15:00",
        entry_price=100,
        exit_price=110,
        quantity=10,
        pnl=90,
        pnl_pct=9,
        commission=2,
        bars_held=1,
        exit_reason="SIGNAL",
    )
    j.record(trade)
    assert len(j) == 1
    assert j.trades[0].winning


def test_compute_metrics_no_trades():
    curve = compute_equity_curve((), 1_000_000, "2026-01-01 09:15:00")
    metrics = compute_metrics((), curve, 1_000_000)
    assert metrics.total_trades == 0
    assert metrics.win_rate is None


def test_compute_metrics_with_trades():
    trade = TradeRecord(
        symbol="T",
        side="LONG",
        entry_index=0,
        exit_index=1,
        entry_time="2026-01-01 09:15:00",
        exit_time="2026-01-02 09:15:00",
        entry_price=100,
        exit_price=110,
        quantity=10,
        pnl=90,
        pnl_pct=9,
        commission=2,
        bars_held=1,
        exit_reason="SIGNAL",
    )
    curve = compute_equity_curve((trade,), 1_000_000, "2026-01-01 09:15:00")
    metrics = compute_metrics((trade,), curve, 1_000_000)
    assert metrics.total_trades == 1
    assert metrics.win_rate == 1.0
    assert metrics.net_profit == 90
