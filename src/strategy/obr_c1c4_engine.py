# OBR — Full Pine 1:1 engine, converted for VAYREN Strategy Lab
#
# The OBREngine.process_bar() state machine below is the AUTHORITATIVE
# OBR algorithm (LAYER 1 -> 12) and is preserved VERBATIM from the
# original standalone main.py clone of the "OBR" Pine Script indicator.
# Layer ordering, calculations, same-bar execution semantics, and the
# no-future-bar rule are untouched.
#
# What was REMOVED (Strategy Lab owns these, not the strategy):
#   - SQLite market-data loading (LAYER 2 data side)  -> Lab feeds bars
#   - argparse / CLI / main() / __main__ orchestration
#   - stdout summary tables / report printing / CSV export
#   - standalone Bar loading from D:\ZerodhaTradingData
#
# What was ADDED (thin adapter only):
#   - class Strategy(PythonStrategy): param_specs() + on_bar_logic()
#     which builds the engine's Bar and drives engine.process_bar(),
#     then maps the engine's LAYER 6B entry/exit event pulses onto the
#     Strategy Lab signal API (BUY / SELL / close).
#
# NOTE ON STOPS: the engine detects and records its own stop-loss exits
# (SL = refLow for longs / refHigh for shorts, with slSlippagePct) inside
# process_bar(). The adapter deliberately does NOT arm the Lab-side stop
# (no self.stop_loss()) because the Lab runner would then close the
# position one layer EARLIER (runner try_close runs before strategy
# logic and skips the bar), desynchronising the engine state machine.
# The engine remains the single source of truth for SL prices/PnL in
# engine.trades.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import cast
from zoneinfo import ZoneInfo

from strategy.models.plot_event import MarkerType
from strategy.models.signal import Signal, SignalKind
from strategy.strategies.base import PythonStrategy

IST = ZoneInfo("Asia/Kolkata")


# ============================================================================
#  LAYER 0 - CONFIGURATION                                      (verbatim)
# ============================================================================


@dataclass
class StrategyConfig:
    # -- ENGINE --
    maxDays: int = 1000  # "Rolling Market Days"
    showExportTable: bool = True
    exportMaxRows: int = 100
    exportRowsPerCol: int = 20

    # VAYREN OS v5.3.0 is SELL-only (short-side OBR). Long entries are
    # never generated, exactly like the original attached strategy.
    entryMode: str = "SELL"

    # -- REFERENCE CANDLE --
    refIndex: int = 3  # "REF Candle (1=first, 2=second...)"
    extendBars: int = 5  # visual only (REF line length, bars)
    c1ThresholdPct: float = 0.35
    c4ThresholdPct: float = 0.45

    # -- SESSION & TIME --
    exitHour: int = 15
    exitMinute: int = 15

    # -- DATE FILTER --
    useCalendar: bool = False
    fromDate: datetime = field(default_factory=lambda: datetime(2024, 1, 1, 0, 0, tzinfo=IST))
    toDate: datetime = field(default_factory=lambda: datetime(2026, 12, 31, 23, 59, tzinfo=IST))

    # -- SLIPPAGE & COSTS --
    buySlippagePct: float = 0.0
    sellSlippagePct: float = 0.0
    slSlippagePct: float = 0.0
    commissionPct: float = 0.0

    # -- STOP LOSS --
    # sellSLMode fixed in Pine (dropdown removed) -> always "REF HIGH"
    sellSLMode: str = "REF HIGH"

    # -- ENTRY LABEL (NEW) --
    showEntryLabel: bool = True
    labelOffsetPct: float = 0.5

    # -- RESOLVER DEBUG (NEW) --
    showResolverDebug: bool = False

    @property
    def rollingDays(self) -> int:
        # Pine: rollingDays = maxDays
        return self.maxDays


# ============================================================================
#  LAYER 1 - CONSTANTS                                          (verbatim)
# ============================================================================

SIGNAL_NONE = 0
SIGNAL_BUY = 1
SIGNAL_SELL = 2

STATE_IDLE = 0
STATE_LONG = 1
STATE_SHORT = 2

EXIT_NONE = 0
EXIT_SL = 1
EXIT_TIME = 2

TRADE_EVENT_NONE = 0
TRADE_EVENT_SL = 1
TRADE_EVENT_TIME_EXIT = 2

SESSION_STATE_WAIT_FIRST_BREAK = 0
SESSION_STATE_DIRECTION_LOCKED = 1
SESSION_STATE_FILTER_VALIDATION = 2
SESSION_STATE_IN_POSITION = 3
SESSION_STATE_NO_TRADE = 4
SESSION_STATE_DAY_COMPLETE = 5

# dayType codes used in the trade storage arrays (Pine LAYER 9 / 13)
DAYTYPE_BUY_WIN = 0
DAYTYPE_BUY_LOSS = 1
DAYTYPE_SELL_WIN = 2
DAYTYPE_SELL_LOSS = 3
DAYTYPE_NO_TRADE = -1


@dataclass(frozen=True, slots=True)
class Bar:
    bar_index: int
    ts: object  # ISO string or datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    date_key: object = None
    is_exit_time: bool = False


@dataclass(slots=True)
class TradeRecord:
    """One row per array-slot pushed in Pine LAYER 9 (all ten Pine
    arrays flattened into a single record so indices can never drift)."""

    trade_no: int
    day_type: int
    day_points: float
    exit_time: object
    entry_price: float | None
    exit_price: float | None
    direction: int
    exit_reason: int
    holding_bars: int
    exit_bar: int
    entry_time: object | None


