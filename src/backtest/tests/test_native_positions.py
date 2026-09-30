"""Contract tests for backtest.native_positions (fill, close, stops, slippage)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from backtest import native_positions as np  # noqa: E402


def test_fill_long_successful() -> None:
    res = np.fill(
        signal_side="LONG",
        bar_close=100.0,
        available_equity=10000.0,
        slippage_pct=0.001,
        commission_pct=0.0003,
    )
    assert res is not None
    assert res.side == "LONG"
    assert res.fill_price >= 100.0
    assert res.quantity > 0.0
    assert res.commission > 0.0


def test_fill_short_successful() -> None:
    res = np.fill(
        signal_side="SHORT",
        bar_close=200.0,
        available_equity=10000.0,
        slippage_pct=0.001,
        commission_pct=0.0003,
    )
    assert res is not None
    assert res.side == "SHORT"
    assert res.fill_price <= 200.0
    assert res.quantity > 0.0


def test_signal_exit_slippage() -> None:
    # LONG exit -> selling -> price slips down
    long_exit = np.signal_exit(side="LONG", is_buy=False, bar_close=100.0, slippage_pct=0.01)
    assert long_exit is not None
    assert long_exit < 100.0


def test_close_trade_pnl_math() -> None:
    trade = np.close_trade(
        side="LONG",
        exit_reason="SIGNAL",
        entry_price=100.0,
        quantity=10.0,
        commission_entry=0.3,
        exit_price=110.0,
        commission_pct=0.0003,
        sl_price=95.0,
    )
    assert trade.exit_reason == "SIGNAL"
    assert trade.pnl == pytest.approx(99.6967, rel=1e-3)
    assert trade.r_multiple is not None
    assert trade.r_multiple > 1.9
