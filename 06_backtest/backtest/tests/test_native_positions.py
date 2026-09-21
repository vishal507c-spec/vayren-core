"""Parity: Rust backtest position kernel vs frozen Python references.

The reference functions below are verbatim copies of the pre-migration Python
rule table and trade maths (kept in the TEST as oracles, never in production).
The kernel must match them on deterministic fixtures and seeded fuzz, including
the edges that used to differ: a `0.0` stop that is a real exit level but no
risk basis, touching bars, SL ahead of TP on one leg, zero-cost entries.
"""

from __future__ import annotations

import math
import random

import pytest

from backtest import native_positions


def _ref_exit(
    side: str,
    sl: float | None,
    tp: float | None,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    exit_signal: bool,
) -> tuple[float, str] | None:
    if exit_signal:
        slip = bar_close * 0.0002
        price = bar_close - slip if side == "LONG" else bar_close + slip
        return price, "SIGNAL"
    if side == "LONG":
        if sl is not None and bar_low <= sl:
            return sl, "SL"
        if tp is not None and bar_high >= tp:
            return tp, "TP"
        return None
    if sl is not None and bar_high >= sl:
        return sl, "SL"
    if tp is not None and bar_low <= tp:
        return tp, "TP"
    return None


def _ref_economics(
    side: str,
    entry_price: float,
    quantity: float,
    commission_entry: float,
    exit_price: float,
    commission_pct: float,
    sl: float | None,
) -> tuple[float, float, float, float | None]:
    commission_exit = exit_price * quantity * (commission_pct / 100.0)
    commission = commission_entry + commission_exit
    if side == "LONG":
        gross = (exit_price - entry_price) * quantity
    else:
        gross = (entry_price - exit_price) * quantity
    pnl = gross - commission
    entry_cost = entry_price * quantity
    pnl_pct = (pnl / entry_cost * 100.0) if entry_cost else 0.0
    risk = abs(entry_price - sl) * quantity if sl else None
    r_multiple = (pnl / risk) if risk and risk != 0 else None
    return commission, pnl, pnl_pct, r_multiple


