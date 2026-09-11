"""OBR Pine 1:1 parity audit — engine + thin Strategy Lab adapter.

Canonical implementation under test: the Strategy Lab OBR record at
``D:\\VAYREN_STRATEGIES\\OBR.py`` (a JSON record whose ``code`` field holds
``StrategyConfig`` / ``Bar`` / ``TradeRecord`` / ``OBREngine`` /
``Strategy(PythonStrategy)``). That record is the single source of truth;
this file never duplicates its logic, it only drives it with identical
OHLCV sequences and asserts Pine-specified behaviour:

- candle counting / ref-candle selection (refIndex=3 -> third candle)
- strict break comparisons (``low < refLow``, ``high > refHigh``)
- same-candle double break -> NONE / invalidated, no priority tie-break
- break -> confirmation -> lock -> approval -> same-bar entry ordering
- direction lock (no opposite signals afterwards)
- LONG SL = REF LOW / SHORT SL = REF HIGH, same-bar, exact trigger ops
- 15:15 time exit at bar OPEN, NO-TRADE day, end-of-bar day reset
- IST session days, slippage / commission / PnL formulas
- trade records, analytics (equity base 100000.0), rolling days
- thin adapter: entry pulse -> BUY/SELL, exit pulse -> close, warmup 0,
  no Lab-side stop, no duplicate OBR logic
- no future-bar access (prefix-invariance)

The original Pine Script file itself (desktop ``OBR.txt``) is empty, so the
Pine semantics asserted here are the task specification's verbatim Pine
rules (operators, order, formulas). Every scenario day is a full session:
a prior-day 15:15 bar opens the stream so the engine's day counter starts
at 0 exactly like Pine's ``ta.change(time("D"))`` on a continuing chart
(see ``test_dataset_open_counts_from_one_pine_verbatim`` for the
dataset-open edge, which is preserved verbatim, not redesigned).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

IST = ZoneInfo("Asia/Kolkata")
# Resolve through the shared library resolver (env var → data_dir → per-user)
# instead of a machine-specific drive letter; missing record → module skip.
OBR_RECORD_PATH = strategy_dir() / "OBR.py"


def _load_obr() -> dict[str, Any]:
    if not OBR_RECORD_PATH.exists():
        pytest.skip(
            f"canonical OBR record missing: {OBR_RECORD_PATH}",
            allow_module_level=True,
        )
    record = json.loads(OBR_RECORD_PATH.read_text(encoding="utf-8"))
    namespace: dict[str, Any] = {}
    exec(record["code"], namespace)
    return namespace


_NS = _load_obr()

StrategyConfig = _NS["StrategyConfig"]
OBREngine = _NS["OBREngine"]
EBar = _NS["Bar"]
SIGNAL_NONE = _NS["SIGNAL_NONE"]
SIGNAL_BUY = _NS["SIGNAL_BUY"]
SIGNAL_SELL = _NS["SIGNAL_SELL"]
STATE_IDLE = _NS["STATE_IDLE"]
STATE_LONG = _NS["STATE_LONG"]
STATE_SHORT = _NS["STATE_SHORT"]
EXIT_SL = _NS["EXIT_SL"]
EXIT_TIME = _NS["EXIT_TIME"]
TRADE_EVENT_NONE = _NS["TRADE_EVENT_NONE"]
DAYTYPE_BUY_WIN = _NS["DAYTYPE_BUY_WIN"]
DAYTYPE_BUY_LOSS = _NS["DAYTYPE_BUY_LOSS"]
DAYTYPE_SELL_WIN = _NS["DAYTYPE_SELL_WIN"]
DAYTYPE_SELL_LOSS = _NS["DAYTYPE_SELL_LOSS"]
DAYTYPE_NO_TRADE = _NS["DAYTYPE_NO_TRADE"]
WAIT_FIRST_BREAK = _NS["SESSION_STATE_WAIT_FIRST_BREAK"]
DIRECTION_LOCKED = _NS["SESSION_STATE_DIRECTION_LOCKED"]
IN_POSITION = _NS["SESSION_STATE_IN_POSITION"]
DAY_COMPLETE = _NS["SESSION_STATE_DAY_COMPLETE"]


def _bar(
    index: int,
    day: int,
    slot: int,
    o: float,
    h: float,
    lo: float,
    c: float,
) -> Any:
    """One 15m session bar; slot 0 = 09:15, slot 24 = 15:15."""
    hour = 9 + (15 + slot * 15) // 60
    minute = (15 + slot * 15) % 60
    return EBar(
        bar_index=index,
        ts=datetime(2026, 1, day, hour, minute, tzinfo=IST),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=1000.0,
    )


def _session_open(day: int) -> Any:
    """Prior-session 15:00 bar so the tested day counts candles from 0.

    Slot 23 (15:00), deliberately NOT 15:15: an exit-time bar would fire the
    Pine time-exit / no-trade branch on the lead bar itself.
    """
    return _bar(-1, day - 1, 23, 100, 100.5, 99.5, 100)


def _run(cfg: Any, bars: list[Any]) -> Any:
    eng = OBREngine(cfg)
    for bar in bars:
        eng.process_bar(bar)
    return eng


def _cfg(**over: float) -> Any:
    params: dict[str, Any] = {
        "refIndex": 3,
        "buySlippagePct": 0.0,
        "sellSlippagePct": 0.0,
        "slSlippagePct": 0.0,
        "commissionPct": 0.0,
    }
    params.update(over)
    return StrategyConfig(**params)


def _buy_day(start: int, day: int) -> list[Any]:
    """Canonical BUY session: ref bar slot2 H103/L100, break slot3, confirm slot4."""
    return [
        _bar(start, day, 0, 100, 101, 99, 100),
        _bar(start + 1, day, 1, 100, 102, 99.5, 101),
        _bar(start + 2, day, 2, 101, 103, 100, 102),
        _bar(start + 3, day, 3, 102, 104, 101, 103),
        _bar(start + 4, day, 4, 103, 105, 102, 104),
    ]


def _sell_day(start: int, day: int) -> list[Any]:
    """Canonical SELL session: ref bar slot2 H103/L100, low-break slot3, confirm slot4."""
    return [
        _bar(start, day, 0, 102, 103, 101, 102),
        _bar(start + 1, day, 1, 102, 103, 100.5, 101),
        _bar(start + 2, day, 2, 101, 103, 100, 100.5),
        _bar(start + 3, day, 3, 100.5, 102, 99, 99.5),
        _bar(start + 4, day, 4, 99.5, 101, 98, 98.5),
    ]


def _flat_tail(start: int, day: int, first_slot: int, price: float = 102.0) -> list[Any]:
    return [
        _bar(start + i, day, first_slot + i, price, price + 0.5, price - 0.5, price)
        for i in range(25 - first_slot)
    ]


# ── reference candle ────────────────────────────────────────────────


def test_ref_candle_is_third_candle() -> None:
    eng = _run(_cfg(), [_session_open(5)] + _buy_day(0, 5))
    assert eng.refHigh == 103.0
    assert eng.refLow == 100.0
    assert eng.refBar == 2
    assert eng.refSet is True


def test_dataset_open_counts_from_one_pine_verbatim() -> None:
    """Dataset-open edge, preserved verbatim (NOT redesigned).

    On the very first streamed bar Pine's ``ta.change(time("D"))`` is ``na``
    (falsy), so the new-day branch does not run and the counter starts at
    ``nz(na) + 1 = 1``; the ref then lands on the second bar. Every later
    full session counts 0,1,2 and refs the third candle. The engine mirrors
    this exactly (``prev_date_key is None`` -> not-new-day -> ``+= 1``).
    """
    eng = _run(_cfg(), _buy_day(0, 5))  # NO lead bar: dataset opens here
    assert eng.refHigh == 102.0
    assert eng.refBar == 1


def test_touching_ref_is_not_a_break() -> None:
    """Strict Pine comparisons: high == refHigh / low == refLow never break."""
    bars = [_session_open(6)]
    bars += [
        _bar(0, 6, 0, 100, 101, 99, 100),
        _bar(1, 6, 1, 100, 102, 99.5, 101),
        _bar(2, 6, 2, 101, 103, 100, 102),
        _bar(3, 6, 3, 102, 103, 100, 102),  # touches both, breaks neither
    ]
    eng = _run(_cfg(), bars)
    assert eng.firstBreakDone is False
    assert eng.ccBuySet is False
    assert eng.ccSellSet is False


# ── break detection / confirmation ──────────────────────────────────


def test_first_high_break_registers_buy_side() -> None:
    eng = _run(_cfg(), [_session_open(5)] + _buy_day(0, 5)[:4])
    assert eng.firstBreakDone is True
    assert eng.ccBuySet is True
    assert eng.ccBuyHigh == 104.0
    assert eng.ccBuyBar == 3
    assert eng.ccSellSet is False


def test_first_low_break_registers_sell_side() -> None:
    eng = _run(_cfg(), [_session_open(5)] + _sell_day(0, 5)[:4])
    assert eng.firstBreakDone is True
    assert eng.ccSellSet is True
    assert eng.ccSellLow == 99.0
    assert eng.ccSellBar == 3
    assert eng.ccBuySet is False


def test_break_bar_itself_never_confirms() -> None:
    """Confirmation needs bar_index > break bar: single-step breakout never enters."""
    eng = _run(_cfg(), [_session_open(5)] + _buy_day(0, 5)[:4])
    assert eng.tradeState == STATE_IDLE
    assert eng.entryEventNow is False


def test_confirmation_on_later_candle_enters() -> None:
    bars = [_session_open(5)] + _buy_day(0, 5)[:4]
    bars.append(_bar(4, 5, 5, 103, 104.5, 102.5, 103.5))  # slot5 still > 104
    eng = _run(_cfg(), bars)
    assert eng.tradeState == STATE_LONG
    assert eng.entryBarIndex == 4


def test_no_confirmation_without_push() -> None:
    bars = [_session_open(5)] + _buy_day(0, 5)[:4]
    bars.append(_bar(4, 5, 4, 103, 103.9, 102, 103))  # high 103.9 < 104: no confirm
    eng = _run(_cfg(), bars)
    assert eng.tradeState == STATE_IDLE


# ── same-candle double break ────────────────────────────────────────


def test_same_candle_double_break_invalidates_with_no_priority() -> None:
    bars = [_session_open(7)]
    bars += [
        _bar(0, 7, 0, 100, 101, 99, 100),
        _bar(1, 7, 1, 100, 102, 99.5, 101),
        _bar(2, 7, 2, 101, 103, 100, 102),
        _bar(3, 7, 3, 102, 108, 95, 103),  # breaks both sides
    ]
    eng = _run(_cfg(), bars)
    assert eng.ccInvalidated is True
    assert eng.firstBreakDone is True
    assert eng.ccBuySet is False
    assert eng.ccSellSet is False
    assert eng.directionLocked is False
    assert eng.lockedDirection == SIGNAL_NONE


def test_double_break_day_never_trades() -> None:
    bars = [_session_open(7)]
    bars += [
        _bar(0, 7, 0, 100, 101, 99, 100),
        _bar(1, 7, 1, 100, 102, 99.5, 101),
        _bar(2, 7, 2, 101, 103, 100, 102),
        _bar(3, 7, 3, 102, 108, 95, 103),
    ]
    bars += _flat_tail(4, 7, 4)
    eng = _run(_cfg(), bars)
    assert eng.tradeState == STATE_IDLE
    assert eng.dayDone is True
    assert len(eng.trades) == 1
    assert eng.trades[0].day_type == DAYTYPE_NO_TRADE


# ── invalidation ────────────────────────────────────────────────────


def test_sell_side_invalidated_by_ref_high_breach() -> None:
    bars = [_session_open(8)] + _sell_day(0, 8)[:4]
    # high 103.5 >= refHigh 103 without touching entry 99 -> invalidate
    bars.append(_bar(4, 8, 4, 100, 103.5, 99.5, 101))
    eng = _run(_cfg(), bars)
    assert eng.ccInvalidated is True
    assert eng.ccSellSet is False
    assert eng.tradeState == STATE_IDLE


def test_sell_side_survives_ambiguous_candle() -> None:
    """high >= refHigh TOGETHER with low < ccSellLow is ambiguity, not invalidation.

    The guard keeps the sell setup alive so the bar still confirms the SHORT;
    that same bar's high (103.5) then necessarily touches the SHORT SL
    (REF HIGH = 103), so Pine records a 0-hold SELL LOSS — entry happened,
    unlike the invalidated case where no entry ever fires.
    """
    bars = [_session_open(8)] + _sell_day(0, 8)[:4]
    bars.append(_bar(4, 8, 4, 100, 103.5, 98.5, 99))  # also pushes below 99 -> confirms
    eng = _run(_cfg(), bars)
    assert eng.ccInvalidated is False
    assert eng.lockedDirection == SIGNAL_SELL
    assert eng.entryEventNow is True
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_SL
    rec = eng.trades[-1]
    assert rec.day_type == DAYTYPE_SELL_LOSS
    assert rec.holding_bars == 0


def test_buy_side_invalidated_by_ref_low_breach() -> None:
    bars = [_session_open(8)] + _buy_day(0, 8)[:4]
    # low 99.5 <= refLow 100 without pushing above entry 104 -> invalidate
    bars.append(_bar(4, 8, 4, 102, 103.5, 99.5, 101))
    eng = _run(_cfg(), bars)
    assert eng.ccInvalidated is True
    assert eng.ccBuySet is False


# ── signal resolution / direction lock ──────────────────────────────


def test_resolver_has_no_tie_break() -> None:
    assert OBREngine._resolve_signal(True, False) == SIGNAL_BUY
    assert OBREngine._resolve_signal(False, True) == SIGNAL_SELL
    assert OBREngine._resolve_signal(False, False) == SIGNAL_NONE
    assert OBREngine._resolve_signal(True, True) == SIGNAL_NONE


def test_buy_direction_lock_and_session_state() -> None:
    eng = _run(_cfg(), [_session_open(9)] + _buy_day(0, 9))
    assert eng.directionLocked is True
    assert eng.lockedDirection == SIGNAL_BUY
    assert eng.sessionState == IN_POSITION
    assert eng.tradeState == STATE_LONG
    assert eng.executedSignal == SIGNAL_BUY
    assert DIRECTION_LOCKED == 1  # contract pins the code


def test_sell_direction_lock() -> None:
    eng = _run(_cfg(), [_session_open(9)] + _sell_day(0, 9))
    assert eng.directionLocked is True
    assert eng.lockedDirection == SIGNAL_SELL
    assert eng.tradeState == STATE_SHORT


def test_opposite_signal_blocked_after_lock() -> None:
    """After a BUY lock, a later SELL-shaped push can never enter SHORT."""
    bars = [_session_open(10)] + _buy_day(0, 10)[:5]
    # collapse far below ref low on a later bar: LONG SL fires, never SHORT
    bars.append(_bar(5, 10, 5, 103, 104, 95, 96))
    bars.append(_bar(6, 10, 6, 96, 97, 90, 91))
    eng = _run(_cfg(), bars)
    assert eng.lockedDirection == SIGNAL_BUY
    assert eng.tradeState != STATE_SHORT
    assert all(t.direction != STATE_SHORT for t in eng.trades if t.entry_price is not None)


# ── entry execution ─────────────────────────────────────────────────


def test_buy_entry_same_bar_at_breakout_plus_slippage() -> None:
    eng = _run(_cfg(buySlippagePct=0.5), [_session_open(11)] + _buy_day(0, 11))
    assert eng.entryEventNow is True
    assert eng.entryEventDir == SIGNAL_BUY
    assert eng.entryEventPrice == pytest.approx(104.0 * 1.005)
    assert eng.entryPrice == pytest.approx(104.0 * 1.005)
    assert eng.entryBarIndex == 4
    assert eng.slPrice == 100.0


def test_sell_entry_same_bar_at_breakout_minus_slippage() -> None:
    eng = _run(_cfg(sellSlippagePct=0.25), [_session_open(11)] + _sell_day(0, 11))
    assert eng.entryEventNow is True
    assert eng.entryEventDir == SIGNAL_SELL
    assert eng.entryPrice == pytest.approx(99.0 * (1 - 0.0025))
    assert eng.slPrice == 103.0


# ── stop loss ───────────────────────────────────────────────────────


def test_long_sl_trigger_price_bar_and_record() -> None:
    bars = [_session_open(12)] + _buy_day(0, 12)[:5]
    bars.append(_bar(5, 12, 5, 103, 104, 99.0, 100.0))  # low 99 <= SL 100
    eng = _run(_cfg(), bars)
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_SL
    assert eng.exitEventVisPrice == 100.0
    assert eng.exitEventPnl == pytest.approx(-4.0)
    assert eng.tradeState == STATE_IDLE
    assert eng.dayDone is True
    assert eng.sessionState == DAY_COMPLETE
    rec = eng.trades[-1]
    assert rec.day_type == DAYTYPE_BUY_LOSS
    assert rec.day_points == pytest.approx(-4.0)
    assert rec.entry_price == pytest.approx(104.0)
    assert rec.exit_price == pytest.approx(100.0)
    assert rec.direction == STATE_LONG
    assert rec.exit_reason == EXIT_SL
    assert rec.holding_bars == 1
    assert rec.exit_bar == 5


def test_long_sl_touch_exactly_at_level_fires() -> None:
    """Inclusive Pine trigger: bar.low <= slPrice fires even on exact touch."""
    bars = [_session_open(12)] + _buy_day(0, 12)[:5]
    bars.append(_bar(5, 12, 5, 103, 104, 100.0, 101.0))
    eng = _run(_cfg(), bars)
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_SL


def test_short_sl_trigger_and_record() -> None:
    bars = [_session_open(12)] + _sell_day(0, 12)[:5]
    bars.append(_bar(5, 12, 5, 99, 103.5, 98, 100.0))  # high 103.5 >= SL 103
    eng = _run(_cfg(), bars)
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_SL
    rec = eng.trades[-1]
    assert rec.day_type == DAYTYPE_SELL_LOSS
    assert rec.day_points == pytest.approx(-4.0)
    assert rec.exit_price == pytest.approx(103.0)


def test_sl_slippage_and_commission_formula() -> None:
    bars = [_session_open(13)] + _buy_day(0, 13)[:5]
    bars.append(_bar(5, 13, 5, 103, 104, 99.0, 100.0))
    eng = _run(_cfg(slSlippagePct=0.5, commissionPct=0.1), bars)
    adj = 100.0 * (1 - 0.005)
    expected_loss = abs(104.0 - adj) + 104.0 * 0.001
    assert eng.exitEventPnl == pytest.approx(-expected_loss)
    assert eng.trades[-1].exit_price == pytest.approx(adj)


def test_same_bar_entry_and_sl() -> None:
    """Confirmation bar that also spans down to SL: Pine enters AND exits same bar."""
    bars = [_session_open(14)] + _buy_day(0, 14)[:4]
    bars.append(_bar(4, 14, 4, 103, 106, 99.0, 100.0))  # confirms 104, hits SL 100
    eng = _run(_cfg(), bars)
    assert eng.entryEventNow is True
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_SL
    rec = eng.trades[-1]
    assert rec.holding_bars == 0
    assert rec.exit_bar == 4
    assert rec.entry_time == datetime(2026, 1, 14, 10, 15, tzinfo=IST)
    assert rec.exit_time == datetime(2026, 1, 14, 10, 15, tzinfo=IST)


# ── time exit / no-trade ────────────────────────────────────────────


def test_time_exit_uses_bar_open_at_1515() -> None:
    bars = [_session_open(15)] + _buy_day(0, 15)[:5]
    bars += _flat_tail(5, 15, 5, price=106.0)[:-1]
    bars.append(_bar(24, 15, 24, 107.0, 108.0, 106.0, 107.5))  # 15:15 bar
    eng = _run(_cfg(), bars)
    assert eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_TIME
    assert eng.exitEventVisPrice == 107.0
    assert eng.exitEventPnl == pytest.approx(107.0 - 104.0)
    rec = eng.trades[-1]
    assert rec.day_type == DAYTYPE_BUY_WIN
    assert rec.entry_price == pytest.approx(104.0)
    assert rec.exit_price == pytest.approx(107.0)
    assert rec.holding_bars == 20


def test_short_time_exit_loss_classification() -> None:
    bars = [_session_open(15)] + _sell_day(0, 15)[:5]
    bars += _flat_tail(5, 15, 5, price=97.0)[:-1]
    bars.append(_bar(24, 15, 24, 100.0, 101.0, 99.0, 99.5))  # open 100 > entry 99
    eng = _run(_cfg(), bars)
    rec = eng.trades[-1]
    assert rec.exit_reason == EXIT_TIME
    assert rec.day_type == DAYTYPE_SELL_LOSS
    assert rec.day_points == pytest.approx(99.0 - 100.0)


def test_no_trade_day_record() -> None:
    bars = [_session_open(16)]
    bars += [
        _bar(0, 16, 0, 100, 101, 99, 100),
        _bar(1, 16, 1, 100, 102, 99.5, 101),
        _bar(2, 16, 2, 101, 103, 100, 102),
    ]
    bars += _flat_tail(3, 16, 3)
    eng = _run(_cfg(), bars)
    assert eng.tradeState == STATE_IDLE
    assert eng.dayDone is True
    assert eng.sessionState == DAY_COMPLETE
    assert eng.ntEventNow is True
    rec = eng.trades[-1]
    assert rec.day_type == DAYTYPE_NO_TRADE
    assert rec.day_points == 0.0
    assert rec.entry_price is None
    assert rec.exit_price is None
    assert rec.holding_bars == 0


# ── day reset / multi-day ───────────────────────────────────────────


def test_new_day_reset_clears_session_state() -> None:
    bars = [_session_open(17)] + _buy_day(0, 17)[:5]
    bars.append(_bar(5, 18, 0, 100, 101, 99, 100))  # next day first bar
    eng = _run(_cfg(), bars)
    assert eng.tradeState == STATE_IDLE
    assert eng.dayDone is False
    assert eng.directionLocked is False
    assert eng.lockedDirection == SIGNAL_NONE
    assert eng.sessionState == WAIT_FIRST_BREAK
    assert eng.firstBreakDone is False
    assert eng.ccInvalidated is False
    assert eng.ccBuySet is False
    assert eng.ccSellSet is False
    assert eng.executedSignal == SIGNAL_NONE


def test_next_day_trades_independently() -> None:
    bars = [_session_open(19)] + _buy_day(0, 19)[:5]
    bars.append(_bar(5, 19, 5, 103, 104, 99.0, 100.0))  # day1 SL
    day2 = [
        _bar(6 + i, 20, i, o, h, lo, c)
        for i, (o, h, lo, c) in enumerate(
            [
                (100, 101, 99, 100),
                (100, 102, 99.5, 101),
                (101, 103, 100, 100.5),
                (100.5, 102, 99, 99.5),
                (99.5, 101, 98, 98.5),
            ]
        )
    ]
    bars += day2
    eng = _run(_cfg(), bars)
    assert eng.trades[0].day_type == DAYTYPE_BUY_LOSS
    assert eng.tradeState == STATE_SHORT
    assert eng.lockedDirection == SIGNAL_SELL


def test_multiple_days_accumulate_trade_records() -> None:
    bars = [_session_open(21)] + _buy_day(0, 21)[:5]
    bars.append(_bar(5, 21, 5, 103, 104, 99.0, 100.0))  # day1: BUY LOSS
    bars += _sell_day(6, 22)[:5]
    bars += _flat_tail(11, 22, 5, price=97.0)[:-1]
    bars.append(_bar(30, 22, 24, 96.0, 97.0, 95.0, 95.5))  # day2: SELL WIN 99->96
    eng = _run(_cfg(), bars)
    assert len(eng.trades) == 2
    assert eng.trades[0].day_type == DAYTYPE_BUY_LOSS
    assert eng.trades[1].day_type == DAYTYPE_SELL_WIN
    assert eng.trades[1].day_points == pytest.approx(3.0)
    assert eng.totalTradeEvents == 2
    assert eng.totalLongEvents == 1
    assert eng.totalShortEvents == 1
    assert eng.totalWinEvents == 1
    assert eng.totalLossEvents == 1


# ── analytics ───────────────────────────────────────────────────────


def test_analytics_equity_drawdown_streaks() -> None:
    bars = [_session_open(23)] + _buy_day(0, 23)[:5]
    bars.append(_bar(5, 23, 5, 103, 104, 99.0, 100.0))  # -4
    bars += _sell_day(6, 24)[:5]
    bars.append(_bar(11, 24, 5, 99, 103.5, 98, 100.0))  # -4
    eng = _run(_cfg(), bars)
    assert eng.runningEquity == pytest.approx(100000.0 - 8.0)
    assert eng.peakEquity == pytest.approx(100000.0)
    assert eng.currentDrawdown == pytest.approx(8.0)
    assert eng.maximumDrawdown == pytest.approx(8.0)
    assert eng.consecutiveLossEvents == 2
    assert eng.maximumConsecutiveLossEvents == 2


def test_win_resets_loss_streak() -> None:
    bars = [_session_open(25)] + _buy_day(0, 25)[:5]
    bars.append(_bar(5, 25, 5, 103, 104, 99.0, 100.0))  # loss
    bars += _sell_day(6, 26)[:5]
    bars += _flat_tail(11, 26, 5, price=97.0)[:-1]
    bars.append(_bar(30, 26, 24, 96.0, 97.0, 95.0, 95.5))  # win
    eng = _run(_cfg(), bars)
    assert eng.consecutiveLossEvents == 0
    assert eng.maximumConsecutiveLossEvents == 1
    assert eng.runningEquity == pytest.approx(100000.0 - 4.0 + 3.0)


# ── rolling market days ─────────────────────────────────────────────


def test_rolling_days_trim_storage_by_session_date() -> None:
    bars: list[Any] = [_session_open(27)]
    idx = 0
    for day in (27, 28, 29, 30, 31):
        bars += [
            _bar(idx, day, 0, 100, 101, 99, 100),
            _bar(idx + 1, day, 1, 100, 102, 99.5, 101),
            _bar(idx + 2, day, 2, 101, 103, 100, 102),
        ]
        bars += _flat_tail(idx + 3, day, 3)
        idx += 25
    eng = _run(_cfg(maxDays=3), bars)
    assert len(eng.sharedMarketDays) == 3
    assert len(eng.trades) == 3
    assert all(t.day_type == DAYTYPE_NO_TRADE for t in eng.trades)


# ── no look-ahead ───────────────────────────────────────────────────


def test_prefix_invariance_no_future_bar_access() -> None:
    """Events for day 1 must be identical whether or not day 2 is fed later."""
    day1 = [_session_open(5)] + _buy_day(0, 5)[:5]
    day1.append(_bar(5, 5, 5, 103, 104, 99.0, 100.0))
    day1 += _flat_tail(6, 5, 6)
    day2 = _sell_day(31, 6)[:5] + _flat_tail(36, 6, 5, price=97.0)
    eng_prefix = _run(_cfg(), list(day1))
    eng_full = _run(_cfg(), list(day1) + list(day2))
    snap_prefix = [(t.trade_no, t.day_type, t.day_points) for t in eng_prefix.trades]
    snap_full = [(t.trade_no, t.day_type, t.day_points) for t in eng_full.trades[:1]]
    assert snap_prefix == snap_full
    assert eng_prefix.runningEquity == pytest.approx(100000.0 - 4.0)
    assert eng_full.trades[0].day_points == pytest.approx(-4.0)


def test_pulses_fire_once_per_bar() -> None:
    eng = OBREngine(_cfg())
    seen_entry = []
    for bar in [_session_open(5)] + _buy_day(0, 5):
        eng.process_bar(bar)
        if eng.entryEventNow:
            seen_entry.append(bar.bar_index)
    assert seen_entry == [4]


# ── thin Lab adapter ────────────────────────────────────────────────


def _lab_bars(
    day: int, rows: list[tuple[int, float, float, float, float]], lead: bool = True
) -> Any:
    from market.models.bar import Bar

    out = []
    if lead:
        out.append(
            Bar(
                symbol="OBRTEST",
                open=100,
                high=100.5,
                low=99.5,
                close=100,
                volume=1000,
                timestamp=f"2026-01-{day - 1:02d} 15:00:00",
            )
        )
    for s, o, h, lo, c in rows:
        out.append(
            Bar(
                symbol="OBRTEST",
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=1000,
                timestamp=f"2026-01-{day:02d} {9 + (15 + s * 15) // 60:02d}:"
                f"{(15 + s * 15) % 60:02d}:00",
            )
        )
    return tuple(out)


def _drive_adapter(logic: Any, bars: Any) -> list[Any]:
    from strategy.models.parameters import StrategyParameters
    from strategy.models.state import StrategyState
    from strategy.runtime import BarView

    out: list[Any] = []
    state = StrategyState()
    params = StrategyParameters(dict(logic.params))
    for i, _b in enumerate(bars):
        view = BarView(bars=bars, index=i, params=params, state=state)
        sig = logic.on_bar(view)
        out.append(sig)
        if sig is not None:
            from strategy.models.signal import SignalKind

            if state.flat and sig.kind == SignalKind.BUY:
                state = StrategyState.open("LONG", sig.price, i)
            elif state.flat and sig.kind == SignalKind.SELL:
                state = StrategyState.open("SHORT", sig.price, i)
            elif not state.flat:
                is_long = state.side == "LONG"
                if (is_long and sig.kind == SignalKind.SELL) or (
                    not is_long and sig.kind == SignalKind.BUY
                ):
                    state = StrategyState()
    return out


def _adapter_logic(**params: float) -> Any:
    from strategy.language import compile_strategy
    from strategy.models.parameters import StrategyParameters

    record = json.loads(OBR_RECORD_PATH.read_text(encoding="utf-8"))
    compiled = compile_strategy(record["code"])
    merged = dict(compiled.param_defaults)
    merged.update(params)
    return compiled.create_logic(StrategyParameters(merged))


def test_adapter_warmup_is_zero() -> None:
    assert _adapter_logic().warmup() == 0


def test_adapter_exposes_six_parameters() -> None:
    from strategy.language import compile_strategy

    record = json.loads(OBR_RECORD_PATH.read_text(encoding="utf-8"))
    compiled = compile_strategy(record["code"])
    by_key = {spec.key: spec for spec in compiled.param_specs}
    assert set(by_key) == {
        "refIndex",
        "buySlippagePct",
        "sellSlippagePct",
        "slSlippagePct",
        "commissionPct",
        "extendBars",
    }
    ref = by_key["refIndex"]
    assert (ref.default, ref.minimum, ref.maximum) == (3, 1, 10)
    ext = by_key["extendBars"]
    assert ext.default == 5
    assert ext.minimum == 1
    assert ext.maximum >= 5


def test_adapter_buy_and_lab_close_on_sl() -> None:
    from strategy.models.signal import SignalKind

    bars = _lab_bars(
        5,
        [
            (0, 100, 101, 99, 100),
            (1, 100, 102, 99.5, 101),
            (2, 101, 103, 100, 102),
            (3, 102, 104, 101, 103),
            (4, 103, 105, 102, 104),
            (5, 103, 104, 99.0, 100.0),
        ],
    )
    sigs = _drive_adapter(_adapter_logic(), bars)
    assert sigs[5] is not None and sigs[5].kind == SignalKind.BUY
    assert sigs[5].index == 5
    assert sigs[5].stop_loss is None  # engine owns SL; adapter arms no Lab stop
    assert sigs[5].take_profit is None
    assert sigs[6] is not None and sigs[6].kind == SignalKind.SELL  # close LONG


def test_adapter_no_trade_day_emits_nothing() -> None:
    bars = _lab_bars(
        6,
        [
            (0, 100, 101, 99, 100),
            (1, 100, 102, 99.5, 101),
            (2, 101, 103, 100, 102),
            (3, 102, 102.5, 100.5, 101),
            (4, 101, 102, 100.5, 101),
        ],
    )
    sigs = _drive_adapter(_adapter_logic(), bars)
    assert all(s is None for s in sigs)


def test_adapter_same_bar_entry_and_exit_emits_nothing() -> None:
    """Pine end-state for a 0-hold round trip is flat/dayDone; with one signal
    per host bar the mirror must stay flat too (engine record keeps the trade)."""
    bars = _lab_bars(
        7,
        [
            (0, 100, 101, 99, 100),
            (1, 100, 102, 99.5, 101),
            (2, 101, 103, 100, 102),
            (3, 102, 104, 101, 103),
            (4, 103, 106, 99.0, 100.0),
        ],
    )
    sigs = _drive_adapter(_adapter_logic(), bars)
    assert sigs[5] is None


def _plots_by_id(logic: Any) -> dict[str, Any]:
    """Universal PlotEvents from the adapter keyed by plot_id (one per event)."""
    return {event.plot_id: event for event in logic.get_plot_events()}


def test_adapter_ref_levels_plotted() -> None:
    from strategy import PlotType

    bars = _lab_bars(
        5,
        [
            (0, 100, 101, 99, 100),
            (1, 100, 102, 99.5, 101),
            (2, 101, 103, 100, 102),
            (3, 102, 104, 101, 103),
            (4, 103, 105, 102, 104),
        ],
    )
    logic = _adapter_logic()
    _drive_adapter(logic, bars)
    plots = _plots_by_id(logic)
    assert plots["ref_high"].plot_type == PlotType.RAY
    assert (plots["ref_high"].start_bar, plots["ref_high"].start_price) == (3, 103.0)
    assert (plots["ref_low"].start_bar, plots["ref_low"].start_price) == (3, 100.0)
    assert plots["ref_high"].extend_bars == 5
    assert plots["ref_high"].text is None
    assert plots["ref_high"].marker_type is None
    assert plots["ref_high"].source_strategy != ""


# ── TradingView-like visuals: universal strategy-owned PlotEvents ──
#
# The adapter emits engine pulses/state as generic PlotEvents (READ-ONLY, no
# OBR logic recomputed): markers are compact glyphs with no text, REF rays
# extend exactly extendBars candles, the SL level spans entry to exit. One
# engine event yields exactly one PlotEvent — no legacy duplicate labels.


def _buy_visual_rows() -> list[tuple[int, float, float, float, float]]:
    return [
        (0, 100, 101, 99, 100),
        (1, 100, 102, 99.5, 101),
        (2, 101, 103, 100, 102),
        (3, 102, 104, 101, 103),
        (4, 103, 105, 102, 104),
        (5, 103, 104, 99.0, 100.0),
    ]


def test_visual_buy_marker_uses_engine_price_and_bar() -> None:
    from strategy import MarkerType, PlotType

    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    plots = _plots_by_id(logic)
    buy = plots["buy_5"]
    assert buy.plot_type == PlotType.MARKER
    assert buy.marker_type == MarkerType.UP_ARROW
    assert (buy.bar_index, buy.price) == (5, 104.0)
    # Owner-supplied compact label (reference style), exact owner text.
    assert buy.text == "BUY 104.00"
    assert "sell_5" not in plots


def test_visual_sell_marker_uses_engine_price_and_bar() -> None:
    logic = _adapter_logic()
    _drive_adapter(
        logic,
        _lab_bars(
            5,
            [
                (0, 102, 103, 101, 102),
                (1, 102, 103, 100.5, 101),
                (2, 101, 103, 100, 100.5),
                (3, 100.5, 102, 99, 99.5),
                (4, 99.5, 101, 98, 98.5),
                (5, 99, 100, 98, 99),
            ],
        ),
    )
    from strategy import MarkerType, PlotType

    plots = _plots_by_id(logic)
    sell = plots["sell_5"]
    assert sell.plot_type == PlotType.MARKER
    assert sell.marker_type == MarkerType.DOWN_ARROW
    assert (sell.bar_index, sell.price) == (5, 99.0)
    assert sell.text == "SELL 99.00"
    assert "buy_5" not in plots


def test_sl_exit_has_no_visual_but_trading_intact() -> None:
    """SL is invisible (no marker/line/level/pill) while SL trading stays live."""
    from strategy import PlotType

    logic = _adapter_logic()
    sigs = _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    # Trading intact: BUY then close, engine SL exit recorded with PnL.
    assert sigs[5] is not None and sigs[6] is not None
    eng = logic.engine
    assert eng.exitEventReason == EXIT_SL
    assert eng.trades[-1].exit_reason == EXIT_SL
    assert eng.trades[-1].day_points == -4.0
    # Visuals: BUY only — zero SL representation of any kind.
    plots = _plots_by_id(logic)
    assert (plots["buy_5"].bar_index, plots["buy_5"].price) == (5, 104.0)
    assert not [pid for pid in plots if "sl" in pid.lower()]
    assert not [e for e in logic.get_plot_events() if e.plot_type == PlotType.HORIZONTAL_LEVEL]
    assert not [e for e in logic.get_plot_events() if e.text is not None and "sl" in e.text.lower()]
    assert logic.get_chart_series() == {}
    # The SL exit bar is muted so no execution pill appears there either.
    assert logic.get_muted_signal_bars() == (6,)


def test_visual_eod_uses_bar_open_and_engine_pnl() -> None:
    """TIME exit -> EOD blue arrow with owner-supplied price + signed PnL."""
    rows = [
        (0, 100, 101, 99, 100),
        (1, 100, 102, 99.5, 101),
        (2, 101, 103, 100, 102),
        (3, 102, 104, 101, 103),
        (4, 103, 105, 102, 104),
    ]
    rows += [(s, 106.0, 106.5, 105.5, 106.0) for s in range(5, 24)]
    rows.append((24, 107.0, 108.0, 106.0, 107.5))
    from strategy import MarkerType, PlotType

    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(5, rows))
    plots = _plots_by_id(logic)
    eod = plots["eod_25"]
    assert eod.plot_type == PlotType.MARKER
    assert eod.marker_type == MarkerType.TRIANGLE_BLUE
    assert (eod.bar_index, eod.price) == (25, 107.0)
    assert eod.text == "EOD 107.00 +3.00"
    assert "exit_time_25" not in plots and "exit_time_pnl_25" not in plots
    # SL has no visual; the TIME exit bar is a real exit, never muted.
    assert not [pid for pid in plots if "sl" in pid.lower()]
    assert logic.get_muted_signal_bars() == ()


def test_visual_no_trade_marker_and_no_entry_markers() -> None:
    from strategy import MarkerType, PlotType

    rows = [(0, 100, 101, 99, 100), (1, 100, 102, 99.5, 101), (2, 101, 103, 100, 102)]
    rows += [(s, 102.0, 102.5, 101.5, 102.0) for s in range(3, 25)]
    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(6, rows))
    plots = _plots_by_id(logic)
    assert "buy_5" not in plots and "sell_5" not in plots and "sl_5" not in plots
    nt = plots["no_trade_25"]
    assert nt.plot_type == PlotType.MARKER
    assert nt.marker_type == MarkerType.CIRCLE_X
    assert (nt.bar_index, nt.price) == (25, pytest.approx(101.5 * (1 - 0.0025)))
    assert nt.text is None
    assert logic.get_chart_series() == {}


def test_visual_no_trade_is_circle_x_without_text() -> None:
    """ntEventNow -> MARKER + CIRCLE_X + text None at the exact event bar/price."""
    from strategy import MarkerType, PlotType

    rows = [(0, 100, 101, 99, 100), (1, 100, 102, 99.5, 101), (2, 101, 103, 100, 102)]
    rows += [(s, 102.0, 102.5, 101.5, 102.0) for s in range(3, 25)]
    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(6, rows))
    eng = logic.engine
    # Source of truth unchanged: engine event + visual price + event bar.
    assert eng.ntEventNow is True
    assert eng.ntEventVisPrice == pytest.approx(101.5 * (1 - 0.0025))
    plots = _plots_by_id(logic)
    nt = plots["no_trade_25"]
    assert nt.plot_type == PlotType.MARKER
    assert nt.marker_type == MarkerType.CIRCLE_X
    assert (nt.bar_index, nt.price) == (25, pytest.approx(eng.ntEventVisPrice))
    assert nt.text is None  # glyph-only: no text pill
    # Negatives: no legacy square/label, no duplicate, no shifted coordinates.
    assert nt.marker_type != MarkerType.SQUARE
    assert "buy_5" not in plots and "sell_5" not in plots
    assert len([p for p in plots if p.startswith("no_trade_")]) == 1


def test_visual_sell_then_eod_exit() -> None:
    """SELL entry held to 15:15: SELL marker + EOD, no SL visual."""
    from strategy import MarkerType, PlotType

    rows = [
        (0, 102, 103, 101, 102),
        (1, 102, 103, 100.5, 101),
        (2, 101, 103, 100, 100.5),
        (3, 100.5, 102, 99, 99.5),
        (4, 99.5, 101, 98, 98.5),
    ]
    rows += [(s, 97.0, 97.5, 96.5, 97.0) for s in range(5, 24)]
    rows.append((24, 100.0, 101.0, 99.0, 99.5))
    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(5, rows))
    eng = logic.engine
    assert eng.exitEventReason == EXIT_TIME
    plots = _plots_by_id(logic)
    sell = plots["sell_5"]
    assert sell.marker_type == MarkerType.DOWN_ARROW
    assert (sell.bar_index, sell.price) == (5, 99.0)
    eod = plots["eod_25"]
    assert eod.plot_type == PlotType.MARKER
    assert eod.marker_type == MarkerType.TRIANGLE_BLUE
    assert (eod.bar_index, eod.price) == (25, pytest.approx(eng.exitEventVisPrice))
    assert eod.text == f"EOD {eng.exitEventVisPrice:.2f} {eng.exitEventPnl:+.2f}"
    assert not [pid for pid in plots if "sl" in pid.lower()]
    assert logic.get_muted_signal_bars() == ()


def test_visual_same_bar_sl_round_trip_shows_only_buy() -> None:
    """Same-bar entry+SL exit: BUY marker only; SL invisible; bar muted."""
    logic = _adapter_logic()
    sigs = _drive_adapter(
        logic,
        _lab_bars(
            7,
            [
                (0, 100, 101, 99, 100),
                (1, 100, 102, 99.5, 101),
                (2, 101, 103, 100, 102),
                (3, 102, 104, 101, 103),
                (4, 103, 106, 99.0, 100.0),
            ],
        ),
    )
    assert sigs[5] is None
    assert logic.engine.exitEventReason == EXIT_SL
    plots = _plots_by_id(logic)
    assert (plots["buy_5"].bar_index, plots["buy_5"].price) == (5, 104.0)
    assert plots["buy_5"].text == "BUY 104.00"
    assert "exit_sl_5" not in plots
    assert logic.get_muted_signal_bars() == (5,)


def test_visual_same_bar_time_round_trip_shows_buy_and_time() -> None:
    """Same-bar entry+TIME exit: BUY + TIME diamond, never EOD on entry."""
    from strategy import MarkerType

    rows = [
        (0, 100, 101, 99, 100),
        (1, 100, 102, 99.5, 101),
        (2, 101, 103, 100, 102),
        (3, 102, 104, 101, 103),
    ]
    rows += [(s, 102.0, 103.5, 101.5, 102.0) for s in range(4, 24)]
    rows.append((24, 103.0, 106.0, 102.0, 104.0))
    logic = _adapter_logic()
    sigs = _drive_adapter(logic, _lab_bars(7, rows))
    assert all(s is None for s in sigs)
    eng = logic.engine
    assert eng.entryEventNow is True and eng.exitEventNow is True
    assert eng.exitEventReason == EXIT_TIME
    plots = _plots_by_id(logic)
    assert (plots["buy_25"].bar_index, plots["buy_25"].price) == (25, 104.0)
    time_plot = plots["time_25"]
    assert time_plot.marker_type == MarkerType.DIAMOND
    assert (time_plot.bar_index, time_plot.price) == (25, pytest.approx(eng.exitEventVisPrice))
    assert time_plot.text is None
    assert "eod_25" not in plots
    assert logic.get_muted_signal_bars() == ()


def test_visual_days_stay_separate() -> None:
    day1 = _lab_bars(5, _buy_visual_rows())
    day2_rows = [(0, 100, 101, 99, 100), (1, 100, 102, 99.5, 101), (2, 101, 103, 100, 102)]
    day2_rows += [(s, 102.0, 102.5, 101.5, 102.0) for s in range(3, 25)]
    day2 = _lab_bars(6, day2_rows, lead=False)
    logic = _adapter_logic()
    _drive_adapter(logic, day1 + day2)
    events = logic.get_plot_events()
    # REF rays anchor on each day's own reference candle (lead bar shifts +1).
    ref_starts = sorted(e.start_bar for e in events if e.plot_id == "ref_high")
    assert ref_starts == [3, 9]
    assert all(e.start_price == 103.0 for e in events if e.plot_id == "ref_high")
    # Day-1 SL exit is muted (invisible); nothing leaks into day 2.
    assert logic.get_muted_signal_bars() == (6,)
    assert not [e for e in events if "sl" in e.plot_id.lower()]
    plots = _plots_by_id(logic)
    assert "buy_5" in plots and "sell_5" not in plots


def test_visual_entry_text_follows_owner_label_flag() -> None:
    """Entry text is owner-supplied only: priced label iff showEntryLabel."""
    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    assert _plots_by_id(logic)["buy_5"].text == "BUY 104.00"
    logic_plain = _adapter_logic()
    logic_plain.cfg.showEntryLabel = False
    logic_plain.engine.cfg.showEntryLabel = False
    _drive_adapter(logic_plain, _lab_bars(5, _buy_visual_rows()))
    assert _plots_by_id(logic_plain)["buy_5"].text is None


def test_runner_forwards_obr_marker_styles() -> None:
    """End-to-end: runner forwards universal OBR PlotEvents, no legacy series."""
    import sqlite3
    import tempfile

    from backtest.models.config import BacktestConfig
    from backtest.runner import BacktestRunner
    from market.repository.symbol_repository import SymbolRepository

    from strategy.language.storage import create_strategy

    def _rows(day: str, spec: list[tuple[float, float, float, float]]) -> list[tuple]:
        out = []
        for i, (o, h, lo, c) in enumerate(spec):
            hh = 9 + (15 + i * 15) // 60
            mm = (15 + i * 15) % 60
            out.append((f"{day} {hh:02d}:{mm:02d}:00", o, h, lo, c, 1000))
        return out

    flat = [(102.0, 102.5, 101.5, 102.0)] * 25
    setup = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101),
        (101, 103, 100, 102),
        (102, 104, 101, 103),
        (103, 105, 102, 104),
        (103, 104, 99.0, 100.0),
    ]
    setup += [(101.0, 101.5, 100.5, 101.0)] * 19
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = sqlite3.connect(tmp / "TEST.db")
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER)"
        )
        for row in _rows("2026-01-05", flat) + _rows("2026-01-06", setup):
            conn.execute("INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)", row)
        conn.commit()
        conn.close()
        code = json.loads(OBR_RECORD_PATH.read_text(encoding="utf-8"))["code"]
        rec = create_strategy("OBR-Visual-Probe", code, data_dir=tmp)
        runner = BacktestRunner(SymbolRepository(tmp), None, tmp)
        result = runner.run(
            BacktestConfig(
                symbol="TEST",
                timeframe="15m",
                start_date="2026-01-05",
                end_date="2026-01-06",
            ),
            (rec.id,),
        )
    from strategy import MarkerType, PlotType

    assert not result.has_error, result.error_detail
    assert result.results[0].chart_series == ()
    by_plot = {p.plot_id: p for p in result.results[0].chart_plots}
    buy = by_plot["buy_29"]
    assert buy.plot_type == PlotType.MARKER and buy.marker_type == MarkerType.UP_ARROW
    assert (buy.bar_index, buy.price) == (29, 104.0) and buy.text == "BUY 104.00"
    # SL has zero visual representation; the SL exit bar is muted instead.
    assert not [pid for pid in by_plot if "sl" in pid.lower()]
    assert result.results[0].muted_bars == (30,)
    assert len(result.results[0].trades) == 1
    ref_high = by_plot["ref_high"]
    assert ref_high.plot_type == PlotType.RAY
    assert (ref_high.start_bar, ref_high.start_price) == (27, 103.0)
    assert ref_high.extend_bars == 5
    assert by_plot["ref_low"].extend_bars == 5


def test_runner_result_shows_one_visual_per_obr_event() -> None:
    """One OBR event -> one canonical visual: TradeOverlay skips covered ends."""
    import sqlite3
    import tempfile

    from backtest.models.config import BacktestConfig
    from backtest.runner import BacktestRunner
    from backtest.ui.overlay import TradeOverlay, _bar_x, _price_y
    from market.models.bar import Bar
    from market.repository.symbol_repository import SymbolRepository
    from PySide6.QtCore import QRect, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from strategy.language.storage import create_strategy

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])

    def _rows(day: str, spec: list[tuple[float, float, float, float]]) -> list[tuple]:
        out = []
        for i, (o, h, lo, c) in enumerate(spec):
            hh = 9 + (15 + i * 15) // 60
            mm = (15 + i * 15) % 60
            out.append((f"{day} {hh:02d}:{mm:02d}:00", o, h, lo, c, 1000))
        return out

    flat = [(102.0, 102.5, 101.5, 102.0)] * 25
    setup = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101),
        (101, 103, 100, 102),
        (102, 104, 101, 103),
        (103, 105, 102, 104),
        (103, 104, 99.0, 100.0),
    ]
    setup += [(101.0, 101.5, 100.5, 101.0)] * 19
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conn = sqlite3.connect(tmp / "TEST.db")
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER)"
        )
        for row in _rows("2026-01-05", flat) + _rows("2026-01-06", setup):
            conn.execute("INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)", row)
        conn.commit()
        conn.close()
        code = json.loads(OBR_RECORD_PATH.read_text(encoding="utf-8"))["code"]
        rec = create_strategy("OBR-Visual-Probe", code, data_dir=tmp)
        runner = BacktestRunner(SymbolRepository(tmp), None, tmp)
        result = runner.run(
            BacktestConfig(
                symbol="TEST",
                timeframe="15m",
                start_date="2026-01-05",
                end_date="2026-01-06",
            ),
            (rec.id,),
        )
    assert not result.has_error, result.error_detail
    first = result.results[0]
    assert len(first.trades) == 1
    trade = first.trades[0]
    assert (trade.entry_index, trade.exit_index) == (29, 30)

    overlay = TradeOverlay()
    overlay.set_result(first)
    # Entry covered by the BUY marker; the SL exit bar is muted (SL has no
    # visual); bar 24 is the day-1 NO-TRADE marker (no trade to suppress).
    assert overlay.covered_bars == frozenset({24, 29, 30})
    assert first.muted_bars == (30,)

    bars = tuple(
        Bar(
            symbol="TEST",
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000,
            timestamp=f"2026-01-06 10:{i:02d}:00",
        )
        for i in range(50)
    )
    from chart.models.chart_viewport import ChartViewport
    from chart.renderer.plot_renderer import PlotOverlay

    viewport = ChartViewport(
        bars=bars,
        first=20,
        last=40,
        price_low=95.0,
        price_high=110.0,
        volume_max=1000,
        chart_rect=QRect(0, 0, 600, 300),
        volume_rect=QRect(0, 300, 600, 50),
        axis_rect=QRect(0, 350, 600, 20),
    )
    plots = PlotOverlay()
    plots.ingest_plot_events(first.chart_plots)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    plots.paint_overlay(painter, viewport)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    # Exactly ONE compact pill cluster: the owner-supplied BUY label (white
    # label text exists only inside pills). Covered execution pills stay
    # silent; glyph-only markers and lines add no white pixels.
    seen: set[tuple[int, int]] = set()
    clusters = 0
    for x in range(0, 600, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) != (0xFF, 0xFF, 0xFF):
                continue
            key = (x // 2, y // 2)
            if any(
                (key[0] + dx, key[1] + dy) in seen for dx in range(-3, 4) for dy in range(-3, 4)
            ):
                seen.add(key)
                continue
            clusters += 1
            seen.add(key)
    assert clusters == 1, f"one event must yield one pill cluster, got {clusters}"
    # Holding-span connection (different information) still paints.
    rect = QRectF(viewport.chart_rect)
    mx = (_bar_x(29, 20, 40, rect) + _bar_x(30, 20, 40, rect)) / 2.0
    my = (
        _price_y(trade.entry_price, 95.0, 110.0, rect)
        + _price_y(trade.exit_price, 95.0, 110.0, rect)
    ) / 2.0
    assert any(
        image.pixelColor(int(mx) + dx, int(my) + dy) != QColor("#101418")
        for dx in range(-2, 3)
        for dy in range(-2, 3)
    )


def test_marker_style_parsing_and_label_format() -> None:
    from chart.renderer.plot_renderer import _format_marker_label, _parse_marker_style

    assert _parse_marker_style({}) == (None, "")
    assert _parse_marker_style({"style": "line"}) == (None, "")
    assert _parse_marker_style({"style": "nope|x"}) == (None, "")
    assert _parse_marker_style({"style": "marker_up|BUY {v:.2f}"}) == (
        "marker_up",
        "BUY {v:.2f}",
    )
    assert _parse_marker_style({"style": "marker_square"}) == ("marker_square", "{t} {v:.2f}")
    assert _format_marker_label("BUY {v:.2f}", "BUY", 104.0) == "BUY 104.00"
    assert _format_marker_label("{v:+.2f}", "X", -4.0) == "-4.00"
    assert _format_marker_label("NO TRADE", "NO TRADE", 1.0) == "NO TRADE"


def test_marker_series_paints_glyph_and_pill() -> None:
    from chart.models.chart_viewport import ChartViewport
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    bars = _lab_bars(5, _buy_visual_rows())
    rect = QRect(0, 0, 400, 300)
    viewport = ChartViewport(
        bars=bars,
        first=0,
        last=len(bars),
        price_low=98.0,
        price_high=106.0,
        volume_max=1000,
        chart_rect=rect,
        volume_rect=QRect(0, 300, 400, 50),
        axis_rect=QRect(0, 350, 400, 20),
    )
    overlay = PlotOverlay()
    overlay.set_series_legacy(
        {"BUY": {5: 104.0}, "REF HIGH": {3: 103.0}},
        {"BUY": {"style": "marker_up|BUY {v:.2f}"}, "REF HIGH": {"style": "line"}},
    )
    image = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    green = pill_text = 0
    for x in range(0, 400, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) == (0x26, 0xA6, 0x9A):
                green += 1
            elif (color.red(), color.green(), color.blue()) == (0xFF, 0xFF, 0xFF):
                pill_text += 1
    # Solid-fill pill + tip-anchored triangle share the marker color family.
    assert green > 40, "solid label pill must paint theme-green pixels"
    assert pill_text > 0, "label text must paint white pixels"


def test_line_series_path_unchanged_for_default_style() -> None:
    from chart.models.chart_viewport import ChartViewport
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    bars = _lab_bars(5, _buy_visual_rows())
    rect = QRect(0, 0, 400, 300)
    viewport = ChartViewport(
        bars=bars,
        first=0,
        last=len(bars),
        price_low=98.0,
        price_high=106.0,
        volume_max=1000,
        chart_rect=rect,
        volume_rect=QRect(0, 300, 400, 50),
        axis_rect=QRect(0, 350, 400, 20),
    )
    overlay = PlotOverlay()
    overlay.set_series_legacy({"LVL": {1: 100.0, 2: 100.0, 3: 100.0}}, None)
    image = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    drawn = 0
    for x in range(0, 400, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) != (0x10, 0x14, 0x18):
                drawn += 1
    assert drawn > 10, "default line path must still paint level pixels"


# ── visual cleanup: short levels + de-collided labels ──


def _paint_viewport(bars: Any, first: int, last: int, lo: float, hi: float) -> Any:
    from chart.models.chart_viewport import ChartViewport
    from PySide6.QtCore import QRect

    return ChartViewport(
        bars=bars,
        first=first,
        last=last,
        price_low=lo,
        price_high=hi,
        volume_max=1000,
        chart_rect=QRect(0, 0, 600, 300),
        volume_rect=QRect(0, 300, 600, 50),
        axis_rect=QRect(0, 350, 600, 20),
    )


def _qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    return app


def _flat_lab_bars(day: int, count: int, price: float = 100.0) -> Any:
    from market.models.bar import Bar

    out = []
    for i in range(count):
        hh = 9 + (15 + i * 15) // 60
        mm = (15 + i * 15) % 60
        out.append(
            Bar(
                symbol="OBRTEST",
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1000,
                timestamp=f"2026-01-{day:02d} {hh:02d}:{mm:02d}:00",
            )
        )
    return tuple(out)


def _painted_x_span(image: Any, y: int, background: tuple[int, int, int]) -> tuple[int, int]:
    lo_x, hi_x = 10**9, -(10**9)
    for x in range(0, image.width(), 2):
        for dy in range(-3, 4):
            color = image.pixelColor(x, y + dy)
            if (color.red(), color.green(), color.blue()) != background:
                lo_x = min(lo_x, x)
                hi_x = max(hi_x, x)
    return lo_x, hi_x


def test_visual_level_extend_meta_is_short() -> None:
    from strategy import PlotType

    logic = _adapter_logic()
    _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    plots = _plots_by_id(logic)
    assert plots["ref_high"].plot_type == PlotType.RAY
    assert plots["ref_high"].extend_bars == 5
    assert plots["ref_low"].extend_bars == 5
    # SL has no visual at all — no level, no ray, no marker.
    assert not [e for e in logic.get_plot_events() if e.plot_type == PlotType.HORIZONTAL_LEVEL]


def test_parse_extend_contract() -> None:
    from chart.renderer.plot_renderer import _parse_extend

    assert _parse_extend({}) == (False, None)
    assert _parse_extend({"extend": "none"}) == (False, None)
    assert _parse_extend({"extend": "session"}) == (True, None)
    assert _parse_extend({"extend": "bars:5"}) == (True, 5)
    assert _parse_extend({"extend": "bars:1"}) == (True, 1)
    assert _parse_extend({"extend": "bars:0"}) == (False, None)
    assert _parse_extend({"extend": "bars:x"}) == (False, None)
    assert _parse_extend({"extend": "weird"}) == (False, None)


def test_capped_ray_stops_after_five_bars() -> None:
    from chart.renderer.plot_renderer import PlotOverlay, _bar_x, _price_y
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    bars = _flat_lab_bars(5, 30)
    viewport = _paint_viewport(bars, 0, 30, 98.0, 106.0)
    value = 103.0
    y = int(_price_y(value, 98.0, 106.0, QRectF(viewport.chart_rect)))
    overlay = PlotOverlay()
    overlay.set_series_legacy({"REF HIGH": {2: value}}, {"REF HIGH": {"extend": "bars:5"}})
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    lo_x, hi_x = _painted_x_span(image, y, (0x10, 0x14, 0x18))
    rect = QRectF(viewport.chart_rect)
    assert lo_x <= int(_bar_x(2, 0, 30, rect)) + 2
    assert hi_x <= int(_bar_x(7, 0, 30, rect)), "capped ray must stop near bar 6"
    assert hi_x >= int(_bar_x(5, 0, 30, rect)), "capped ray must cover ~5 candles"


def test_uncapped_session_ray_still_spans() -> None:
    from chart.renderer.plot_renderer import PlotOverlay, _bar_x, _price_y
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    bars = _flat_lab_bars(5, 30)
    viewport = _paint_viewport(bars, 0, 30, 98.0, 106.0)
    value = 103.0
    y = int(_price_y(value, 98.0, 106.0, QRectF(viewport.chart_rect)))
    overlay = PlotOverlay()
    overlay.set_series_legacy({"REF HIGH": {2: value}}, {"REF HIGH": {"extend": "session"}})
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    _, hi_x = _painted_x_span(image, y, (0x10, 0x14, 0x18))
    rect = QRectF(viewport.chart_rect)
    assert hi_x >= int(_bar_x(28, 0, 30, rect)), "legacy session rays keep full length"


def test_sl_dense_path_has_no_overhang() -> None:
    from chart.renderer.plot_renderer import PlotOverlay, _bar_x, _price_y
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    bars = _flat_lab_bars(5, 30)
    viewport = _paint_viewport(bars, 0, 30, 98.0, 106.0)
    value = 100.0
    y = int(_price_y(value, 98.0, 106.0, QRectF(viewport.chart_rect)))
    overlay = PlotOverlay()
    overlay.set_series_legacy({"SL": {5: value, 6: value}}, {"SL": {"extend": "none"}})
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    _, hi_x = _painted_x_span(image, y, (0x10, 0x14, 0x18))
    rect = QRectF(viewport.chart_rect)
    assert hi_x <= int(_bar_x(7, 0, 30, rect)), "SL line must end at its exit bar"


def test_layout_pills_do_not_overlap_and_keep_priority() -> None:
    from chart.renderer.plot_renderer import _layout_marker_pills, _MarkerJob

    def _job(x: float, y: float, title: str, priority: int, order: int) -> _MarkerJob:
        return _MarkerJob(
            x=x,
            y=y,
            width=80.0,
            height=18.0,
            below=True,
            priority=priority,
            order=order,
            kind="marker_square",
            text=title,
        )

    jobs = [
        _job(300.0, 150.0, "BUY", 0, 0),
        _job(300.0, 150.0, "EXIT SL", 1, 1),
        _job(300.0, 150.0, "NO TRADE", 2, 2),
    ]
    placed = _layout_marker_pills(jobs)
    assert len(placed) == 3
    # glyph x never moves
    assert all(job.x == 300.0 for job, _, _, _ in placed)
    # BUY (priority 0) keeps its preferred below spot
    buy = next(p for p in placed if p[0].text == "BUY")
    assert buy[1:3] == (300.0 - 40.0, 150.0 + 9.0)
    assert buy[3] is False
    # no two pills overlap
    from PySide6.QtCore import QRectF

    rects = [QRectF(px, py, job.width, job.height) for job, px, py, _ in placed]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not rects[i].intersects(rects[j])
    # deterministic: same jobs, same layout
    again = _layout_marker_pills(jobs)
    assert [(p[1], p[2]) for p in placed] == [(p[1], p[2]) for p in again]


def test_layout_single_pill_never_displaced() -> None:
    from chart.renderer.plot_renderer import _layout_marker_pills, _MarkerJob

    job = _MarkerJob(
        x=100.0,
        y=100.0,
        width=60.0,
        height=16.0,
        below=False,
        priority=0,
        order=0,
        kind="marker_up",
        text="BUY",
    )
    (got, px, py, displaced) = _layout_marker_pills([job])[0]
    assert (px, py) == (100.0 - 30.0, 100.0 - 9.0 - 16.0)
    assert displaced is False


def test_same_bar_markers_all_paint_without_overlap() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    bars = _flat_lab_bars(5, 30)
    viewport = _paint_viewport(bars, 0, 30, 90.0, 110.0)
    overlay = PlotOverlay()
    overlay.set_series_legacy(
        {
            "BUY": {10: 100.0},
            "EXIT SL": {10: 99.0},
            "EXIT SL PNL": {10: -1.0},
        },
        {
            "BUY": {"style": "marker_up|BUY {v:.2f}"},
            "EXIT SL": {"style": "marker_square|SL {v:.2f}"},
            "EXIT SL PNL": {"style": "marker_square|{v:+.2f}"},
        },
    )
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    found = set()
    for x in range(0, 600, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            rgb = (color.red(), color.green(), color.blue())
            if rgb == (0x26, 0xA6, 0x9A):
                found.add("up")
            elif rgb == (0xFF, 0xB7, 0x4D):
                found.add("square")
            elif rgb == (0xFF, 0xFF, 0xFF):
                found.add("pill-text")
    assert found == {"up", "square", "pill-text"}


def test_marker_priority_titles() -> None:
    from chart.renderer.plot_renderer import _marker_priority

    assert _marker_priority("BUY") == 0
    assert _marker_priority("SELL") == 0
    assert _marker_priority("EXIT SL") == 1
    assert _marker_priority("EXIT TIME PNL") == 1
    assert _marker_priority("NO TRADE") == 2
    assert _marker_priority("REF HIGH") == 3


# ── refIndex moves the engine setup; extendBars only the visual length ──


def _session_rows(
    ref_slot: int,
    ref_high: float = 103.0,
    ref_low: float = 100.0,
    span: float = 1.0,
) -> list[tuple[int, float, float, float, float]]:
    """One session: flat 101.5 bars, REF H/L at `ref_slot`, then a high break
    (+span) on the next bar and confirmation (+span more) after that."""
    rows = []
    for slot in range(25):
        if slot == ref_slot:
            rows.append((slot, 101.5, ref_high, ref_low, 101.5))
        elif slot == ref_slot + 1:
            rows.append((slot, 101.5, ref_high + span, ref_low + 0.5, ref_high + span - 0.2))
        elif slot == ref_slot + 2:
            rows.append(
                (slot, ref_high + span - 0.2, ref_high + 2 * span, ref_low + 0.5, ref_high + span)
            )
        else:
            rows.append((slot, 101.5, 102.0, 101.0, 101.5))
    return rows


def test_ref_index_3_engine_setup() -> None:
    rows = _session_rows(2)[:5]  # through the confirmation bar (rest of day exits at 15:15)
    eng = _run(
        _cfg(refIndex=3),
        [_session_open(5)]
        + [_bar(i, 5, s, o, h, lo, c) for i, (s, o, h, lo, c) in enumerate(rows)],
    )
    assert eng.refHigh == 103.0
    assert eng.refLow == 100.0
    assert eng.refBar == 2
    assert eng.tradeState == STATE_LONG
    assert eng.entryBarIndex == 4
    assert eng.entryPrice == pytest.approx(104.0)


def test_ref_index_4_moves_engine_setup() -> None:
    rows = _session_rows(3)[:6]
    eng = _run(
        _cfg(refIndex=4),
        [_session_open(6)]
        + [_bar(i, 6, s, o, h, lo, c) for i, (s, o, h, lo, c) in enumerate(rows)],
    )
    assert eng.refHigh == 103.0
    assert eng.refLow == 100.0
    assert eng.refBar == 3
    assert eng.tradeState == STATE_LONG
    assert eng.entryBarIndex == 5
    assert eng.entryPrice == pytest.approx(104.0)
    assert eng.slPrice == 100.0


def test_ref_index_5_moves_engine_setup() -> None:
    rows = _session_rows(4)[:7]
    eng = _run(
        _cfg(refIndex=5),
        [_session_open(7)]
        + [_bar(i, 7, s, o, h, lo, c) for i, (s, o, h, lo, c) in enumerate(rows)],
    )
    assert eng.refBar == 4
    assert eng.tradeState == STATE_LONG
    assert eng.entryBarIndex == 6


def test_ref_index_1_uses_first_candle() -> None:
    rows = _session_rows(0)[:3]
    eng = _run(
        _cfg(refIndex=1),
        [_session_open(8)]
        + [_bar(i, 8, s, o, h, lo, c) for i, (s, o, h, lo, c) in enumerate(rows)],
    )
    assert eng.refBar == 0
    assert eng.tradeState == STATE_LONG
    assert eng.entryBarIndex == 2


def test_ref_index_moves_visual_ref_origin() -> None:
    logic = _adapter_logic(refIndex=4)
    _drive_adapter(logic, _lab_bars(5, _session_rows(3)))
    plots = _plots_by_id(logic)
    # lead bar shifts engine indices by one: 4th candle == engine bar 4
    assert (plots["ref_high"].start_bar, plots["ref_high"].start_price) == (4, 103.0)
    assert (plots["ref_low"].start_bar, plots["ref_low"].start_price) == (4, 100.0)
    assert (plots["buy_6"].bar_index, plots["buy_6"].price) == (6, 104.0)


def test_ref_index_moves_between_days_independently() -> None:
    day1 = [_session_open(9)] + [
        _bar(i, 9, s, o, h, lo, c) for i, (s, o, h, lo, c) in enumerate(_session_rows(3))
    ]
    day2 = [
        _bar(25 + i, 10, s, o, h, lo, c)
        for i, (s, o, h, lo, c) in enumerate(_session_rows(3, ref_high=203.0, ref_low=200.0))
    ]
    eng = _run(_cfg(refIndex=4), day1 + day2)
    assert eng.refHigh == 203.0
    assert eng.refLow == 200.0
    assert eng.refBar == 28


def test_extend_bars_param_drives_ref_meta_only() -> None:
    logic = _adapter_logic(extendBars=3)
    _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    plots = _plots_by_id(logic)
    assert plots["ref_high"].extend_bars == 3
    assert plots["ref_low"].extend_bars == 3
    assert logic.extendBars == 3
    assert logic.engine.cfg.extendBars == 3


def test_extend_bars_default_is_five() -> None:
    logic = _adapter_logic()
    assert logic.extendBars == 5
    _drive_adapter(logic, _lab_bars(5, _buy_visual_rows()))
    plots = _plots_by_id(logic)
    assert plots["ref_high"].extend_bars == 5
    # trading behaviour identical regardless of visual length (field-wise:
    # each adapter exec() owns distinct record classes, so == is class-bound)
    from dataclasses import astuple

    logic7 = _adapter_logic(extendBars=7)
    _drive_adapter(logic7, _lab_bars(5, _buy_visual_rows()))
    assert [astuple(t) for t in logic7.engine.trades] == [astuple(t) for t in logic.engine.trades]
    assert len(logic.engine.trades) == 1
    assert logic7.engine.tradeState == logic.engine.tradeState == STATE_IDLE
    assert logic7.engine.entryPrice == logic.engine.entryPrice == pytest.approx(104.0)


def test_ref_rays_use_teal_and_red() -> None:
    from chart.renderer.plot_renderer import PlotOverlay, _price_y
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    bars = _flat_lab_bars(5, 30)
    viewport = _paint_viewport(bars, 0, 30, 98.0, 106.0)
    overlay = PlotOverlay()
    overlay.set_series_legacy(
        {"REF HIGH": {2: 103.0}, "REF LOW": {2: 100.0}},
        {"REF HIGH": {"extend": "bars:5"}, "REF LOW": {"extend": "bars:5"}},
    )
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    from PySide6.QtCore import QRectF

    rect = QRectF(viewport.chart_rect)
    y_hi = int(_price_y(103.0, 98.0, 106.0, rect))
    y_lo = int(_price_y(100.0, 98.0, 106.0, rect))
    teal = red = 0
    for x in range(0, 600, 2):
        for y in (y_hi, y_lo):
            for dy in range(-2, 3):
                color = image.pixelColor(x, y + dy)
                rgb = (color.red(), color.green(), color.blue())
                if rgb == (0x26, 0xA6, 0x9A):
                    teal += 1
                elif rgb == (0xEF, 0x53, 0x50):
                    red += 1
    assert teal > 5, "REF HIGH ray must paint teal pixels"
    assert red > 5, "REF LOW ray must paint red pixels"
