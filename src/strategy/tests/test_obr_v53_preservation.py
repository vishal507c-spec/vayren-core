"""Regression tests: OBR C1C4 must preserve VAYREN OS v5.3.0 exactly.

Reference: attached ``vayren (2).py`` (VAYREN OS v5.3.0).
Covers the snapshot/day-reset bug (C1 capture), SELL-only execution,
break-candle-low immediate entry (no extra confirmation candle),
MetaLabel PASS/FAIL gating, ref_high invalidation, and original constants.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import pytest

from market import Bar
from strategy.language.compiler import compile_strategy
from strategy.models.parameters import StrategyParameters
from strategy.models.state import StrategyState
from strategy.obr_c1c4_engine import Strategy as ObrC1C4Strategy
from strategy.obr_source import OBR_C1C4_SOURCE
from strategy.registry import OBR_C1C4_SPECS, OBR_C1C4_SYMBOLS
from strategy.runtime import BarView


def _params() -> StrategyParameters:
    return StrategyParameters({s.key: s.default for s in OBR_C1C4_SPECS})


def _logic():
    return compile_strategy(OBR_C1C4_SOURCE).create_logic(_params(), owner_id="test")


def _bar(ts: str, o: float, h: float, lo: float, c: float) -> Bar:
    return Bar(
        symbol="NSE:TEST",
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=1000,
        timestamp=ts,
        bar_size="30m",
    )


def _feed(logic, bars: list[Bar]):
    window: list[Bar] = []
    signals = []
    for b in bars:
        window.append(b)
        view = BarView(
            bars=tuple(window),
            index=len(window) - 1,
            params=_params(),
            state=StrategyState(),
        )
        sig = logic.on_bar(view)
        if sig is not None:
            signals.append((b.timestamp, sig))
    return signals


def _day_bars(c1_high=101.0, c1_low=99.0, break_low=98.5, break_high=100.6):
    return [
        _bar("2026-11-20 15:15:00", 100.0, 100.5, 99.5, 100.0),
        _bar("2026-11-23 09:15:00", 100.0, c1_high, c1_low, 100.0),
        _bar("2026-11-23 09:45:00", 100.0, 100.5, 99.5, 100.0),
        _bar("2026-11-23 10:15:00", 100.0, 100.8, 99.2, 100.0),
        _bar(
            "2026-11-23 10:45:00",
            100.0,
            break_high,
            break_low,
            min(max(99.0, break_low), break_high),
        ),
        _bar("2026-11-23 11:15:00", 99.0, 100.0, 98.0, 99.5),
    ]


def test_original_constants_preserved():
    cfg = compile_strategy(OBR_C1C4_SOURCE).strategy_class(_params()).cfg
    assert cfg.refIndex == 3
    assert cfg.c1ThresholdPct == pytest.approx(0.35)
    assert cfg.c4ThresholdPct == pytest.approx(0.45)
    assert cfg.exitHour == 15 and cfg.exitMinute == 15
    assert cfg.entryMode == "SELL"
    assert OBR_C1C4_SYMBOLS[0] == "NSE:KAYNES"
    assert len(OBR_C1C4_SYMBOLS) == 22


def test_c1_captured_even_when_day_preseeded_by_snapshot():
    """The old snapshot path pre-seeded prev_date_key before the first
    candle, skipping C1 capture and forcing MetaLabel FAIL all day."""
    logic = _logic()
    eng = cast(ObrC1C4Strategy, logic).engine
    eng.prev_date_key = date(2026, 11, 23)
    eng.candleCount = 0
    eng.c1High = None
    eng.c1Low = None
    bars = _day_bars()
    signals = _feed(logic, bars)
    assert eng.c1High == pytest.approx(101.0)
    assert eng.c1Low == pytest.approx(99.0)
    assert eng.metaLabelPass is True
    assert any(ts.startswith("2026-11-23 10:45") for ts, _ in signals)


def test_sell_only_break_candle_low_entry_no_confirmation_candle():
    logic = _logic()
    signals = _feed(logic, _day_bars())
    today = [(ts, s) for ts, s in signals if ts.startswith("2026-11-23")]
    assert len(today) == 1
    ts, sig = today[0]
    assert ts == "2026-11-23 10:45:00"  # break candle itself, not 11:15
    assert str(sig.kind).endswith("SELL")
    assert sig.price == pytest.approx(98.5)  # break candle low = SLM trigger
    assert sig.stop_loss == pytest.approx(100.8)  # reference high


def test_metalabel_fail_blocks_signal():
    logic = _logic()
    signals = _feed(logic, _day_bars(c1_high=100.1, c1_low=99.9))
    assert cast(ObrC1C4Strategy, logic).engine.metaLabelPass is False
    assert not any(ts.startswith("2026-11-23") for ts, _ in signals)


def test_up_touch_blocks_day_no_long_entry():
    logic = _logic()
    signals = _feed(logic, _day_bars(break_low=99.5, break_high=101.5))
    assert not any(ts.startswith("2026-11-23") for ts, _ in signals)
    assert cast(ObrC1C4Strategy, logic).engine.dayDone is True
    assert all(not str(s.kind).endswith("BUY") for _, s in signals)


def test_ref_high_after_entry_triggers_sl_exit_not_new_entry():
    """Once the short is open, a ref_high re-touch is the SL exit (original
    StopManagementEngine SL=ref_high), never a second entry signal."""
    logic = _logic()
    bars = _day_bars()
    bars.append(_bar("2026-11-23 11:45:00", 99.5, 100.9, 99.0, 100.0))
    signals = _feed(logic, bars)
    eng = cast(ObrC1C4Strategy, logic).engine
    assert eng.dayDone is True
    assert len(eng.trades) >= 1  # SL exit recorded by the engine
    # exactly one SELL entry candidate (the break candle); no re-entry
    assert (
        sum(1 for ts, s in signals if ts.startswith("2026-11-23") and str(s.kind).endswith("SELL"))
        == 1
    )
