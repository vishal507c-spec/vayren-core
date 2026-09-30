"""Contract tests for backtest.native_validation (form checks)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from backtest import native_validation as nv  # noqa: E402
from strategy import BacktestForm  # noqa: E402


def test_validate_mask_valid_form() -> None:
    form = BacktestForm(
        strategy_id="strat-1",
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-10",
        initial_capital=10000.0,
        slippage_pct=0.001,
        commission_pct=0.0003,
        max_position_size=5000.0,
    )
    mask = nv.validate_mask_for(form, symbol="AAPL", enabled_ids={"strat-1"})
    assert mask == 0


def test_validate_mask_missing_symbol() -> None:
    form = BacktestForm(
        strategy_id="strat-1",
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-10",
        initial_capital=10000.0,
        slippage_pct=0.001,
        commission_pct=0.0003,
    )
    mask = nv.validate_mask_for(form, symbol="", enabled_ids={"strat-1"})
    assert (mask & nv.BIT_SYMBOL) != 0


def test_check_mask_reversed_dates() -> None:
    # Test pack_env and check_mask directly with reversed dates
    env = {
        "symbol_ok": 1,
        "strategy_ok": 1,
        "timeframe_ok": 1,
        "dates_ordered": 0,  # Reversed dates
        "initial_capital": 10000.0,
        "has_cap": 0,
        "max_position_size": 0.0,
    }
    mask = nv.check_mask(env)
    assert (mask & nv.BIT_DATES) != 0
