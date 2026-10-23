"""Timeframe-aware market-data health policy.

Three different questions must never be conflated:

* **Transport liveness** — is the broker socket connected? Owned by the
  provider/face (``health()``), never by this module.
* **Candle completeness** — has a closed candle arrived when the timeframe
  says it should? This module answers that question.
* **Market calendar** — should a candle arrive at all right now? Weekends,
  holidays, the overnight gap and the pre/post-session window suspend the
  expectation instead of manufacturing a false stale alarm.

The numeric candle window is derived by the Rust stream kernel
(``vy_norm_candle_stale_after``): one interval plus a 10% delivery grace,
clamped to 30–300 seconds. Python never keeps a second copy of that rule. It
only decides *when the expectation clock is valid*: the session rebases the
kernel clock at each market open, and no deadline exists while the calendar is
closed. With a 30-minute timeframe the window is 1,980 seconds — comfortably
longer than the 1,800-second cadence, and short enough to flag a genuinely
missing candle a few minutes after it was due.

NSE timestamps are fixed-offset IST (no daylight saving), so a timezone
object is used rather than the platform tz database. Only the session window
is modelled here; per-symbol sequencing, duplicate suppression and the
stale verdict itself stay in Rust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from execution.market_data.native_normalizer import candle_stale_after
from market import timeframe_seconds

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_SESSION_START = "09:15"
DEFAULT_SESSION_END = "15:30"


def _clock_seconds(value: str, *, field_name: str) -> int:
    """``"HH:MM"`` → seconds of day; malformed values are caller misuse."""
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be HH:MM, got {value!r}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{field_name} must be a valid time, got {value!r}")
    return hour * 3600 + minute * 60


def _ist_clock(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, IST)


@dataclass(frozen=True)
class MarketCalendar:
    """Cash-market session calendar for one venue (NSE by default)."""

    session_start: str = DEFAULT_SESSION_START
    session_end: str = DEFAULT_SESSION_END
    holidays: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        start = _clock_seconds(self.session_start, field_name="session_start")
        end = _clock_seconds(self.session_end, field_name="session_end")
        if end <= start:
            raise ValueError("session_end must be after session_start")
        for holiday in self.holidays:
            try:
                date.fromisoformat(holiday)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"holiday must be YYYY-MM-DD, got {holiday!r}") from exc

    @property
    def open_seconds(self) -> int:
        return _clock_seconds(self.session_end, field_name="session_end") - _clock_seconds(
            self.session_start, field_name="session_start"
        )

    def is_trading_day(self, day: date) -> bool:
        """Weekday that is not in the configured holiday set."""
        return day.weekday() < 5 and day.isoformat() not in self.holidays

    def trading_day(self, epoch: float) -> date:
        """IST calendar date for an epoch (the unit of the daily rebase)."""
        return _ist_clock(epoch).date()

    def is_open(self, epoch: float) -> bool:
        moment = _ist_clock(epoch)
        if not self.is_trading_day(moment.date()):
            return False
        seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
        return (
            _clock_seconds(self.session_start, field_name="session_start")
            <= seconds
            < _clock_seconds(self.session_end, field_name="session_end")
        )

    def closure_reason(self, epoch: float) -> str:
        """Operator-facing reason the calendar is not open at ``epoch``."""
        moment = _ist_clock(epoch)
        if not self.is_trading_day(moment.date()):
            kind = "holiday" if moment.date().isoformat() in self.holidays else "weekend"
            return f"market closed ({kind})"
        seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
        if seconds < _clock_seconds(self.session_start, field_name="session_start"):
            return f"market closed (pre-open; opens {self.session_start} IST)"
        return f"market closed (post-close; closed {self.session_end} IST)"

    def open_seconds_between(self, start_epoch: float, end_epoch: float) -> float:
        """Seconds of *trading session* time in ``[start_epoch, end_epoch]``.

        Closed periods are excluded. Iterating calendar days is bounded by the
        actual gap (at most a few years of history), and a multi-day holiday
        contributes nothing.
        """
        if end_epoch <= start_epoch:
            return 0.0
        start = _ist_clock(start_epoch)
        end = _ist_clock(end_epoch)
        total = 0.0
        day = start.date()
        last_day = end.date()
        start_s = _clock_seconds(self.session_start, field_name="session_start")
        end_s = _clock_seconds(self.session_end, field_name="session_end")
        while day <= last_day:
            if self.is_trading_day(day):
                window_start = datetime.combine(day, time.min, tzinfo=IST) + timedelta(
                    seconds=start_s
                )
                window_end = datetime.combine(day, time.min, tzinfo=IST) + timedelta(seconds=end_s)
                overlap_start = max(window_start, start)
                overlap_end = min(window_end, end)
                if overlap_end > overlap_start:
                    total += (overlap_end - overlap_start).total_seconds()
            day += timedelta(days=1)
        return total

    def session_open_epoch(self, epoch: float) -> float:
        """Epoch of the current trading day's session open (IST)."""
        moment = _ist_clock(epoch)
        start_s = _clock_seconds(self.session_start, field_name="session_start")
        return (
            datetime.combine(moment.date(), time.min, tzinfo=IST) + timedelta(seconds=start_s)
        ).timestamp()

    def expectation_anchor(self, last_event_epoch: float | None, now_epoch: float) -> float | None:
        """When the *current* expectation window started, or ``None`` if closed.

        The anchor is the later of the last delivery and today's session open.
        This is the key calendar rule: the tail of a previous session (for
        example 15:15→15:30 on Friday) is not time in which a new candle was
        expected, so it must not make Monday's first candle overdue. The live
        session rebases the kernel clock to the same anchor at each open; this
        pure function exposes the same rule for tests and diagnostics.
        """
        if not self.is_open(now_epoch):
            return None
        open_epoch = self.session_open_epoch(now_epoch)
        if last_event_epoch is None:
            return open_epoch
        if last_event_epoch < open_epoch:
            return open_epoch
        return min(last_event_epoch, now_epoch)

    def overdue_seconds(self, last_event_epoch: float | None, now_epoch: float) -> float | None:
        """Seconds since the current expectation window started; None if closed."""
        anchor = self.expectation_anchor(last_event_epoch, now_epoch)
        if anchor is None:
            return None
        return max(0.0, now_epoch - anchor)


