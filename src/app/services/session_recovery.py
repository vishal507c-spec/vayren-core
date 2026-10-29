"""Session Recovery — startup and reconnect historical candle recovery & catch-up.

Ensures the trading engine can start or recover at any time during market hours
without losing valid strategy opportunities merely because it started late,
restarted, or temporarily lost market data.

Key Responsibilities:
1. Historical Candle Recovery:
   - Fetches required historical OHLCV candles for all configured symbols via
     the FYERS Historical Data API (/data/history).
   - Recovers every completed candle from 09:15 opening candle up to current time.
   - Respects NSE market hours (09:15-15:30), holidays, and IST timezone.
   - Validates response, timestamps, missing candles, duplicates, and stale data.
   - Fails safely if required historical data cannot be recovered.
2. Gap Backfill for WebSocket Reconnects:
   - Backfills missing closed candles after temporary network / WebSocket drops.
3. Signal Expiry & Duplicate Guard:
   - Validates recovered historical signals against current market conditions,
     SL breach, session time cutoff, existing positions, and order idempotency.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data.candle_store import write_candles
from data.settings import NSE_HOLIDAYS
from execution.events import CandleEvent
from execution.models.intent import StrategySignal
from market.models.bar import Bar
from market.native_timeframe import seconds_of

logger = logging.getLogger(__name__)

#: Venue wall clock (IST: UTC + 5:30).
VENUE_TZ = timezone(timedelta(hours=5, minutes=30))

SESSION_OPEN_TIME = (9, 15)
SESSION_CLOSE_TIME = (15, 30)

RESOLUTION_BY_TIMEFRAME: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "20m": "20",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "D",
    "D": "D",
}


class HistoricalRecoveryError(RuntimeError):
    """Raised when required session historical data cannot be recovered."""


@dataclass(frozen=True)
class HistoricalRecoveryReport:
    """Detailed report of session historical recovery."""

    symbol: str
    status: str  # "SUCCESS", "NO_DATA_REQUIRED", "FAILED"
    expected_candles_count: int
    recovered_candles_count: int
    bars: tuple[Bar, ...]
    missing_intervals: tuple[str, ...]
    detail: str


def is_trading_day(dt: date | datetime, holidays: Collection[str] = NSE_HOLIDAYS) -> bool:
    """Check if the given date is an active NSE trading day (Mon-Fri, non-holiday)."""
    d = dt.date() if isinstance(dt, datetime) else dt
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    iso = d.isoformat()
    return iso not in holidays


def expected_completed_session_intervals(
    now_dt: datetime,
    timeframe: str = "30m",
    anchor: str = "09:15",
    holidays: Collection[str] = NSE_HOLIDAYS,
) -> list[tuple[datetime, datetime]]:
    """Return all completed candle interval windows (start_dt, end_dt) for today's session.

    Only intervals whose end_dt <= now_dt are completed. Incomplete candles
    (which are still open and forming in live ticks) are never returned.
    """
    now = now_dt.replace(tzinfo=VENUE_TZ) if now_dt.tzinfo is None else now_dt.astimezone(VENUE_TZ)

    if not is_trading_day(now, holidays):
        return []

    today = now.date()
    anchor_parts = anchor.split(":")
    anchor_h, anchor_m = int(anchor_parts[0]), int(anchor_parts[1])
    session_start = datetime(
        today.year, today.month, today.day, anchor_h, anchor_m, tzinfo=VENUE_TZ
    )
    session_close = datetime(today.year, today.month, today.day, 15, 30, tzinfo=VENUE_TZ)

    if now <= session_start:
        return []

    tf_sec = float(seconds_of(timeframe) or 1800)
    step = timedelta(seconds=tf_sec)

    completed: list[tuple[datetime, datetime]] = []
    curr = session_start
    while curr < session_close:
        interval_end = curr + step
        if interval_end > session_close:
            interval_end = session_close
        if interval_end <= now:
            completed.append((curr, interval_end))
        else:
            # Current interval is still forming/open; stop here
            break
        curr = interval_end

    return completed


def _format_stamp(dt: datetime) -> str:
    """Format datetime as naive wall-clock string 'YYYY-MM-DD HH:MM:SS'."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fetch_and_validate_session_candles(
    adapter: Any,
    symbol: str,
    timeframe: str,
    *,
    now_dt: datetime | None = None,
    holidays: Collection[str] = NSE_HOLIDAYS,
    data_dir: Path | str | None = None,
    fail_fast: bool = True,
) -> tuple[tuple[Bar, ...], HistoricalRecoveryReport]:
    """Fetch and validate completed session candles from the broker Historical API.

    Validates:
    - Expected intervals from 09:15 up to current time.
    - Eliminates incomplete candles (no lookahead / partial bars).
    - Checks for missing candles (e.g. 09:15 opening candle missing).
    - Deduplicates and sorts in strict ascending order.
    - Persists valid candles to local store if data_dir is provided.

    If required historical data cannot be recovered, raises HistoricalRecoveryError
    when fail_fast is True, or returns a FAILED report.
    """
    raw_now = now_dt if now_dt is not None else datetime.now(VENUE_TZ)
    now = (
        raw_now.replace(tzinfo=VENUE_TZ) if raw_now.tzinfo is None else raw_now.astimezone(VENUE_TZ)
    )

    expected = expected_completed_session_intervals(now, timeframe, holidays=holidays)
    resolution = RESOLUTION_BY_TIMEFRAME.get(str(timeframe).lower(), str(timeframe))

    if not expected:
        report = HistoricalRecoveryReport(
            symbol=symbol,
            status="NO_DATA_REQUIRED",
            expected_candles_count=0,
            recovered_candles_count=0,
            bars=(),
            missing_intervals=(),
            detail="Session has not started yet or today is not a trading day.",
        )
        return (), report

    if adapter is None or not hasattr(adapter, "history"):
        err = f"Broker adapter has no historical data capability for {symbol}"
        if fail_fast:
            raise HistoricalRecoveryError(err)
        return (), HistoricalRecoveryReport(
            symbol=symbol,
            status="FAILED",
            expected_candles_count=len(expected),
            recovered_candles_count=0,
            bars=(),
            missing_intervals=tuple(_format_stamp(s) for s, _ in expected),
            detail=err,
        )

    # Fetch from 09:15 IST today up to now
    start_dt = expected[0][0]
    start_epoch = int(start_dt.timestamp())
    now_epoch = int(now.timestamp())

    try:
        raw_rows = adapter.history(symbol, resolution, start_epoch, now_epoch)
    except Exception as exc:
        err = f"Failed to fetch historical session candles for {symbol}: {exc}"
        logger.error(err)
        if fail_fast:
            raise HistoricalRecoveryError(err) from exc
        return (), HistoricalRecoveryReport(
            symbol=symbol,
            status="FAILED",
            expected_candles_count=len(expected),
            recovered_candles_count=0,
            bars=(),
            missing_intervals=tuple(_format_stamp(s) for s, _ in expected),
            detail=err,
        )

    # Parse and filter rows
    by_stamp: dict[str, tuple[float, float, float, float, int]] = {}
    tf_sec = int(seconds_of(timeframe) or 1800)

    for row in raw_rows if isinstance(raw_rows, (list, tuple)) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            epoch = int(row[0])
            o, h, low, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = int(float(row[5]))
        except (TypeError, ValueError):
            continue

        if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(low) and math.isfinite(c)):
            continue
        if o <= 0 or h <= 0 or low <= 0 or c <= 0 or h < low or v < 0:
            continue

        candle_dt = datetime.fromtimestamp(epoch, VENUE_TZ)
        # Discard candles from prior days or before session start
        if candle_dt < start_dt:
            continue
        # Discard incomplete candles whose end time is in the future
        if (epoch + tf_sec) > now_epoch:
            logger.debug(
                "Skipping incomplete forming candle at %s (now=%s)",
                candle_dt,
                now,
            )
            continue

        stamp_str = _format_stamp(candle_dt)
        by_stamp[stamp_str] = (o, h, low, c, v)

    # Validate against expected intervals
    expected_stamps = [_format_stamp(s) for s, _ in expected]
    missing = [stamp for stamp in expected_stamps if stamp not in by_stamp]

    if missing:
        err = (
            f"Missing required session candle(s) for {symbol}: "
            f"expected {len(expected_stamps)}, recovered {len(by_stamp)}. "
            f"Missing stamps: {missing}"
        )
        logger.error(err)
        if fail_fast:
            raise HistoricalRecoveryError(err)
        return (), HistoricalRecoveryReport(
            symbol=symbol,
            status="FAILED",
            expected_candles_count=len(expected_stamps),
            recovered_candles_count=len(by_stamp),
            bars=(),
            missing_intervals=tuple(missing),
            detail=err,
        )

    # Build Bar objects in strict chronological order
    recovered_bars: list[Bar] = []
    store_rows: list[tuple[int, float, float, float, float, int]] = []
    for stamp in sorted(by_stamp.keys()):
        o, h, low, c, v = by_stamp[stamp]
        dt_val = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=VENUE_TZ)
        epoch_val = int(dt_val.timestamp())
        store_rows.append((epoch_val, o, h, low, c, v))
        recovered_bars.append(
            Bar(
                symbol=symbol,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                timestamp=stamp,
                bar_size=timeframe,
                source="fyers_history",
            )
        )

    # Persist to local candle store
    if data_dir is not None and store_rows:
        try:
            write_candles(data_dir, symbol, store_rows)
        except Exception as exc:
            logger.warning("Failed to persist recovered candles for %s: %s", symbol, exc)

    detail_msg = (
        f"Successfully recovered {len(recovered_bars)} session candles "
        f"from {expected_stamps[0]} to {expected_stamps[-1]}"
    )
    report = HistoricalRecoveryReport(
        symbol=symbol,
        status="SUCCESS",
        expected_candles_count=len(expected_stamps),
        recovered_candles_count=len(recovered_bars),
        bars=tuple(recovered_bars),
        missing_intervals=(),
        detail=detail_msg,
    )
    return tuple(recovered_bars), report


