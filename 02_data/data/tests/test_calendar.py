"""Calendar — IST, market hours, trading days."""

from datetime import datetime

from data.calendar import (
    IST,
    count_trading_days,
    is_market_open,
    is_trading_day,
    target_start_dt,
    today_end_dt,
)
from data.tests.conftest import make_settings

DEFAULT_WINDOW = {
    "market_open_h": 9,
    "market_open_m": 15,
    "market_close_h": 12,
    "market_close_m": 40,
}


def test_ist_offset_is_fixed() -> None:
    assert IST.utcoffset(None).total_seconds() == 5 * 3600 + 30 * 60


def test_market_open_during_hours(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "data.calendar.ist_now",
        lambda: datetime(2026, 8, 10, 10, 0, tzinfo=IST),  # Monday
    )
    assert is_market_open(make_settings(tmp_path, **DEFAULT_WINDOW))


def test_market_closed_outside_hours(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path, **DEFAULT_WINDOW)
    monkeypatch.setattr("data.calendar.ist_now", lambda: datetime(2026, 8, 10, 13, 0, tzinfo=IST))
    assert not is_market_open(settings)
    monkeypatch.setattr("data.calendar.ist_now", lambda: datetime(2026, 8, 10, 8, 0, tzinfo=IST))
    assert not is_market_open(settings)


def test_market_closed_on_weekend(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "data.calendar.ist_now",
        lambda: datetime(2026, 8, 15, 10, 0, tzinfo=IST),  # Saturday
    )
    assert not is_market_open(make_settings(tmp_path, **DEFAULT_WINDOW))


def test_trading_day_skips_weekends_and_holidays(tmp_path) -> None:
    settings = make_settings(tmp_path)
    monday = datetime(2026, 8, 10)
    saturday = datetime(2026, 8, 15)
    holiday = datetime(2026, 1, 26)
    assert is_trading_day(monday, settings)
    assert not is_trading_day(saturday, settings)
    assert not is_trading_day(holiday, settings)


def test_count_trading_days_counts_only_trading_days(tmp_path) -> None:
    settings = make_settings(tmp_path)
    # 2026-08-10 (Mon) .. 2026-08-14 (Fri) = 5 trading days
    count = count_trading_days(datetime(2026, 8, 10), datetime(2026, 8, 15), settings)
    assert count == 5


def test_target_start_is_max_history_years_back(tmp_path) -> None:
    settings = make_settings(tmp_path, max_history_years=2)
    target = target_start_dt(settings)
    assert target.hour == 0 and target.minute == 0


def test_today_end_is_end_of_day() -> None:
    end = today_end_dt()
    assert end.hour == 23 and end.minute == 59 and end.second == 59
