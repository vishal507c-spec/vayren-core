"""Timeframe ladder helpers tests."""

from market.timeframe.timeframe import (
    TIMEFRAME_LADDER,
    available_timeframes,
    generate_label,
    timeframe_name,
    timeframe_seconds,
)


def test_ladder_seconds_lookup() -> None:
    assert timeframe_seconds("1m") == 60
    assert timeframe_seconds("15m") == 900
    assert timeframe_seconds("1h") == 3600
    assert timeframe_seconds("1D") == 86400
    assert timeframe_seconds("1W") == 604800
    assert timeframe_seconds("nonsense") is None


def test_generated_labels_parse_back() -> None:
    assert timeframe_seconds("2m") == 120
    assert timeframe_seconds("90m") == 5400
    assert timeframe_seconds("2D") == 172800
    assert timeframe_seconds("2W") == 1209600
    assert timeframe_seconds("45s") == 45
    assert timeframe_seconds("x5m") is None


def test_ladder_is_ascending_and_unique() -> None:
    seconds = [timeframe_seconds(name) for name in TIMEFRAME_LADDER]
    assert all(value is not None for value in seconds)
    values = [value for value in seconds if value is not None]
    assert len(values) == len(set(values))
    assert values == sorted(values)


def test_timeframe_name() -> None:
    assert timeframe_name(900) == "15m"
    assert timeframe_name(604800) == "1W"
    assert timeframe_name(120) is None


def test_generate_label() -> None:
    assert generate_label(120) == "2m"
    assert generate_label(5400) == "90m"
    assert generate_label(172800) == "2D"
    assert generate_label(1209600) == "2W"
    assert generate_label(45) == "45s"


def test_available_15m_base() -> None:
    assert available_timeframes(900) == (
        "15m",
        "30m",
        "45m",
        "1h",
        "2h",
        "4h",
        "1D",
        "1W",
    )


def test_available_minute_base() -> None:
    assert available_timeframes(60) == TIMEFRAME_LADDER


def test_available_5m_base() -> None:
    assert available_timeframes(300) == ("5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W")


def test_available_daily_base() -> None:
    assert available_timeframes(86400) == ("1D", "1W")


def test_available_non_ladder_base_is_generated() -> None:
    assert available_timeframes(120) == ("2m", "30m", "1h", "2h", "4h", "1D", "1W")
    assert available_timeframes(5400) == ("90m", "1D", "1W")


def test_available_invalid_base_is_empty() -> None:
    assert available_timeframes(0) == ()
    assert available_timeframes(-900) == ()
