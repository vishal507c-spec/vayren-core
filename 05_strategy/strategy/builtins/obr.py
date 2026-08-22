"""OBR — Opening Breakout Range (simplified).

Preserves Pine ``PINE.txt`` behavior for reference candle, first-break
ownership, resolver fallback, SL = REF levels, EOD 15:15 IST, and daily
state machine. Range filters and strategy-level calendar filtering have
been removed per product requirement — Date Range is now only a
backtest-data selection (``BacktestConfig``).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from market.models.bar import Bar

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic

IST = ZoneInfo("Asia/Kolkata")

KIND = "obr"

PARAM_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec("rolling_market_days", "Rolling Market Days", 1000, 1, 5000, 0),
    ParameterSpec("reference_candle_index", "Reference Candle Index", 3, 1, 100, 0),
    ParameterSpec("extend_reference_bars", "Extend Reference Bars", 7, 0, 100, 0),
    ParameterSpec("exit_hour", "Exit Hour", 15, 0, 23, 0),
    ParameterSpec("exit_minute", "Exit Minute", 15, 0, 59, 0),
    ParameterSpec("buy_slippage_pct", "Buy Slippage %", 0.0, 0.0, 5.0, 2),
    ParameterSpec("sell_slippage_pct", "Sell Slippage %", 0.0, 0.0, 5.0, 2),
    ParameterSpec("sl_slippage_pct", "SL Slippage %", 0.0, 0.0, 5.0, 2),
    ParameterSpec("commission_pct", "Commission %", 0.0, 0.0, 5.0, 2),
)

SIGNAL_NONE = 0
SIGNAL_BUY = 1
SIGNAL_SELL = 2

SESSION_WAIT_FIRST_BREAK = 0
SESSION_DIRECTION_LOCKED = 1
SESSION_IN_POSITION = 3
SESSION_DAY_COMPLETE = 5


def _parse_dt(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except Exception:
        try:
            return datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=IST)
        except Exception:
            return None


class ObrLogic:
    """Pine-faithful OBR without range filters and without calendar filter."""

    def __init__(self, params: StrategyParameters) -> None:
        v = params.validated(PARAM_SPECS)
        self._ref_index = int(v["reference_candle_index"])
        self._exit_hour = int(v["exit_hour"])
        self._exit_min = int(v["exit_minute"])
        self._buy_slip = float(v["buy_slippage_pct"])
        self._sell_slip = float(v["sell_slippage_pct"])
        self._sl_slip = float(v["sl_slippage_pct"])

        self._candle_count = 0
        self._prev_ts: str | None = None
        self._last_close: float | None = None

        self._ref_high: float | None = None
        self._ref_low: float | None = None
        self._ref_set = False

        self._cc_sell_low: float | None = None
        self._cc_sell_bar: int | None = None
        self._cc_sell_set = False
        self._cc_buy_high: float | None = None
        self._cc_buy_bar: int | None = None
        self._cc_buy_set = False
        self._first_break_done = False
        self._cc_invalidated = False
        self._sell_touched_once = False
        self._buy_touched_once = False

        self._locked_dir = SIGNAL_NONE
        self._direction_locked = False
        self._session_state = SESSION_WAIT_FIRST_BREAK
        self._day_done = False

        self._sl_price: float | None = None
        self._ladder_min = (240, 60, 30, 15, 5, 1)

    def warmup(self) -> int:
        return 1

    def _is_new_day(self, cur_dt: datetime | None, prev_ts: str | None) -> bool:
        if cur_dt is None or prev_ts is None:
            return True
        prev_dt = _parse_dt(prev_ts)
        if prev_dt is None:
            return False
        return cur_dt.date() != prev_dt.date()

    def _is_exit_time(self, cur_dt: datetime | None) -> bool:
        if cur_dt is None:
            return False
        t = cur_dt.astimezone(IST).time()
        return t.hour > self._exit_hour or (
            t.hour == self._exit_hour and t.minute >= self._exit_min
        )

    def _active_rungs(self, chart_sec: int) -> list[int]:
        return [m * 60 for m in self._ladder_min if m * 60 < chart_sec]

    def _resolve_outcome(self, chart_sec: int) -> int:
        # No lower-TF data in replay → fallback ENTRY_FIRST (Pine: verdict 0 → 1)
        active = self._active_rungs(chart_sec)
        if not active:
            return 1
        return 1

    def on_bar(self, view: BarView) -> Signal | None:
        bar: Bar = view.bar
        idx = view.index
        cur_dt = _parse_dt(bar.timestamp)
        is_new_day = self._is_new_day(cur_dt, self._prev_ts)

        if is_new_day:
            self._candle_count = 1
            self._ref_high = None
            self._ref_low = None
            self._ref_set = False
            self._cc_sell_low = None
            self._cc_sell_bar = None
            self._cc_sell_set = False
            self._cc_buy_high = None
            self._cc_buy_bar = None
            self._cc_buy_set = False
            self._first_break_done = False
            self._cc_invalidated = False
            self._sell_touched_once = False
            self._buy_touched_once = False
            self._locked_dir = SIGNAL_NONE
            self._direction_locked = False
            self._session_state = SESSION_WAIT_FIRST_BREAK
            self._day_done = False
            self._sl_price = None
        else:
            self._candle_count += 1

        self._last_close = bar.close
        self._prev_ts = bar.timestamp

        if self._candle_count == self._ref_index:
            self._ref_high = bar.high
            self._ref_low = bar.low
            self._ref_set = True

        past_ref = self._ref_set and self._candle_count > self._ref_index

        chart_sec = 900
        if len(view.bars) >= 2:
            try:
                t0 = _parse_dt(view.bars[0].timestamp)
                t1 = _parse_dt(view.bars[1].timestamp)
                if t0 and t1:
                    chart_sec = max(60, int(abs((t1 - t0).total_seconds())))
            except Exception:
                pass

        if past_ref and not is_new_day and not self._first_break_done:
            low_broke = bar.low < self._ref_low if self._ref_low is not None else False
            high_broke = bar.high > self._ref_high if self._ref_high is not None else False
            if low_broke and not high_broke:
                self._cc_sell_low = bar.low
                self._cc_sell_bar = idx
                self._cc_sell_set = True
                self._first_break_done = True
            elif high_broke and not low_broke:
                self._cc_buy_high = bar.high
                self._cc_buy_bar = idx
                self._cc_buy_set = True
                self._first_break_done = True
            elif low_broke and high_broke:
                self._cc_invalidated = True
                self._first_break_done = True

        if past_ref and not is_new_day and self._first_break_done and not self._cc_invalidated:
            if (
                self._cc_sell_set
                and self._cc_sell_bar is not None
                and idx != self._cc_sell_bar
                and bar.high >= (self._ref_high or float("inf"))
                and not (self._cc_sell_low is not None and bar.low < self._cc_sell_low)
            ):
                self._cc_sell_set = False
                self._cc_invalidated = True
            if (
                self._cc_buy_set
                and self._cc_buy_bar is not None
                and idx != self._cc_buy_bar
                and bar.low <= (self._ref_low or float("-inf"))
                and not (self._cc_buy_high is not None and bar.high > self._cc_buy_high)
            ):
                self._cc_buy_set = False
                self._cc_invalidated = True

        sell_touched_now = (
            past_ref
            and not is_new_day
            and self._cc_sell_set
            and self._cc_sell_low is not None
            and self._cc_sell_bar is not None
            and idx > self._cc_sell_bar
            and not self._cc_invalidated
            and not self._sell_touched_once
            and bar.low < self._cc_sell_low
        )
        buy_touched_now = (
            past_ref
            and not is_new_day
            and self._cc_buy_set
            and self._cc_buy_high is not None
            and self._cc_buy_bar is not None
            and idx > self._cc_buy_bar
            and not self._cc_invalidated
            and not self._buy_touched_once
            and bar.high > self._cc_buy_high
        )
        sell_amb = sell_touched_now and (self._ref_high is not None and bar.high >= self._ref_high)
        buy_amb = buy_touched_now and (self._ref_low is not None and bar.low <= self._ref_low)
        if sell_touched_now:
            self._sell_touched_once = True
        if buy_touched_now:
            self._buy_touched_once = True

        if sell_amb and self._cc_sell_low is not None and self._ref_high is not None:
            verdict = self._resolve_outcome(chart_sec)
            if verdict == 2:
                self._cc_sell_set = False
                self._cc_invalidated = True
        if buy_amb and self._cc_buy_high is not None and self._ref_low is not None:
            verdict = self._resolve_outcome(chart_sec)
            if verdict == 2:
                self._cc_buy_set = False
                self._cc_invalidated = True

        sell_confirmed = (
            past_ref
            and not is_new_day
            and self._cc_sell_set
            and self._cc_sell_low is not None
            and self._cc_sell_bar is not None
            and idx > self._cc_sell_bar
            and bar.low < self._cc_sell_low
            and not self._cc_invalidated
        )
        buy_confirmed = (
            past_ref
            and not is_new_day
            and self._cc_buy_set
            and self._cc_buy_high is not None
            and self._cc_buy_bar is not None
            and idx > self._cc_buy_bar
            and bar.high > self._cc_buy_high
            and not self._cc_invalidated
        )

        raw_signal = SIGNAL_NONE
        if buy_confirmed and not sell_confirmed:
            raw_signal = SIGNAL_BUY
        elif sell_confirmed and not buy_confirmed:
            raw_signal = SIGNAL_SELL

        can_lock = (
            not self._direction_locked
            and raw_signal != SIGNAL_NONE
            and not is_new_day
            and not self._cc_invalidated
        )
        if can_lock:
            self._locked_dir = raw_signal
            self._direction_locked = True
            self._session_state = SESSION_DIRECTION_LOCKED

        raw_matches_lock = self._direction_locked and raw_signal == self._locked_dir
        can_validate = (
            self._session_state == SESSION_DIRECTION_LOCKED
            and raw_matches_lock
            and self._session_state not in (SESSION_IN_POSITION, SESSION_DAY_COMPLETE)
        )
        if can_validate:
            self._session_state = SESSION_WAIT_FIRST_BREAK  # allow immediate approval check

        final_signal = SIGNAL_NONE
        if raw_matches_lock and self._candle_count >= 4:
            final_signal = raw_signal

        is_idle = view.state.flat
        is_long = not is_idle and view.state.side == "LONG"
        is_short = not is_idle and view.state.side == "SHORT"
        is_exit_time = self._is_exit_time(cur_dt)

        if not self._day_done and not is_new_day:
            if is_long and self._sl_price is not None and bar.low <= self._sl_price:
                adj = self._sl_price * (1 - self._sl_slip / 100)
                self._day_done = True
                self._session_state = SESSION_DAY_COMPLETE
                self._sl_price = None
                return Signal(index=idx, timestamp=bar.timestamp, kind=SignalKind.SELL, price=adj)

            if is_short and self._sl_price is not None and bar.high >= self._sl_price:
                adj = self._sl_price * (1 + self._sl_slip / 100)
                self._day_done = True
                self._session_state = SESSION_DAY_COMPLETE
                self._sl_price = None
                return Signal(index=idx, timestamp=bar.timestamp, kind=SignalKind.BUY, price=adj)

        if is_exit_time and not self._day_done and not is_idle:
            adj = (
                bar.open * (1 - self._buy_slip / 100)
                if is_long
                else bar.open * (1 + self._sell_slip / 100)
            )
            self._day_done = True
            self._session_state = SESSION_DAY_COMPLETE
            self._sl_price = None
            return Signal(
                index=idx,
                timestamp=bar.timestamp,
                kind=SignalKind.SELL if is_long else SignalKind.BUY,
                price=adj,
            )

        if is_exit_time and not self._day_done and is_idle:
            self._day_done = True
            self._session_state = SESSION_DAY_COMPLETE

        if not self._day_done and past_ref and is_idle and final_signal != SIGNAL_NONE:
            if final_signal == SIGNAL_BUY:
                entry_price = (self._cc_buy_high or bar.close) * (1 + self._buy_slip / 100)
                self._sl_price = self._ref_low
                self._session_state = SESSION_IN_POSITION
                return Signal(
                    index=idx,
                    timestamp=bar.timestamp,
                    kind=SignalKind.BUY,
                    price=entry_price,
                    stop_loss=self._sl_price,
                )
            if final_signal == SIGNAL_SELL:
                entry_price = (self._cc_sell_low or bar.close) * (1 - self._sell_slip / 100)
                self._sl_price = self._ref_high
                self._session_state = SESSION_IN_POSITION
                return Signal(
                    index=idx,
                    timestamp=bar.timestamp,
                    kind=SignalKind.SELL,
                    price=entry_price,
                    stop_loss=self._sl_price,
                )

        return None


def obr_factory(params: StrategyParameters) -> StrategyLogic:
    """Factory registered under :data:`KIND`."""
    return ObrLogic(params)