# ============================================================================
#  LAYERS 3-12 - THE ENGINE (bar-by-bar state machine)          (verbatim)
# ============================================================================


class OBREngine:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

        # -- LAYER 2 (market data state) --
        self.prev_date_key = None
        self.candleCount = 0
        self.lastBarClose: float | None = None
        self.prevDayClose: float | None = None
        self.c1High: float | None = None
        self.c1Low: float | None = None
        self.c4High: float | None = None
        self.c4Low: float | None = None
        self.metaLabelPass: bool = True

        # -- LAYER 3 - reference candle --
        self.refHigh: float | None = None
        self.refLow: float | None = None
        self.refBar: int | None = None
        self.refSet: bool = False

        # -- LAYER 4 - break detection / confirmation --
        self.ccSellLow: float | None = None
        self.ccSellBreakHigh: float | None = None
        self.ccSellBar: int | None = None
        self.ccSellSet: bool = False

        self.ccBuyHigh: float | None = None
        self.ccBuyBar: int | None = None
        self.ccBuySet: bool = False

        self.firstBreakDone: bool = False
        self.ccInvalidated: bool = False

        # -- LAYER 5B - session ownership --
        self.lockedDirection: int = SIGNAL_NONE
        self.directionLocked: bool = False
        self.sessionState: int = SESSION_STATE_WAIT_FIRST_BREAK

        # -- LAYER 7 - trade state machine --
        self.tradeState: int = STATE_IDLE
        self.dayDone: bool = False
        self.entryPrice: float | None = None
        self.slPrice: float | None = None
        self.dayTime = None
        self.entryBarIndex: int | None = None
        self.entryTimeVal = None
        self.executedSignal: int = SIGNAL_NONE

        # -- LAYER 8 - trade event vars --
        self.tradeEventType: int = TRADE_EVENT_NONE
        self.tradeEventPnL: float | None = None
        self.tradeEventDirection: int | None = None
        self.tradeEventEntryPrice: float | None = None
        self.tradeEventExitPrice: float | None = None

        # -- LAYER 9 - storage --
        self.trades: list[TradeRecord] = []
        self._trade_counter = 0

        # -- LAYER 9C - shared market day engine --
        self.sharedMarketDays: list = []
        self._sharedMarketDays_set: set = set()

        # -- LAYER 9D - chart object lifecycle (rolling-window-scoped) --
        # Label object references for deleted candles (Python stores metadata only)
        self.dayEntryPriceLabelArr: list = []
        self.dayEntryShapeLabelArr: list = []
        self.dayExitTextLabelArr: list = []
        self.dayExitShapeLabelArr: list = []
        self.dayNTLabelArr: list = []
        self.currentEntryPriceLabel = None
        self.currentEntryShapeLabel = None

        # -- LAYER 6B - Entry/Exit event pulses (NEW) --
        self.entryEventNow: bool = False
        self.entryEventDir: int | None = None
        self.entryEventPrice: float | None = None

        self.exitEventNow: bool = False
        self.exitEventReason: int | None = None
        self.exitEventVisPrice: float | None = None
        self.exitEventPnl: float | None = None

        self.ntEventNow: bool = False
        self.ntEventVisPrice: float | None = None

        # -- LAYER 10 (non-visual state) --
        self.exitMarked: bool = False
        self.exitType: int = EXIT_NONE
        self.exitVisualPrice: float | None = None
        self.ntMarked: bool = False

        # -- LAYER 11 - analytics --
        self.runningEquity: float = 100000.0
        self.peakEquity: float = 100000.0
        self.currentDrawdown: float = 0.0
        self.maximumDrawdown: float = 0.0
        self.consecutiveLossEvents: int = 0
        self.maximumConsecutiveLossEvents: int = 0
        self.totalTradeEvents: int = 0
        self.totalLongEvents: int = 0
        self.totalShortEvents: int = 0
        self.totalWinEvents: int = 0
        self.totalLossEvents: int = 0

        self.last_bar: Bar | None = None

    # -- small Pine functions --
    def isIdle(self) -> bool:
        return self.tradeState == STATE_IDLE

    def isLong(self) -> bool:
        return self.tradeState == STATE_LONG

    def isShort(self) -> bool:
        return self.tradeState == STATE_SHORT

    @staticmethod
    def _day_key(ts):
        if hasattr(ts, "date"):
            return ts.date()
        s = str(ts)
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))

    def _register_market_day(self, ts) -> None:
        # f_registerMarketDay(_t)
        key = self._day_key(ts)
        if (
            not self.sharedMarketDays or self.sharedMarketDays[-1] != key
        ) and key not in self._sharedMarketDays_set:
            self.sharedMarketDays.append(key)
            self._sharedMarketDays_set.add(key)
        while len(self.sharedMarketDays) > self.cfg.rollingDays:
            rem = self.sharedMarketDays.pop(0)
            self._sharedMarketDays_set.discard(rem)

    def _trim_storage(self) -> None:
        # f_trimStorage()
        allowed_size = len(self.sharedMarketDays)
        while len(self.trades) > allowed_size:
            self.trades.pop(0)

        # -- LAYER 9D - Trim label arrays --
        while len(self.dayEntryPriceLabelArr) > allowed_size:
            self.dayEntryPriceLabelArr.pop(0)
        while len(self.dayEntryShapeLabelArr) > allowed_size:
            self.dayEntryShapeLabelArr.pop(0)
        while len(self.dayExitTextLabelArr) > allowed_size:
            self.dayExitTextLabelArr.pop(0)
        while len(self.dayExitShapeLabelArr) > allowed_size:
            self.dayExitShapeLabelArr.pop(0)
        while len(self.dayNTLabelArr) > allowed_size:
            self.dayNTLabelArr.pop(0)

    def shared_start_date(self):
        return self.sharedMarketDays[0] if self.sharedMarketDays else None

    def shared_end_date(self):
        return self.sharedMarketDays[-1] if self.sharedMarketDays else None

    @staticmethod
    def _resolve_signal(buy_conf: bool, sell_conf: bool) -> int:
        # f_resolveSignal - verbatim Pine logic. Pine has NO priority/tie-break
        # input: if both sides confirm on the same bar, resolved stays NONE.
        if buy_conf and not sell_conf:
            return SIGNAL_BUY
        if sell_conf and not buy_conf:
            return SIGNAL_SELL
        return SIGNAL_NONE

    # -- entry/exit arithmetic (Pine LAYER 7 / 8 / 10 formulas) --
    @staticmethod
    def _buy_entry_price(cc_buy_high: float, buy_slippage_pct: float) -> float:
        return cc_buy_high * (1 + buy_slippage_pct / 100)

    @staticmethod
    def _sell_entry_price(cc_sell_low: float, sell_slippage_pct: float) -> float:
        return cc_sell_low * (1 - sell_slippage_pct / 100)

    @staticmethod
    def _long_sl_exit(entry_price, sl_price, sl_slippage_pct, commission_pct):
        adj_exit = sl_price * (1 - sl_slippage_pct / 100)
        commission_cost = entry_price * commission_pct / 100
        loss_pts = abs(entry_price - adj_exit) + commission_cost
        return adj_exit, loss_pts

    @staticmethod
    def _short_sl_exit(entry_price, sl_price, sl_slippage_pct, commission_pct):
        adj_exit = sl_price * (1 + sl_slippage_pct / 100)
        commission_cost = entry_price * commission_pct / 100
        loss_pts = abs(adj_exit - entry_price) + commission_cost
        return adj_exit, loss_pts

    @staticmethod
    def _time_exit(
        is_long, entry_price, bar_open, buy_slippage_pct, sell_slippage_pct, commission_pct
    ):
        adj_exit_long = bar_open * (1 - buy_slippage_pct / 100)
        adj_exit_short = bar_open * (1 + sell_slippage_pct / 100)
        commission_cost = entry_price * commission_pct / 100
        if is_long:
            exit_price = adj_exit_long
            profit_pts = (adj_exit_long - entry_price) - commission_cost
        else:
            exit_price = adj_exit_short
            profit_pts = (entry_price - adj_exit_short) - commission_cost
        return exit_price, profit_pts

    # -- LAYER 11 - analytics engine --
    def _on_trade_event(self, pnl: float, direction: int) -> None:
        self.runningEquity += pnl
        self.peakEquity = max(self.peakEquity, self.runningEquity)
        self.currentDrawdown = self.peakEquity - self.runningEquity
        self.maximumDrawdown = max(self.maximumDrawdown, self.currentDrawdown)
        self.totalTradeEvents += 1

        if direction == STATE_LONG:
            self.totalLongEvents += 1
        if direction == STATE_SHORT:
            self.totalShortEvents += 1

        if pnl >= 0:
            self.totalWinEvents += 1
            self.consecutiveLossEvents = 0
        else:
            self.totalLossEvents += 1
            self.consecutiveLossEvents += 1
            self.maximumConsecutiveLossEvents = max(
                self.maximumConsecutiveLossEvents, self.consecutiveLossEvents
            )

    # ------------------------------------------------------------------
    # MAIN PER-BAR ENTRY POINT - mirrors the Pine Script's execution
    # order layer by layer. Do not reorder blocks.
    #
    # Pine executes entry, time-exit, and SL on the SAME bar where the
    # condition becomes true - no next-bar deferral.  This matches the
    # Pine indicator (not strategy()) which uses hand-rolled logic.
    # ------------------------------------------------------------------
    def process_bar(self, bar: Bar) -> None:
        cfg = self.cfg
        self.last_bar = bar

        # -- LAYER 6B - Reset event pulses (fires once per bar) --
        self.entryEventNow = False
        self.entryEventDir = None
        self.entryEventPrice = None
        self.exitEventNow = False
        self.exitEventReason = None
        self.exitEventVisPrice = None
        self.exitEventPnl = None
        self.ntEventNow = False
        self.ntEventVisPrice = None

        # -- LAYER 2 - MARKET DATA LAYER --
        date_key = bar.date_key if bar.date_key is not None else self._day_key(bar.ts)
        is_new_day = (self.prev_date_key != date_key) or (
            self.candleCount == 0 and self.c1High is None
        )
        self.prev_date_key = date_key

        if is_new_day:
            self.prevDayClose = self.lastBarClose
            self.candleCount = 0
            self.c1High = None
            self.c1Low = None
            self.c4High = None
            self.c4Low = None
            self.metaLabelPass = True
        else:
            self.candleCount += 1

        if self.candleCount == 0:
            self.c1High = bar.high
            self.c1Low = bar.low
        elif self.candleCount == 3:
            self.c4High = bar.high
            self.c4Low = bar.low
            if self.prevDayClose is not None and self.prevDayClose > 0:
                c1_rng = (
                    (self.c1High - self.c1Low)
                    if self.c1High is not None and self.c1Low is not None
                    else 0.0
                )
                c4_rng = self.c4High - self.c4Low
                c1_p = (c1_rng / self.prevDayClose) * 100.0
                c4_p = (c4_rng / self.prevDayClose) * 100.0
                c1_ok = c1_p >= cfg.c1ThresholdPct
                c4_ok = c4_p >= cfg.c4ThresholdPct
                self.metaLabelPass = c1_ok and c4_ok
            else:
                self.metaLabelPass = True

        in_date_range = True
        if cfg.useCalendar:
            bar_dt = (
                bar.ts
                if isinstance(bar.ts, datetime)
                else datetime.fromisoformat(str(bar.ts)).replace(tzinfo=IST)
            )
            in_date_range = cfg.fromDate <= bar_dt <= cfg.toDate

        is_exit_time = bar.is_exit_time

        # -- LAYER 3 - REFERENCE CANDLE LAYER --
        if self.candleCount == cfg.refIndex - 1:
            self.refHigh = bar.high
            self.refLow = bar.low
            self.refBar = bar.bar_index
            self.refSet = True

        # -- LAYER 4 - BREAK DETECTION + CONFIRMATION ENGINE --
        past_ref = self.refSet and (self.candleCount > cfg.refIndex - 1)

        if past_ref and not is_new_day and not self.firstBreakDone:
            low_broke = bar.low < cast(float, self.refLow)
            high_broke = bar.high > cast(float, self.refHigh)

            # Original v5.3.0 LiveCandleBreakEngine: a candle that breaks
            # ref_high (alone or together with ref_low) is an UP touch /
            # invalidation - NO TRADE, block the symbol for the day.
            if high_broke:
                self.ccInvalidated = True
                self.firstBreakDone = True
                self.dayDone = True
            elif low_broke:
                # Break candle CLOSE arms a SELL at THIS candle's low
                # (SLM trigger), not at ref_low and not one candle later.
                self.ccSellLow = bar.low
                self.ccSellBreakHigh = bar.high
                self.ccSellBar = bar.bar_index
                self.ccSellSet = True
                self.firstBreakDone = True

        # Original post-break invalidation: while the SELL is still
        # pending (not filled), ANY re-touch of ref_high cancels it and
        # blocks the day. Once a position is open, the SL owns the exit.
        if (
            past_ref
            and not is_new_day
            and self.firstBreakDone
            and not self.ccInvalidated
            and self.ccSellSet
            and self.ccSellBar != bar.bar_index
            and self.isIdle()
            and bar.high >= cast(float, self.refHigh)
        ):
            self.ccSellSet = False
            self.ccInvalidated = True
            self.dayDone = True

        # Original v5.3.0 arms the SELL on the break candle itself - there
        # is NO extra confirmation candle. The SLM trigger is the break
        # candle low; MetaLabel is computed before this evaluation on the
        # same closed bar, so the deferred-release race cannot occur.
        sell_confirmed = (
            past_ref
            and not is_new_day
            and self.ccSellSet
            and self.ccSellLow is not None
            and self.ccSellBar is not None
            and bar.bar_index == self.ccSellBar
            and not self.ccInvalidated
        )
        buy_confirmed = False  # SELL-only strategy

        # -- LAYER 5 - SIGNAL RESOLUTION LAYER --
        # f_resolveSignal(): if BOTH sides confirm on the same bar, the
        # signal stays NONE - identical to Pine (no priority tie-break).
        raw_signal = (
            self._resolve_signal(buy_confirmed, sell_confirmed) if past_ref else SIGNAL_NONE
        )

        # -- LAYER 5B - FIRST BREAK SESSION OWNERSHIP ENGINE --
        can_lock = (
            in_date_range
            and not self.directionLocked
            and raw_signal != SIGNAL_NONE
            and not is_new_day
            and not self.ccInvalidated
        )
        if can_lock:
            self.lockedDirection = raw_signal
            self.directionLocked = True
            self.sessionState = SESSION_STATE_DIRECTION_LOCKED

        raw_signal_matches_lock = self.directionLocked and raw_signal == self.lockedDirection

        can_validate = (
            self.sessionState == SESSION_STATE_DIRECTION_LOCKED
            and raw_signal_matches_lock
            and self.sessionState != SESSION_STATE_IN_POSITION
            and self.sessionState != SESSION_STATE_DAY_COMPLETE
            and self.sessionState != SESSION_STATE_NO_TRADE
        )
        if can_validate:
            self.sessionState = SESSION_STATE_FILTER_VALIDATION

        # -- LAYER 6 - APPROVAL ENGINE (MetaLabel C1/C4 Gate) --
        meta_ok = getattr(self, "metaLabelPass", True)
        # SELL-only approval (original v5.3.0). MetaLabel FAIL or missing
        # data never approves a trade.
        allow_sell_approval = (
            raw_signal_matches_lock
            and self.lockedDirection == SIGNAL_SELL
            and cfg.entryMode == "SELL"
            and meta_ok
        )
        final_approved_signal = SIGNAL_SELL if allow_sell_approval else SIGNAL_NONE

        # -- LAYER 7 - STATE MACHINE / EXECUTION ENGINE (ENTRY) --
        # Pine executes entry on the SAME bar at the breakout level.
        if (
            in_date_range
            and not self.dayDone
            and past_ref
            and not is_exit_time
            and self.isIdle()
            and final_approved_signal == SIGNAL_SELL
        ):
            self.tradeState = STATE_SHORT
            self.executedSignal = SIGNAL_SELL
            self.entryPrice = self._sell_entry_price(
                cast(float, self.ccSellLow), cfg.sellSlippagePct
            )
            self.slPrice = self.refHigh
            self.entryBarIndex = bar.bar_index
            self.entryTimeVal = bar.ts
            self.sessionState = SESSION_STATE_IN_POSITION

            # -- LAYER 6B - Set entry event pulse --
            self.entryEventNow = True
            self.entryEventDir = SIGNAL_SELL
            self.entryEventPrice = self.entryPrice

        # -- LAYER 10 - EXIT ENGINE --
        self.exitMarked = False
        self.ntMarked = False

        # LONG SL EXIT - immediate, same-bar
        if (
            in_date_range
            and self.isLong()
            and not self.dayDone
            and not is_new_day
            and bar.low <= cast(float, self.slPrice)
        ):
            adj_exit, loss_pts = self._long_sl_exit(
                self.entryPrice, self.slPrice, cfg.slSlippagePct, cfg.commissionPct
            )
            self.dayTime = bar.ts
            self.exitType = EXIT_SL
            self.exitMarked = True
            self.exitVisualPrice = self.slPrice

            self.tradeEventType = TRADE_EVENT_SL
            self.tradeEventPnL = -loss_pts
            self.tradeEventDirection = STATE_LONG
            self.tradeEventEntryPrice = self.entryPrice
            self.tradeEventExitPrice = adj_exit

            self._trade_counter += 1
            rec = TradeRecord(
                trade_no=self._trade_counter,
                day_type=DAYTYPE_BUY_LOSS,
                day_points=-loss_pts,
                exit_time=self.dayTime,
                entry_price=self.entryPrice,
                exit_price=adj_exit,
                direction=STATE_LONG,
                exit_reason=EXIT_SL,
                holding_bars=bar.bar_index - cast(int, self.entryBarIndex),
                exit_bar=bar.bar_index,
                entry_time=self.entryTimeVal,
            )
            self.trades.append(rec)
            # -- LAYER 9D - Track label objects (Python stores None) --
            self.dayEntryPriceLabelArr.append(self.currentEntryPriceLabel)
            self.dayEntryShapeLabelArr.append(self.currentEntryShapeLabel)
            self.dayExitTextLabelArr.append(None)
            self.dayExitShapeLabelArr.append(None)
            self.dayNTLabelArr.append(None)
            self.currentEntryPriceLabel = None
            self.currentEntryShapeLabel = None

            self._register_market_day(bar.ts)
            self._trim_storage()

            self.tradeState = STATE_IDLE
            self.sessionState = SESSION_STATE_DAY_COMPLETE
            self.dayDone = True

            # -- LAYER 6B - Set exit event pulse --
            self.exitEventNow = True
            self.exitEventReason = EXIT_SL
            self.exitEventVisPrice = self.slPrice
            self.exitEventPnl = -loss_pts

        # SHORT SL EXIT - immediate, same-bar
        if (
            in_date_range
            and self.isShort()
            and not self.dayDone
            and not is_new_day
            and bar.high >= cast(float, self.slPrice)
        ):
            adj_exit, loss_pts = self._short_sl_exit(
                self.entryPrice, self.slPrice, cfg.slSlippagePct, cfg.commissionPct
            )
            self.dayTime = bar.ts
            self.exitType = EXIT_SL
            self.exitMarked = True
            self.exitVisualPrice = self.slPrice

            self.tradeEventType = TRADE_EVENT_SL
            self.tradeEventPnL = -loss_pts
            self.tradeEventDirection = STATE_SHORT
            self.tradeEventEntryPrice = self.entryPrice
            self.tradeEventExitPrice = adj_exit

            self._trade_counter += 1
            rec = TradeRecord(
                trade_no=self._trade_counter,
                day_type=DAYTYPE_SELL_LOSS,
                day_points=-loss_pts,
                exit_time=self.dayTime,
                entry_price=self.entryPrice,
                exit_price=adj_exit,
                direction=STATE_SHORT,
                exit_reason=EXIT_SL,
                holding_bars=bar.bar_index - cast(int, self.entryBarIndex),
                exit_bar=bar.bar_index,
                entry_time=self.entryTimeVal,
            )
            self.trades.append(rec)
            # -- LAYER 9D - Track label objects (Python stores None) --
            self.dayEntryPriceLabelArr.append(self.currentEntryPriceLabel)
            self.dayEntryShapeLabelArr.append(self.currentEntryShapeLabel)
            self.dayExitTextLabelArr.append(None)
            self.dayExitShapeLabelArr.append(None)
            self.dayNTLabelArr.append(None)
            self.currentEntryPriceLabel = None
            self.currentEntryShapeLabel = None

            self._register_market_day(bar.ts)
            self._trim_storage()

            self.tradeState = STATE_IDLE
            self.sessionState = SESSION_STATE_DAY_COMPLETE
            self.dayDone = True

            # -- LAYER 6B - Set exit event pulse --
            self.exitEventNow = True
            self.exitEventReason = EXIT_SL
            self.exitEventVisPrice = self.slPrice
            self.exitEventPnl = -loss_pts

        # TIME EXIT - immediate, same-bar (uses open, matching Pine)
        if in_date_range and is_exit_time and not self.dayDone and not self.isIdle():
            is_long = self.isLong()
            exit_price, profit_pts = self._time_exit(
                is_long,
                self.entryPrice,
                bar.open,
                cfg.buySlippagePct,
                cfg.sellSlippagePct,
                cfg.commissionPct,
            )
            self.dayTime = bar.ts
            self.exitType = EXIT_TIME
            self.exitMarked = True
            self.exitVisualPrice = bar.open

            direction = STATE_LONG if is_long else STATE_SHORT
            self.tradeEventType = TRADE_EVENT_TIME_EXIT
            self.tradeEventPnL = profit_pts
            self.tradeEventDirection = direction
            self.tradeEventEntryPrice = self.entryPrice
            self.tradeEventExitPrice = exit_price

            if is_long:
                day_type = DAYTYPE_BUY_WIN if profit_pts >= 0 else DAYTYPE_BUY_LOSS
            else:
                day_type = DAYTYPE_SELL_WIN if profit_pts >= 0 else DAYTYPE_SELL_LOSS

            self._trade_counter += 1
            rec = TradeRecord(
                trade_no=self._trade_counter,
                day_type=day_type,
                day_points=profit_pts,
                exit_time=self.dayTime,
                entry_price=self.entryPrice,
                exit_price=exit_price,
                direction=direction,
                exit_reason=EXIT_TIME,
                holding_bars=bar.bar_index - cast(int, self.entryBarIndex),
                exit_bar=bar.bar_index,
                entry_time=self.entryTimeVal,
            )
            self.trades.append(rec)
            # -- LAYER 9D - Track label objects (Python stores None) --
            self.dayEntryPriceLabelArr.append(self.currentEntryPriceLabel)
            self.dayEntryShapeLabelArr.append(self.currentEntryShapeLabel)
            self.dayExitTextLabelArr.append(None)
            self.dayExitShapeLabelArr.append(None)
            self.dayNTLabelArr.append(None)
            self.currentEntryPriceLabel = None
            self.currentEntryShapeLabel = None

            self._register_market_day(bar.ts)
            self._trim_storage()

            self.tradeState = STATE_IDLE
            self.sessionState = SESSION_STATE_DAY_COMPLETE
            self.dayDone = True

            # -- LAYER 6B - Set exit event pulse --
            self.exitEventNow = True
            self.exitEventReason = EXIT_TIME
            self.exitEventVisPrice = bar.open
            self.exitEventPnl = profit_pts

        # NO TRADE
        if in_date_range and is_exit_time and not self.dayDone and self.isIdle():
            self.dayTime = bar.ts
            self.ntMarked = True
            nt_visual_price = bar.low - abs(bar.low) * 0.0025

            self._trade_counter += 1
            self.trades.append(
                TradeRecord(
                    trade_no=self._trade_counter,
                    day_type=DAYTYPE_NO_TRADE,
                    day_points=0.0,
                    exit_time=self.dayTime,
                    entry_price=None,
                    exit_price=None,
                    direction=STATE_IDLE,
                    exit_reason=EXIT_NONE,
                    holding_bars=0,
                    exit_bar=bar.bar_index,
                    entry_time=None,
                )
            )
            # -- LAYER 9D - Track label objects (Python stores None) --
            self.dayEntryPriceLabelArr.append(None)
            self.dayEntryShapeLabelArr.append(None)
            self.dayExitTextLabelArr.append(None)
            self.dayExitShapeLabelArr.append(None)
            self.dayNTLabelArr.append(None)

            self._register_market_day(bar.ts)
            self._trim_storage()

            self.sessionState = SESSION_STATE_DAY_COMPLETE
            self.dayDone = True

            # -- LAYER 6B - Set NT event pulse --
            self.ntEventNow = True
            self.ntEventVisPrice = nt_visual_price

        # -- LAYER 11 - ANALYTICS ENGINE --
        has_trade_event = self.tradeEventType != TRADE_EVENT_NONE
        if has_trade_event:
            self._on_trade_event(
                cast(float, self.tradeEventPnL), cast(int, self.tradeEventDirection)
            )
            self.tradeEventType = TRADE_EVENT_NONE
            self.tradeEventPnL = None
            self.tradeEventDirection = None
            self.tradeEventEntryPrice = None
            self.tradeEventExitPrice = None

        self.lastBarClose = bar.close

        # -- LAYER 12 - DAY RESET ENGINE --
        # Runs at the END of the bar, matching Pine's order.
        if is_new_day:
            self.tradeState = STATE_IDLE
            self.dayDone = False
            self.entryPrice = None
            self.slPrice = None
            self.executedSignal = SIGNAL_NONE
            self.lockedDirection = SIGNAL_NONE
            self.directionLocked = False
            self.sessionState = SESSION_STATE_WAIT_FIRST_BREAK
            self.exitMarked = False
            self.exitType = EXIT_NONE
            self.exitVisualPrice = None
            self.ntMarked = False
            self.ccSellLow = None
            self.ccSellBreakHigh = None
            self.ccSellBar = None
            self.ccSellSet = False
            self.ccBuyHigh = None
            self.ccBuyBar = None
            self.ccBuySet = False
            self.refHigh = None
            self.refLow = None
            self.refBar = None
            self.refSet = False
            self.firstBreakDone = False
            self.ccInvalidated = False

    def reset_for_new_day(self, current_date=None) -> None:
        """Reset all intraday session state for a new trading day boundary."""
        self.tradeState = STATE_IDLE
        self.dayDone = False
        self.entryPrice = None
        self.slPrice = None
        self.executedSignal = SIGNAL_NONE
        self.lockedDirection = SIGNAL_NONE
        self.directionLocked = False
        self.sessionState = SESSION_STATE_WAIT_FIRST_BREAK
        self.exitMarked = False
        self.exitType = EXIT_NONE
        self.exitVisualPrice = None
        self.ntMarked = False
        self.refHigh = None
        self.refLow = None
        self.refBar = None
        self.refSet = False
        self.ccSellLow = None
        self.ccSellBreakHigh = None
        self.ccSellBar = None
        self.ccSellSet = False
        self.ccBuyHigh = None
        self.ccBuyBar = None
        self.ccBuySet = False
        self.firstBreakDone = False
        self.ccInvalidated = False
        self.c1High = None
        self.c1Low = None
        self.c4High = None
        self.c4Low = None
        self.metaLabelPass = True
        self.candleCount = 0
        if current_date is not None:
            self.prev_date_key = current_date

    def run(self, bars) -> None:
        """Feed the whole bar stream through process_bar(), one at a time,
        strictly sequential, never touching a future bar."""
        for bar in bars:
            self.process_bar(bar)