def evaluate_recovered_signal(
    signal: StrategySignal,
    *,
    now_dt: datetime,
    current_quote: float | None = None,
    subsequent_bars: Sequence[Bar] = (),
    existing_positions: Sequence[Any] = (),
    existing_orders: Sequence[Any] = (),
    exit_time_cutoff: tuple[int, int] = (15, 15),
    max_slippage_pct: float = 2.0,
) -> tuple[bool, str]:
    """Check if a signal that occurred while the engine was offline is still valid.

    Checks:
    1. Existing Position / Duplicate:
       - If position already open or order already submitted today -> DUPLICATE_PREVENTED.
    2. Session Time Cutoff:
       - If current time >= 15:15 IST (exit time) -> SESSION_TIME_EXIT_PASSED.
    3. Stop Loss Breached:
       - If subsequent bars touched SL, or current market price breached SL -> STOP_LOSS_BREACHED.
    4. Adverse Price Drift:
       - If current price has moved excessively far against entry -> ADVERSE_PRICE_DRIFT.

    Returns (is_valid, reason).
    """
    now = now_dt.replace(tzinfo=VENUE_TZ) if now_dt.tzinfo is None else now_dt.astimezone(VENUE_TZ)

    # 1. Existing position check
    for pos in existing_positions:
        sym = getattr(pos, "symbol", "") or (pos.get("symbol") if isinstance(pos, dict) else "")
        qty = getattr(pos, "quantity", 0) or (
            pos.get("quantity", 0) if isinstance(pos, dict) else 0
        )
        if sym == signal.symbol and qty != 0:
            return False, f"POSITION_ALREADY_EXISTS: position of {qty} exists for {signal.symbol}"

    # 2. Existing order check (idempotency)
    for ord_item in existing_orders:
        ord_sym = getattr(ord_item, "symbol", "") or (
            ord_item.get("symbol") if isinstance(ord_item, dict) else ""
        )
        ord_side = str(
            getattr(ord_item, "side", "")
            or (ord_item.get("side", "") if isinstance(ord_item, dict) else "")
        ).upper()
        if ord_sym == signal.symbol and ord_side == str(signal.side).upper():
            return (
                False,
                f"ORDER_ALREADY_SUBMITTED: {signal.side} order already placed for {signal.symbol}",
            )

    # 3. Session time cutoff
    cutoff_h, cutoff_m = exit_time_cutoff
    if now.hour > cutoff_h or (now.hour == cutoff_h and now.minute >= cutoff_m):
        return (
            False,
            f"SESSION_TIME_EXIT_PASSED: {now.strftime('%H:%M')} >= {cutoff_h:02d}:{cutoff_m:02d}",
        )

    side = str(signal.side).upper()
    entry_price = float(signal.price)
    stop_loss = float(signal.stop_loss) if signal.stop_loss is not None else None

    # 4. Stop Loss breached check
    if stop_loss is not None and stop_loss > 0:
        if side == "SELL":
            # Short position: stop loss is above entry price.
            for bar in subsequent_bars:
                if bar.high >= stop_loss:
                    return (
                        False,
                        f"STOP_LOSS_BREACHED: bar high {bar.high:.2f} >= SL {stop_loss:.2f}",
                    )
            if current_quote is not None and current_quote >= stop_loss:
                return (
                    False,
                    f"STOP_LOSS_BREACHED: quote {current_quote:.2f} >= SL {stop_loss:.2f}",
                )
        elif side == "BUY":
            # Long position: stop loss is below entry price.
            for bar in subsequent_bars:
                if bar.low <= stop_loss:
                    return (
                        False,
                        f"STOP_LOSS_BREACHED: bar low {bar.low:.2f} <= SL {stop_loss:.2f}",
                    )
            if current_quote is not None and current_quote <= stop_loss:
                return (
                    False,
                    f"STOP_LOSS_BREACHED: quote {current_quote:.2f} <= SL {stop_loss:.2f}",
                )

    # 5. Adverse price drift check
    if current_quote is not None and entry_price > 0:
        if side == "SELL":
            drift_pct = (current_quote - entry_price) / entry_price * 100.0
            if drift_pct > max_slippage_pct:
                return (
                    False,
                    f"ADVERSE_PRICE_DRIFT: rose {drift_pct:.2f}% "
                    f"({current_quote:.2f} vs {entry_price:.2f})",
                )
        elif side == "BUY":
            drift_pct = (entry_price - current_quote) / entry_price * 100.0
            if drift_pct > max_slippage_pct:
                return (
                    False,
                    f"ADVERSE_PRICE_DRIFT: fell {drift_pct:.2f}% "
                    f"({current_quote:.2f} vs {entry_price:.2f})",
                )

    return True, "VALID"


