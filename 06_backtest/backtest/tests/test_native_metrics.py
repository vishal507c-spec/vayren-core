"""Parity: Rust metrics kernels vs frozen Python references.

The reference functions below are verbatim copies of the pre-migration
Python math (kept in the TEST as oracles, never in production). The Rust
kernels must match them bit-for-bit on deterministic fixtures and seeded
random fuzz, including edge cases (empty, zero peak, negative equity,
zero variance). Production holds no Python math (migration §12).
"""

from __future__ import annotations

import math
import random

import pytest

from backtest import native_metrics


def _ref_drawdown(equities: list[float]) -> tuple[float, float]:
    if not equities:
        return 0.0, 0.0
    peak = equities[0]
    max_pct = 0.0
    max_abs = 0.0
    for equity in equities:
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        dd_pct = (dd_abs / peak * 100.0) if peak else 0.0
        if dd_pct > max_pct:
            max_pct = dd_pct
        if dd_abs > max_abs:
            max_abs = dd_abs
    return max_pct, max_abs


def _ref_equity(initial: float, pnls: list[float]) -> tuple[list[float], list[float]]:
    equity = initial
    peak = initial
    equities: list[float] = []
    drawdowns: list[float] = []
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak else 0.0
        equities.append(equity)
        drawdowns.append(dd)
    return equities, drawdowns


def _ref_sharpe(pnls: list[float], bars: list[float], initial: float) -> float | None:
    if len(pnls) < 2:
        return None
    equity = initial
    returns: list[float] = []
    for pnl in pnls:
        before = equity
        equity += pnl
        if before <= 0.0:
            continue
        returns.append(equity / before - 1.0)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return None
    std = math.sqrt(variance)
    mean_bars = sum(bars) / len(pnls) if pnls else 1
    trades_per_year = (252.0 * 25.0) / max(1.0, mean_bars)
    annual_factor = math.sqrt(trades_per_year)
    return (mean / std) * annual_factor if std else None


def _close(a: float, b: float) -> bool:
    return a == b or abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def test_drawdown_deterministic() -> None:
    assert native_metrics.max_drawdown([]) == (0.0, 0.0)
    assert native_metrics.max_drawdown([100.0, 110.0]) == (0.0, 0.0)
    assert native_metrics.max_drawdown([100.0, 80.0, 90.0]) == (20.0, 20.0)
    assert native_metrics.max_drawdown([0.0, 0.0]) == (0.0, 0.0)


def test_drawdown_fuzz() -> None:
    rng = random.Random(20260907)
    for _ in range(200):
        n = rng.randint(0, 60)
        curve = [rng.uniform(-50.0, 150.0) for _ in range(n)]
        expected = _ref_drawdown(curve)
        actual = native_metrics.max_drawdown(curve)
        assert _close(actual[0], expected[0]) and _close(actual[1], expected[1]), curve


def test_equity_curve_fuzz() -> None:
    rng = random.Random(424242)
    for _ in range(200):
        n = rng.randint(0, 40)
        pnls = [rng.uniform(-500.0, 500.0) for _ in range(n)]
        initial = rng.choice([100.0, 1000.0, 1_000_000.0])
        expected_eq, expected_dd = _ref_equity(initial, pnls)
        actual = native_metrics.equity_curve_points(initial, pnls)
        assert len(actual) == n
        for (got_eq, got_dd), (want_eq, want_dd) in zip(
            actual, zip(expected_eq, expected_dd, strict=True), strict=True
        ):
            assert _close(got_eq, want_eq) and _close(got_dd, want_dd), (initial, pnls)


def test_sharpe_deterministic() -> None:
    assert native_metrics.sharpe([5.0], [1.0], 100.0) is None
    assert native_metrics.sharpe([], [], 100.0) is None
    assert native_metrics.sharpe([0.0, 0.0], [1.0, 1.0], 100.0) is None
    assert native_metrics.sharpe([-200.0, 50.0, 50.0], [1.0, 1.0, 1.0], 100.0) is None


def test_sharpe_fuzz() -> None:
    rng = random.Random(777)
    checked = 0
    for _ in range(300):
        n = rng.randint(0, 25)
        pnls = [rng.uniform(-300.0, 300.0) for _ in range(n)]
        bars = [float(rng.randint(1, 50)) for _ in range(n)]
        expected = _ref_sharpe(pnls, bars, 10_000.0)
        actual = native_metrics.sharpe(pnls, bars, 10_000.0)
        if expected is None:
            assert actual is None, (pnls, bars)
        else:
            assert actual is not None and _close(actual, expected), (pnls, bars)
            checked += 1
    assert checked > 50, "fuzz must exercise defined Sharpe cases"