def _close(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return a == b or abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _assert_same_trade(
    closed: native_positions.ClosedTrade,
    side: str,
    entry_price: float,
    quantity: float,
    commission_entry: float,
    commission_pct: float,
    sl: float | None,
    want: tuple[float, str],
) -> None:
    exit_price, reason = want
    want_commission, want_pnl, want_pct, want_r = _ref_economics(
        side, entry_price, quantity, commission_entry, exit_price, commission_pct, sl
    )
    assert closed.exit_reason == reason
    assert _close(closed.exit_price, exit_price), closed
    assert _close(closed.commission, want_commission), closed
    assert _close(closed.pnl, want_pnl), closed
    assert _close(closed.pnl_pct, want_pct), closed
    if want_r is None:
        assert closed.r_multiple is None, closed
    else:
        assert closed.r_multiple is not None and _close(closed.r_multiple, want_r), closed


def test_long_exit_rule() -> None:
    # Stop is checked before target, and the long leg uses the bar extremes.
    got = native_positions.try_close(
        "LONG", 90.0, 120.0, 100.0, 89.0, 95.0, 100.0, 1.0, 0.0, 0.03, False
    )
    assert got is not None and got.exit_price == 90.0 and got.exit_reason == "SL"
    assert (
        native_positions.try_close(
            "LONG", None, None, 100.0, 90.0, 95.0, 100.0, 1.0, 0.0, 0.03, False
        )
        is None
    )
    touching = native_positions.try_close(
        "LONG", 95.0, 200.0, 100.0, 95.0, 99.0, 100.0, 1.0, 0.0, 0.03, False
    )
    assert touching is not None and touching.exit_price == 95.0


def test_short_exit_rule_is_mirrored() -> None:
    stop = native_positions.try_close(
        "SHORT", 110.0, 80.0, 111.0, 90.0, 95.0, 100.0, 1.0, 0.0, 0.03, False
    )
    assert stop is not None and stop.exit_reason == "SL" and stop.exit_price == 110.0
    target = native_positions.try_close(
        "SHORT", 200.0, 80.0, 100.0, 79.0, 95.0, 100.0, 1.0, 0.0, 0.03, False
    )
    assert target is not None and target.exit_reason == "TP" and target.exit_price == 80.0


def test_signal_exit_slips_against_the_position() -> None:
    for side, want in (("LONG", 99.98), ("SHORT", 100.02)):
        got = native_positions.try_close(
            side, 90.0, 120.0, 100.0, 80.0, 100.0, 100.0, 1.0, 0.0, 0.03, True
        )
        assert got is not None
        assert got.exit_reason == "SIGNAL"
        assert _close(got.exit_price, want), got


def test_zero_stop_is_a_real_level_but_no_risk_basis() -> None:
    # Python gated risk on truthiness, so `0.0` never produced an R multiple,
    # while the exit rule (`is not None`) still honoured the level.
    got = native_positions.try_close(
        "SHORT", 0.0, None, 10.0, 0.0, 5.0, 100.0, 1.0, 0.0, 0.0, False
    )
    assert got is not None
    assert got.exit_price == 0.0 and got.exit_reason == "SL"
    assert got.r_multiple is None


def test_close_trade_names_its_reason() -> None:
    end = native_positions.close_trade("LONG", "END", 100.0, 2.0, 0.5, 105.0, 0.03, 90.0)
    signal = native_positions.close_trade("LONG", "SIGNAL", 100.0, 2.0, 0.5, 105.0, 0.03, 90.0)
    assert (end.exit_reason, signal.exit_reason) == ("END", "SIGNAL")
    assert end.pnl == signal.pnl
    assert _close(end.pnl, 9.437), end
    assert end.commission == signal.commission
    assert end.r_multiple is not None and _close(end.r_multiple, 9.437 / 20.0)


def test_zero_cost_entry_has_no_percent_pnl() -> None:
    got = native_positions.close_trade("LONG", "END", 0.0, 1.0, 0.0, 10.0, 0.0, None)
    assert got.pnl == 10.0 and got.pnl_pct == 0.0 and got.r_multiple is None


def test_unknown_vocabulary_is_rejected() -> None:
    with pytest.raises(ValueError, match="LONG or SHORT"):
        native_positions.try_close("FLAT", None, None, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, False)
    with pytest.raises(KeyError):
        native_positions.close_trade("LONG", "MAGIC", 1.0, 1.0, 0.0, 1.0, 0.0, None)


def test_signal_exit_prices_with_the_runners_slippage() -> None:
    # Only the opposite signal closes a leg, and it pays slippage against it.
    assert native_positions.signal_exit("LONG", True, 100.0, 0.02) is None
    assert native_positions.signal_exit("SHORT", False, 100.0, 0.02) is None
    long_exit = native_positions.signal_exit("LONG", False, 100.0, 1.0)
    short_exit = native_positions.signal_exit("SHORT", True, 100.0, 1.0)
    assert long_exit is not None and _close(long_exit, 99.0)
    assert short_exit is not None and _close(short_exit, 101.0)
    with pytest.raises(ValueError, match="LONG or SHORT"):
        native_positions.signal_exit("FLAT", True, 100.0, 0.02)


def test_end_of_run_close_mirrors_the_leg() -> None:
    assert _close(native_positions.exit_price("LONG", 100.0, 0.02), 99.98)
    assert _close(native_positions.exit_price("SHORT", 100.0, 0.02), 100.02)
    with pytest.raises(ValueError, match="LONG or SHORT"):
        native_positions.exit_price("FLAT", 100.0, 0.02)


def _ref_fill(
    signal_side: str,
    bar_close: float,
    available_equity: float,
    slippage_pct: float,
    commission_pct: float,
) -> tuple[str, float, float, float] | None:
    """Verbatim pre-migration Python entry rule, kept here as the oracle."""
    slippage_pct = max(0.0, slippage_pct)
    commission_pct = max(0.0, commission_pct)
    if available_equity <= 0.0 or bar_close <= 0.0:
        return None
    slip = bar_close * (slippage_pct / 100.0)
    fill_price = bar_close + slip if signal_side == "LONG" else bar_close - slip
    if fill_price <= 0.0:
        return None
    quantity = available_equity / fill_price
    if quantity <= 0.0:
        return None
    commission = fill_price * quantity * (commission_pct / 100.0)
    return signal_side, fill_price, quantity, commission


def test_long_entry_slips_upward_short_entry_slips_downward() -> None:
    long = native_positions.fill("LONG", 100.0, 10_000.0, 0.02, 0.03)
    short = native_positions.fill("SHORT", 100.0, 10_000.0, 0.02, 0.03)
    assert long is not None and _close(long.fill_price, 100.02)
    assert short is not None and _close(short.fill_price, 99.98)
    assert long.side == "LONG" and short.side == "SHORT"
    # Fractional sizing: the whole stake buys at the filled price.
    assert _close(long.quantity, 10_000.0 / 100.02)
    assert _close(long.commission, long.fill_price * long.quantity * 0.0003)


def test_entry_rejections_are_the_kernels_own() -> None:
    assert native_positions.fill("LONG", 0.0, 10_000.0, 0.02, 0.03) is None
    assert native_positions.fill("LONG", 100.0, 0.0, 0.02, 0.03) is None
    assert native_positions.fill("LONG", -100.0, 10_000.0, 0.02, 0.03) is None
    # A 100%+ short slippage would price the fill at or below zero.
    assert native_positions.fill("SHORT", 100.0, 10_000.0, 100.0, 0.03) is None


def test_negative_costs_clamp_to_zero_not_to_a_better_fill() -> None:
    got = native_positions.fill("LONG", 100.0, 10_000.0, -5.0, -5.0)
    assert got is not None
    assert _close(got.fill_price, 100.0) and got.commission == 0.0


def test_any_non_long_label_is_priced_as_the_short_leg() -> None:
    # The runner only ever asks for LONG/SHORT, but the direction rule is
    # "exactly LONG buys, anything else sells" — mirrored, not re-decided.
    got = native_positions.fill("SELL", 100.0, 10_000.0, 0.02, 0.03)
    assert got is not None and got.side == "SHORT"
    assert _close(got.fill_price, 99.98)


def test_fill_fuzz() -> None:
    rng = random.Random(20260921)
    filled = 0
    for _ in range(400):
        side = rng.choice(("LONG", "SHORT", "SELL"))
        bar_close = rng.choice([0.0, -1.0]) if rng.random() < 0.15 else rng.uniform(0.5, 900.0)
        equity = rng.choice([0.0, -25.0]) if rng.random() < 0.15 else rng.uniform(1.0, 500_000.0)
        slippage = (
            rng.choice([-3.0, 0.0, rng.uniform(0.0, 120.0)])
            if rng.random() < 0.3
            else rng.uniform(0.0, 2.0)
        )
        commission = (
            rng.choice([-1.0, 0.0, rng.uniform(0.0, 0.5)])
            if rng.random() < 0.3
            else rng.uniform(0.0, 0.1)
        )
        want = _ref_fill(side, bar_close, equity, slippage, commission)
        got = native_positions.fill(side, bar_close, equity, slippage, commission)
        if want is None:
            assert got is None, (side, bar_close, equity, slippage, commission)
            continue
        assert got is not None, (side, bar_close, equity, slippage, commission)
        assert got.side == ("LONG" if side == "LONG" else "SHORT"), got
        assert _close(got.fill_price, want[1]), got
        assert _close(got.quantity, want[2]), got
        assert _close(got.commission, want[3]), got
        filled += 1
    assert filled > 100, "fuzz must exercise priced entries"


def test_exit_fuzz() -> None:
    rng = random.Random(20260920)
    exits = 0
    for _ in range(400):
        side = rng.choice(("LONG", "SHORT"))
        levels = [None, 0.0, -5.0, rng.uniform(50.0, 150.0), rng.uniform(50.0, 150.0)]
        sl, tp = rng.choice(levels), rng.choice(levels)
        low = rng.uniform(40.0, 120.0)
        high = low + rng.uniform(0.0, 40.0)
        close_ = rng.uniform(low, high)
        entry = rng.choice([100.0, rng.uniform(50.0, 150.0)])
        quantity = rng.choice([1.0, rng.uniform(0.5, 50.0)])
        commission_entry = rng.uniform(0.0, 1.0)
        commission_pct = rng.choice([0.0, 0.03, rng.uniform(0.0, 0.1)])
        signal = rng.random() < 0.3
        want = _ref_exit(side, sl, tp, high, low, close_, signal)
        got = native_positions.try_close(
            side,
            sl,
            tp,
            high,
            low,
            close_,
            entry,
            quantity,
            commission_entry,
            commission_pct,
            signal,
        )
        if want is None:
            assert got is None, (side, sl, tp, high, low, close_, signal)
            continue
        assert got is not None, (side, sl, tp, high, low, close_, signal)
        _assert_same_trade(got, side, entry, quantity, commission_entry, commission_pct, sl, want)
        exits += 1
    assert exits > 100, "fuzz must exercise recorded closes"


def test_close_economics_fuzz() -> None:
    rng = random.Random(31337)
    for _ in range(300):
        side = rng.choice(("LONG", "SHORT"))
        reason = rng.choice(("SIGNAL", "SL", "TP", "END"))
        entry = rng.uniform(1.0, 500.0)
        quantity = rng.uniform(0.01, 100.0)
        exit_price = rng.uniform(1.0, 500.0)
        commission_entry = rng.uniform(0.0, 2.0)
        commission_pct = rng.uniform(0.0, 0.2)
        sl = rng.choice([None, 0.0, entry * rng.uniform(0.5, 1.5)])
        want_commission, want_pnl, want_pct, want_r = _ref_economics(
            side, entry, quantity, commission_entry, exit_price, commission_pct, sl
        )
        got = native_positions.close_trade(
            side, reason, entry, quantity, commission_entry, exit_price, commission_pct, sl
        )
        assert got.exit_reason == reason
        assert _close(got.commission, want_commission), got
        assert _close(got.pnl, want_pnl), got
        assert _close(got.pnl_pct, want_pct), got
        if want_r is None:
            assert got.r_multiple is None, got
        else:
            assert got.r_multiple is not None and _close(got.r_multiple, want_r), got
