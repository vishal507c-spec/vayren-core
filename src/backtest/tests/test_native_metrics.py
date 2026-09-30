"""Contract tests for backtest.native_metrics (drawdown, equity curve, Sharpe, backtest report)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from backtest import native_metrics as nm  # noqa: E402


def test_max_drawdown_empty() -> None:
    pct, val = nm.max_drawdown([])
    assert pct == 0.0
    assert val == 0.0


def test_max_drawdown_calculation() -> None:
    # Equity curve: 100 -> 120 -> 90 -> 110 -> 80
    # Peak: 120, trough: 80 => absolute drop = 40, pct drop = 40/120 * 100 = 33.333%
    equities = [100.0, 120.0, 90.0, 110.0, 80.0]
    pct, val = nm.max_drawdown(equities)
    assert val == pytest.approx(40.0)
    assert pct == pytest.approx(100.0 * 40.0 / 120.0)


def test_equity_curve_points() -> None:
    initial = 1000.0
    pnls = [100.0, -50.0, 200.0]
    points = nm.equity_curve_points(initial, pnls)
    assert len(points) == 3
    # Step 1: 1100, peak 1100 => dd 0
    assert points[0][0] == 1100.0
    assert points[0][1] == 0.0
    # Step 2: 1050, peak 1100 => dd 50/1100 * 100
    assert points[1][0] == 1050.0
    assert points[1][1] == pytest.approx(100.0 * 50.0 / 1100.0)
    # Step 3: 1250, peak 1250 => dd 0
    assert points[2][0] == 1250.0
    assert points[2][1] == 0.0


def test_sharpe_undefined_cases() -> None:
    # Single trade -> undefined
    assert nm.sharpe([10.0], [5.0], 1000.0) is None
    # Empty trades -> undefined
    assert nm.sharpe([], [], 1000.0) is None


def test_sharpe_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="must share a length"):
        nm.sharpe([10.0, 20.0], [5.0], 1000.0)


def test_report_comprehensive() -> None:
    initial = 10000.0
    pnls = [100.0, -50.0, 200.0, -30.0, 150.0]
    bars = [10.0, 5.0, 12.0, 4.0, 8.0]
    entry_prices = [100.0, 200.0, 150.0, 300.0, 250.0]

    rep = nm.report(
        pnls=pnls,
        bars_held=bars,
        equities=entry_prices,
        initial_capital=initial,
    )

    assert rep.total_trades == 5
    assert rep.gross_profit == pytest.approx(450.0)
    assert rep.gross_loss == pytest.approx(-80.0)
    assert rep.starting_capital == initial
    assert rep.win_rate == pytest.approx(3.0 / 5.0)