def _ref_report(
    pnls: list[float],
    bars: list[float],
    equities: list[float],
    initial: float,
) -> dict[str, object]:
    """Verbatim pre-migration `compute_metrics` aggregate, kept as the oracle."""
    total = len(pnls)
    final = equities[-1] if equities else initial
    net = final - initial
    net_pct = (net / initial * 100.0) if initial else 0.0
    if total == 0:
        return {
            "net_profit": net,
            "net_profit_pct": net_pct,
            "total_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
            "max_drawdown_abs": 0.0,
            "avg_trade": None,
            "expectancy": None,
            "sharpe_ratio": None,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "starting_capital": initial,
            "ending_capital": final,
        }
    gross_profit = sum(p for p in pnls if p > 0.0)
    gross_loss = sum(p for p in pnls if p < 0.0)
    avg = sum(pnls) / total
    dd_pct, dd_abs = _ref_drawdown(equities)
    return {
        "net_profit": net,
        "net_profit_pct": net_pct,
        "total_trades": total,
        "win_rate": sum(1 for p in pnls if p > 0.0) / total,
        "profit_factor": (gross_profit / abs(gross_loss)) if gross_loss != 0.0 else None,
        "max_drawdown_pct": dd_pct,
        "max_drawdown_abs": dd_abs,
        "avg_trade": avg,
        "expectancy": avg,
        "sharpe_ratio": _ref_sharpe(pnls, bars, initial),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "starting_capital": initial,
        "ending_capital": final,
    }


def _assert_same_report(got: native_metrics.NativeReport, want: dict[str, object]) -> None:
    for name, expected in want.items():
        actual = getattr(got, name)
        if isinstance(expected, float) and isinstance(actual, float):
            assert _close(actual, expected), (name, actual, expected)
        else:
            assert actual == expected, (name, actual, expected)


def test_no_trades_still_reports_the_capital_shape() -> None:
    _assert_same_report(
        native_metrics.report([], [], [1000.0], 1000.0),
        _ref_report([], [], [1000.0], 1000.0),
    )
    empty = native_metrics.report([], [], [], 1000.0)
    assert empty.total_trades == 0
    assert empty.win_rate is empty.profit_factor is empty.avg_trade is None
    assert empty.expectancy is empty.sharpe_ratio is None
    assert (empty.starting_capital, empty.ending_capital) == (1000.0, 1000.0)
    assert (empty.net_profit, empty.net_profit_pct) == (0.0, 0.0)


def test_win_rate_and_profit_factor_are_separate_verdicts() -> None:
    # Every trade won: gross loss is zero, so the factor stays undefined
    # while the win rate is a hard 1.0.
    got = native_metrics.report([10.0, 20.0], [1.0, 1.0], [1000.0, 1010.0, 1030.0], 1000.0)
    assert got.win_rate == 1.0
    assert got.profit_factor is None
    assert (got.gross_profit, got.gross_loss) == (30.0, 0.0)
    # A breakeven trade is neither a win nor part of either gross total.
    flat = native_metrics.report([10.0, -10.0, 0.0], [1.0, 1.0, 1.0], [100.0, 110.0, 100.0], 100.0)
    assert flat.win_rate is not None and _close(flat.win_rate, 1 / 3)
    assert flat.gross_profit == 10.0 and flat.gross_loss == -10.0
    assert flat.profit_factor == 1.0


def test_drawdown_is_read_off_the_curve_on_screen() -> None:
    # The curve dips far below what the trade PnLs alone would accumulate:
    # the displayed drawdown follows the curve, not a recomputed one.
    got = native_metrics.report([-100.0, 50.0], [1.0, 1.0], [1000.0, 900.0, 1200.0], 1000.0)
    assert _close(got.max_drawdown_pct, 10.0) and _close(got.max_drawdown_abs, 100.0)
    assert _close(got.ending_capital, 1200.0) and _close(got.net_profit, 200.0)
    assert _close(got.net_profit_pct, 20.0)
    # Zero starting capital: percentages fall back to 0.0, never to a crash.
    zeroed = native_metrics.report([5.0], [1.0], [5.0], 0.0)
    assert zeroed.net_profit_pct == 0.0 and zeroed.net_profit == 5.0


def test_report_fuzz() -> None:
    rng = random.Random(20260922)
    for _ in range(250):
        n = rng.randint(0, 20)
        pnls = [rng.choice([0.0, rng.uniform(-400.0, 400.0)]) for _ in range(n)]
        bars = [float(rng.randint(1, 40)) for _ in range(n)]
        initial = rng.choice([0.0, 100.0, 10_000.0, 1_000_000.0])
        curve_points = rng.randint(0, n + 1)
        equities = [initial] + [rng.uniform(-200.0, 20_000.0) for _ in range(curve_points)]
        want = _ref_report(pnls, bars, equities, initial)
        _assert_same_report(native_metrics.report(pnls, bars, equities, initial), want)


def test_report_requires_paired_pnl_and_bar_lengths() -> None:
    with pytest.raises(ValueError, match="share a length"):
        native_metrics.report([1.0, 2.0], [1.0], [100.0, 101.0, 103.0], 100.0)