def backfill_candle_gap(
    adapter: Any,
    symbol: str,
    timeframe: str,
    last_seen_stamp: str,
    now_dt: datetime | None = None,
) -> tuple[CandleEvent, ...]:
    """Backfill missing completed candles between last_seen_stamp and current time.

    Used when WebSocket reconnects after a disconnect interval during market hours.
    """
    raw_now = now_dt if now_dt is not None else datetime.now(VENUE_TZ)
    now = (
        raw_now.replace(tzinfo=VENUE_TZ) if raw_now.tzinfo is None else raw_now.astimezone(VENUE_TZ)
    )

    if not is_trading_day(now):
        return ()

    try:
        last_dt = datetime.strptime(last_seen_stamp[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=VENUE_TZ
        )
    except Exception:
        return ()

    tf_sec = int(seconds_of(timeframe) or 1800)
    step = timedelta(seconds=float(tf_sec))
    expected_start = last_dt + step

    expected_intervals: list[tuple[datetime, datetime]] = []
    curr = expected_start
    while curr < now:
        end_val = curr + step
        if end_val <= now:
            expected_intervals.append((curr, end_val))
        else:
            break
        curr = end_val

    if not expected_intervals:
        return ()

    resolution = RESOLUTION_BY_TIMEFRAME.get(str(timeframe).lower(), str(timeframe))
    from_epoch = int(expected_intervals[0][0].timestamp())
    to_epoch = int(now.timestamp())

    try:
        raw_rows = adapter.history(symbol, resolution, from_epoch, to_epoch)
    except Exception as exc:
        logger.warning("Failed to fetch backfill candles for %s: %s", symbol, exc)
        return ()

    events: list[CandleEvent] = []
    for row in raw_rows if isinstance(raw_rows, (list, tuple)) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            epoch = int(row[0])
            o, h, low, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = int(float(row[5]))
        except (TypeError, ValueError):
            continue

        candle_dt = datetime.fromtimestamp(epoch, VENUE_TZ)
        if candle_dt <= last_dt or (epoch + tf_sec) > to_epoch:
            continue

        events.append(
            CandleEvent(
                symbol=symbol,
                timestamp=_format_stamp(candle_dt),
                seq=0,
                source="backfill_history",
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                timeframe=timeframe,
                is_closed=True,
            )
        )

    events.sort(key=lambda e: (e.timestamp, e.symbol))
    return tuple(events)


__all__ = [
    "HistoricalRecoveryError",
    "HistoricalRecoveryReport",
    "RESOLUTION_BY_TIMEFRAME",
    "VENUE_TZ",
    "backfill_candle_gap",
    "evaluate_recovered_signal",
    "expected_completed_session_intervals",
    "fetch_and_validate_session_candles",
    "is_trading_day",
]
