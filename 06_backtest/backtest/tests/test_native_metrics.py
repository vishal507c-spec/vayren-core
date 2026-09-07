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