@dataclass(frozen=True)
class MarketDataHealthPolicy:
    """Candle-completeness policy for one session/timeframe."""

    timeframe: str
    interval_seconds: float
    stale_after_seconds: float
    heartbeat_timeout_seconds: float
    calendar: MarketCalendar = field(default_factory=MarketCalendar)

    @classmethod
    def for_timeframe(
        cls,
        timeframe: str,
        *,
        session_start: str = DEFAULT_SESSION_START,
        session_end: str = DEFAULT_SESSION_END,
        holidays: tuple[str, ...] = (),
    ) -> MarketDataHealthPolicy:
        """Build the policy with the kernel-owned staleness window."""
        interval = timeframe_seconds(timeframe)
        if interval is None or interval <= 0:
            raise ValueError(f"unreadable timeframe for health policy: {timeframe!r}")
        window = candle_stale_after(float(interval))
        return cls(
            timeframe=timeframe,
            interval_seconds=float(interval),
            stale_after_seconds=window,
            # Heartbeats are a fallback until the first candle exists. They
            # must never turn a valid slow candle cadence into an alarm, so
            # the fallback shares the candle window rather than a 30s tick
            # timeout that would flag every 30-minute feed.
            heartbeat_timeout_seconds=max(30.0, window),
            calendar=MarketCalendar(session_start, session_end, tuple(holidays)),
        )

    def expected_deadline(self, last_event_epoch: float | None, now_epoch: float) -> float | None:
        """Wall-clock time at which the *next* candle is overdue.

        ``None`` while the market is closed (no deadline exists). The anchor
        is today's open or the last delivery, whichever is later, so a prior
        session's tail never counts against the first candle of a new day.
        """
        anchor = self.calendar.expectation_anchor(last_event_epoch, now_epoch)
        if anchor is None:
            return None
        return anchor + self.stale_after_seconds

    def describe(self, now_epoch: float) -> str:
        """Short operator-facing state: open deadline vs closure reason."""
        if not self.calendar.is_open(now_epoch):
            return self.calendar.closure_reason(now_epoch)
        return f"market open; candle window {self.stale_after_seconds:.0f}s"