# ============================================================================
#  STRATEGY LAB ADAPTER (thin) - PythonStrategy lifecycle
# ============================================================================


class Strategy(PythonStrategy):
    """OBR — Opening Range Breakout (full Pine 1:1 engine).

    The engine state machine above owns ALL OBR logic. This adapter only:
      1. exposes the user-configurable parameters via param_specs(),
      2. converts each Strategy Lab bar into the engine's Bar (IST-aware),
      3. drives engine.process_bar() exactly once per bar (Lab owns iteration),
      4. maps the engine's LAYER 6B event pulses onto the Lab signal API:
           entryEventNow  -> BUY / SELL
           exitEventNow   -> close current position
           REF HIGH/LOW   -> plot_ray()
    """

    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec

        return (
            ParameterSpec(
                key="refIndex",
                label="REF Candle Index",
                default=3,
                minimum=1,
                maximum=10,
                decimals=0,
            ),
            ParameterSpec(
                key="c1ThresholdPct",
                label="C1 Threshold %",
                default=0.35,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="c4ThresholdPct",
                label="C4 Threshold %",
                default=0.45,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="exitHour",
                label="Exit Hour (IST)",
                default=15,
                minimum=9,
                maximum=15,
                decimals=0,
            ),
            ParameterSpec(
                key="exitMinute", label="Exit Minute", default=15, minimum=0, maximum=59, decimals=0
            ),
            ParameterSpec(
                key="buySlippagePct",
                label="Buy Slippage %",
                default=0.0,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="sellSlippagePct",
                label="Sell Slippage %",
                default=0.0,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="slSlippagePct",
                label="SL Slippage %",
                default=0.0,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="commissionPct",
                label="Commission %",
                default=0.0,
                minimum=0.0,
                maximum=5.0,
                decimals=3,
            ),
            ParameterSpec(
                key="extendBars",
                label="Line Extension (Bars)",
                default=5,
                minimum=1,
                maximum=20,
                decimals=0,
            ),
        )

    def warmup(self) -> int:
        # The engine counts candles per day itself (candleCount resets on
        # every session). Skipping bars would corrupt the day state machine,
        # so the Lab must feed EVERY bar starting from the first.
        return 0

    def __init__(self, params=None):
        super().__init__(params)
        p = self.params
        ext = max(1, int(p.get("extendBars", 5)))
        self.extendBars = ext
        self.cfg = StrategyConfig(
            refIndex=int(p.get("refIndex", 3)),
            c1ThresholdPct=float(p.get("c1ThresholdPct", 0.35)),
            c4ThresholdPct=float(p.get("c4ThresholdPct", 0.45)),
            exitHour=int(p.get("exitHour", 15)),
            exitMinute=int(p.get("exitMinute", 15)),
            buySlippagePct=float(p.get("buySlippagePct", 0.0)),
            sellSlippagePct=float(p.get("sellSlippagePct", 0.0)),
            slSlippagePct=float(p.get("slSlippagePct", 0.0)),
            commissionPct=float(p.get("commissionPct", 0.0)),
            extendBars=ext,
        )
        self.engine = OBREngine(self.cfg)
        self._ref_plotted_day = None

    @property
    def slPrice(self) -> float | None:
        return getattr(self.engine, "slPrice", None)

    def _engine_bar(self, view):
        bar = view.bar
        s = str(bar.timestamp)
        d = date(int(s[:4]), int(s[5:7]), int(s[8:10]))
        h = int(s[11:13])
        m = int(s[14:16])
        return Bar(
            bar_index=view.index,
            ts=s,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            date_key=d,
            is_exit_time=(
                h > self.cfg.exitHour or (h == self.cfg.exitHour and m >= self.cfg.exitMinute)
            ),
        )

    def on_bar_logic(self, view):
        bar = self._engine_bar(view)
        self.engine.process_bar(bar)
        eng = self.engine

        # Reference-candle visual marking (mirrors the Pine indicator's
        # REF HIGH / REF LOW lines) through the Strategy Lab plot API.
        if getattr(self, "_plot_enabled", False) and eng.refSet and eng.refBar == bar.bar_index:
            self.plot_ray(
                view.index,
                float(cast(float, eng.refHigh)),
                plot_id="ref_high",
                symbol=view.bar.symbol,
                extend_bars=self.extendBars,
            )
            self.plot_ray(
                view.index,
                float(cast(float, eng.refLow)),
                plot_id="ref_low",
                symbol=view.bar.symbol,
                extend_bars=self.extendBars,
            )

        # Same-bar entry+exit (Pine entered AND exited on this bar, e.g. the
        # confirmation bar also touching SL, or confirming at/after 15:15):
        # the Pine end-state is flat with the day done, and the host carries
        # only one signal per bar. Emitting BUY/SELL here would strand a
        # phantom Lab position the engine can never close (it is already
        # flat/dayDone), desynchronising every later mirror signal. Emit
        # nothing - the engine trade record (source of truth) keeps the trade.
        both_same_bar = bool(eng.entryEventNow and eng.exitEventNow)
        # LAYER 6B entry pulse -> Lab entry signal. Original v5.3.0 places a
        # SLM SELL whose TRIGGER is the break candle low (eng.entryEventPrice),
        # NOT the confirmation/bar close. Stop is the reference high.
        entry_signal = None
        # SELL-only: the original strategy never opens a long position.
        if eng.entryEventNow and not both_same_bar and eng.entryEventDir == SIGNAL_SELL:
            entry_signal = Signal(
                index=view.index,
                timestamp=str(bar.ts),
                kind=SignalKind.SELL,
                price=float(cast(float, eng.entryEventPrice)),
                stop_loss=(float(eng.slPrice) if eng.slPrice is not None else None),
            )
        # NOTE: no self.stop_loss() here — the engine owns SL detection
        # and exits (see file header note). Arming the Lab stop would
        # make the runner close one layer early and desync the engine.

        # LAYER 6B exit pulse (SL / TIME) -> close the Lab position.
        # NO TRADE days produce no Lab signal (nothing to close).
        if eng.exitEventNow and not both_same_bar:
            self.close_position(view)

        # TradingView-like visuals. READ-ONLY: engine pulses/state only, and
        # driven by the RAW pulses (not the host-limited mapping above), so
        # the chart shows Pine events even for 0-hold same-bar round trips.
        if getattr(self, "_plot_enabled", False):
            self._plot_obr_visuals(view, eng)

        return entry_signal

    def _plot_obr_visuals(self, view, eng):
        """Emit universal PlotEvents for engine events (visuals only).

        Every coordinate below is an engine pulse/state field - no OBR
        condition is recomputed here (no breakout/confirmation/SL/time-exit/
        PnL). Markers are compact glyphs with no text; REF rays extend
        exactly extendBars candles (visual only). One engine event yields
        exactly one PlotEvent; same-bar events coexist by unique plot_id.
        Never arms a Lab stop (no self.stop_loss() here).
        """
        idx = view.index
        symbol = view.bar.symbol
        if eng.entryEventNow:
            # Owner-supplied entry text (reference-style compact label) only
            # when explicitly requested via showEntryLabel; otherwise the
            # arrow direction alone communicates the signal (text=None).
            entry_text = None
            if self.cfg.showEntryLabel:
                if eng.entryEventDir == SIGNAL_BUY:
                    entry_text = f"BUY {float(eng.entryEventPrice):.2f}"
                elif eng.entryEventDir == SIGNAL_SELL:
                    entry_text = f"SELL {float(eng.entryEventPrice):.2f}"
            if eng.entryEventDir == SIGNAL_BUY:
                self.plot_marker(
                    idx,
                    float(eng.entryEventPrice),
                    MarkerType.UP_ARROW,
                    plot_id=f"buy_{idx}",
                    symbol=symbol,
                    text=entry_text,
                )
            elif eng.entryEventDir == SIGNAL_SELL:
                self.plot_marker(
                    idx,
                    float(eng.entryEventPrice),
                    MarkerType.DOWN_ARROW,
                    plot_id=f"sell_{idx}",
                    symbol=symbol,
                    text=entry_text,
                )
            # NOTE: SL has NO chart visual (spec: SL invisible) - the engine
            # still owns slPrice/SL detection/exits; only plotting is skipped.
        if eng.exitEventNow:
            if eng.exitEventReason == EXIT_SL:
                # Mute the execution pill: this exit intentionally shows nothing.
                self.mute_signal_bar(idx)
            elif eng.exitEventReason == EXIT_TIME:
                if eng.entryEventNow:
                    # Same-bar round trip: never put EOD on the entry candle.
                    self.plot_marker(
                        idx,
                        float(eng.exitEventVisPrice),
                        MarkerType.DIAMOND,
                        plot_id=f"time_{idx}",
                        symbol=symbol,
                    )
                else:
                    # EOD: blue up arrow with owner-supplied price + signed PnL.
                    pnl = eng.exitEventPnl if eng.exitEventPnl is not None else 0.0
                    self.plot_marker(
                        idx,
                        float(eng.exitEventVisPrice),
                        MarkerType.TRIANGLE_BLUE,
                        plot_id=f"eod_{idx}",
                        symbol=symbol,
                        text=(f"EOD {float(eng.exitEventVisPrice):.2f} {float(pnl):+.2f}"),
                    )
        if eng.ntEventNow:
            self.plot_marker(
                idx,
                float(eng.ntEventVisPrice),
                MarkerType.CIRCLE_X,
                plot_id=f"no_trade_{idx}",
                symbol=symbol,
            )
