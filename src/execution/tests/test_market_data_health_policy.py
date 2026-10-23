"""Timeframe-derived market-data health, calendar boundaries and recovery.

The 2026-10-22 incident combined two defects: a 60-second stale window was
applied to 30-minute candles, and the pre-ingest gate dropped the first
recovered candle so a stale stream could never clear itself. These tests pin
the kernel-derived window, the NSE session calendar, the daily rebase, and
the fact that a fresh delivery always clears a latched stale flag.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from execution.events import CandleEvent, HeartbeatEvent
from execution.market_data.health_policy import IST, MarketCalendar, MarketDataHealthPolicy
from execution.market_data.native_normalizer import candle_stale_after
from execution.market_data.normalizer import NormalizerConfig, StreamNormalizer
from execution.modes import ExecutionMode, ModeGates
from execution.runtime.session import LiveSession, SessionConfig


def _ist(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> float:
    return datetime(year, month, day, hour, minute, second, tzinfo=IST).timestamp()


FRIDAY = (2026, 10, 16)
MONDAY = (2026, 10, 19)


def _candle(symbol: str, seq: int, timestamp: str, close: float = 100.0) -> CandleEvent:
    return CandleEvent(
        symbol=symbol,
        timestamp=timestamp,
        seq=seq,
        source="test",
        close=close,
        volume=1,
        timeframe="30m",
    )


def test_kernel_candle_window_matches_python_policy() -> None:
    assert candle_stale_after(1_800.0) == 1_980.0
    policy = MarketDataHealthPolicy.for_timeframe("30m")
    assert policy.interval_seconds == 1_800.0
    assert policy.stale_after_seconds == 1_980.0
    assert policy.heartbeat_timeout_seconds == 1_980.0


def test_30_minute_consecutive_candles_are_not_stale() -> None:
    window = candle_stale_after(1_800.0)
    normalizer = StreamNormalizer(
        NormalizerConfig(
            stale_after_seconds=window,
            heartbeat_timeout_seconds=window,
        )
    )
    first = _candle("NSE:A", 1, "2026-10-19T09:15:00+05:30")
    second = _candle("NSE:A", 2, "2026-10-19T09:45:00+05:30")
    assert len(normalizer.observe(first, 0.0)) == 1
    assert normalizer.check_health("NSE:A", 1_800.0) == (True, "ok")
    assert len(normalizer.observe(second, 1_800.0)) == 1
    # 1,800s after delivery is fresh; the documented window is 1,980s.
    assert normalizer.check_health("NSE:A", 3_600.0) == (True, "ok")
    assert normalizer.check_health("NSE:A", 3_780.0) == (True, "ok")
    assert normalizer.check_health("NSE:A", 3_780.1) == (False, "stale market data")


def test_a_recovered_delivery_clears_stale_and_is_delivered() -> None:
    window = candle_stale_after(1_800.0)
    normalizer = StreamNormalizer(
        NormalizerConfig(
            stale_after_seconds=window,
            heartbeat_timeout_seconds=window,
        )
    )
    assert len(normalizer.observe(_candle("NSE:A", 1, "2026-10-19T09:15:00+05:30"), 0.0)) == 1
    assert normalizer.check_health("NSE:A", window + 1.0)[0] is False
    assert normalizer.is_stale("NSE:A") is True
    recovered = _candle("NSE:A", 2, "2026-10-19T10:15:00+05:30")
    delivered = normalizer.observe(recovered, window + 1.0)
    assert [event.seq for event in delivered] == [2]
    assert normalizer.is_stale("NSE:A") is False
    assert normalizer.check_health("NSE:A", window + 1.0) == (True, "ok")


def test_calendar_weekend_holiday_and_session_boundaries() -> None:
    calendar = MarketCalendar(holidays=("2026-10-20",))
    assert calendar.is_open(_ist(*FRIDAY, 9, 15)) is True
    assert calendar.is_open(_ist(*FRIDAY, 15, 29, 59)) is True
    assert calendar.is_open(_ist(*FRIDAY, 15, 30)) is False
    assert calendar.is_open(_ist(*FRIDAY, 9, 14, 59)) is False
    assert calendar.is_open(_ist(2026, 10, 17, 10, 0)) is False  # Saturday
    assert calendar.is_open(_ist(2026, 10, 20, 10, 0)) is False  # holiday
    assert calendar.trading_day(_ist(*MONDAY, 9, 15)).isoformat() == "2026-10-19"


def test_overnight_gap_is_excluded_from_the_expectation_window() -> None:
    calendar = MarketCalendar()
    friday_close_delivery = _ist(*FRIDAY, 15, 15)
    monday_open = _ist(*MONDAY, 9, 15)
    anchor = calendar.expectation_anchor(friday_close_delivery, monday_open)
    assert anchor == monday_open, "Friday's tail must not count against Monday's first candle"
    assert calendar.overdue_seconds(friday_close_delivery, monday_open) == 0.0
    # Monday 09:30 is only 15 minutes into the new expectation window.
    assert calendar.overdue_seconds(friday_close_delivery, _ist(*MONDAY, 9, 30)) == 900.0
    # The session-open clock, not the wall clock, is what health consumes.
    assert calendar.expectation_anchor(friday_close_delivery, _ist(*MONDAY, 8, 0)) is None
    assert calendar.expectation_anchor(friday_close_delivery, _ist(*FRIDAY, 15, 30)) is None


def test_trading_session_seconds_exclude_closed_hours() -> None:
    calendar = MarketCalendar()
    friday_1500 = _ist(*FRIDAY, 15, 0)
    monday_1000 = _ist(*MONDAY, 10, 0)
    # Friday 15:00–15:30 (1,800s) + Monday 09:15–10:00 (2,700s).
    assert calendar.open_seconds_between(friday_1500, monday_1000) == 4_500.0


def test_expected_deadline_uses_today_open_not_yesterday_close() -> None:
    policy = MarketDataHealthPolicy.for_timeframe("30m")
    deadline = policy.expected_deadline(_ist(*FRIDAY, 15, 15), _ist(*MONDAY, 9, 30))
    assert deadline == _ist(*MONDAY, 9, 48)
    assert policy.expected_deadline(_ist(*FRIDAY, 15, 15), _ist(*MONDAY, 8, 0)) is None


def _session(tmp_path: Any) -> LiveSession:
    config = SessionConfig(
        mode=ExecutionMode.BACKTEST,
        gates=ModeGates(),
        journal_path=tmp_path / "journal.jsonl",
        kill_switch_path=tmp_path / "kill.json",
        memory_path=tmp_path / "memory.json",
        session_start="09:15",
        session_end="15:30",
    )
    provider = _Provider()
    return LiveSession(config, provider)


class _Provider:
    name = "test-provider"
    capabilities = ("candle-close",)

    def __init__(self) -> None:
        self.events: list[CandleEvent] = []

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        pass

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        pass

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        pass

    def poll(self) -> tuple[CandleEvent, ...]:
        events = tuple(self.events)
        self.events.clear()
        return events

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def close(self) -> None:
        pass


class _BrokerStub:
    """Minimal venue surface: no orders, no stream events."""

    name = "stub-venue"

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        pass

    def stream_events(self, _consumer_id: str = "") -> tuple[dict[str, Any], ...]:
        return ()


def test_session_rebases_at_open_and_flags_missing_first_candle(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    # Continuous run through Friday close: the daily baseline starts Friday.
    session._sweep_health(_ist(*FRIDAY, 9, 15))
    session._normalizer.observe(
        _candle("NSE:A", 1, "2026-10-16T15:15:00+05:30"), _ist(*FRIDAY, 15, 15)
    )
    session._sweep_health(_ist(*FRIDAY, 15, 20))
    assert session._stale_symbols == set()

    # Monday open rebases to 09:15; Friday's close is not a missing candle.
    session._sweep_health(_ist(*MONDAY, 9, 15))
    session._sweep_health(_ist(*MONDAY, 9, 30))
    assert session._stale_symbols == set()
    # 00:34 into the new window: the first 30m candle is genuinely overdue.
    session._sweep_health(_ist(*MONDAY, 9, 49))
    assert session._stale_symbols == {"NSE:A"}
    kinds = [entry.kind for entry in session.journal.entries]
    assert "STALE_DATA" in kinds


def test_session_rebase_does_not_invent_a_delivery(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    session._sweep_health(_ist(*MONDAY, 9, 15))

    # The rebase is an expectation baseline, not evidence that a candle
    # arrived. Reporting it as last delivery would hide a completely silent
    # feed behind a fabricated timestamp.
    health = session.market_data_health()
    assert health["last_delivery_epoch"] == {}
    assert health["events_delivered"] == 0
    assert health["transport"] == {"healthy": True, "reason": "ok"}
    session._sweep_health(_ist(*MONDAY, 9, 49))
    assert session._stale_symbols == {"NSE:A"}


def test_same_day_session_restart_rebases_the_fresh_kernel(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    session._sweep_health(_ist(*MONDAY, 9, 15))
    # Stop;start on the same trading day rebuilds the kernel. The old
    # per-day marker must not suppress the new kernel's baseline.
    session._configure_health_policy("30m", ("NSE:A",))
    session._sweep_health(_ist(*MONDAY, 9, 15))
    session._sweep_health(_ist(*MONDAY, 9, 49))
    assert session._stale_symbols == {"NSE:A"}


def test_stale_flag_is_not_kept_overnight(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    session._sweep_health(_ist(*FRIDAY, 9, 15))
    session._sweep_health(_ist(*FRIDAY, 15, 20))
    assert session._stale_symbols == {"NSE:A"}
    assert session._normalizer.is_stale("NSE:A") is True

    session._sweep_health(_ist(*FRIDAY, 15, 30))
    assert session._stale_symbols == set()
    assert session._normalizer.is_stale("NSE:A") is False
    kinds = [entry.kind for entry in session.journal.entries]
    assert "STALE_CLEARED" in kinds


def test_session_delivers_the_recovered_candle_and_clears_stale(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    session._normalizer.observe(
        _candle("NSE:A", 1, "2026-10-16T15:15:00+05:30"), _ist(*FRIDAY, 15, 15)
    )
    session._sweep_health(_ist(*MONDAY, 9, 15))
    session._sweep_health(_ist(*MONDAY, 9, 49))
    assert session._stale_symbols == {"NSE:A"}

    session._broker = cast(Any, _BrokerStub())
    provider = session._provider
    assert isinstance(provider, _Provider)
    provider.events.append(_candle("NSE:A", 2, "2026-10-19T09:16:00+05:30", close=101.0))
    delivered = session.step(_ist(*MONDAY, 9, 50))
    assert delivered == 1, "a stale flag must never drop the recovering candle"
    assert session._stale_symbols == set()
    assert session._normalizer.is_stale("NSE:A") is False
    kinds = [entry.kind for entry in session.journal.entries]
    assert "STALE_CLEARED" in kinds


def test_duplicate_candle_never_refreshes_health(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._configure_health_policy("30m", ("NSE:A",))
    session._broker = cast(Any, _BrokerStub())
    provider = session._provider
    assert isinstance(provider, _Provider)
    provider.events.append(_candle("NSE:A", 1, "2026-10-19T09:15:00+05:30"))
    assert session.step(_ist(*MONDAY, 9, 15)) == 1
    assert session.step(_ist(*MONDAY, 9, 20)) == 0
    # The same timestamp is not new evidence; the health clock stays at 09:15.
    provider.events.append(_candle("NSE:A", 2, "2026-10-19T09:15:00+05:30"))
    assert session.step(_ist(*MONDAY, 9, 40)) == 0
    kinds = [entry.kind for entry in session.journal.entries]
    assert "CANDLE_DUPLICATE_SKIPPED" in kinds


def test_heartbeat_fallback_only_before_first_candle() -> None:
    policy = MarketDataHealthPolicy.for_timeframe("30m")
    normalizer = StreamNormalizer(
        NormalizerConfig(
            stale_after_seconds=policy.stale_after_seconds,
            heartbeat_timeout_seconds=policy.heartbeat_timeout_seconds,
        )
    )
    # Before the first candle, a quiet transport is allowed the full candle
    # window; a 30-second tick timeout would flag every 30-minute feed.
    assert policy.heartbeat_timeout_seconds >= policy.interval_seconds
    heartbeat = HeartbeatEvent(
        symbol="NSE:A",
        timestamp="2026-10-19T09:15:00+05:30",
        seq=0,
        source="test",
    )
    normalizer.observe(heartbeat, 0.0)
    assert normalizer.check_health("NSE:A", policy.stale_after_seconds) == (True, "ok")
    assert normalizer.check_health("NSE:A", policy.stale_after_seconds + 0.1)[0] is False
