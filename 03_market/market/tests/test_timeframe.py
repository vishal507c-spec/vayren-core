"""Timeframe ladder helpers tests."""

from typing import Any

import pytest

from market.native_timeframe import fetch_plan
from market.native_timeframe import ladder as _kernel_ladder
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


def test_ladder_vocabulary_comes_from_the_kernel() -> None:
    assert _kernel_ladder() == TIMEFRAME_LADDER


def test_ladder_lookup_is_case_insensitive() -> None:
    assert timeframe_seconds("1M") == 60
    assert timeframe_seconds("1d") == 86400
    assert timeframe_seconds("2w") == 1_209_600


def test_label_must_be_a_string() -> None:
    bad_labels: tuple[Any, ...] = (None, 900, b"15m")
    for bad in bad_labels:
        with pytest.raises(TypeError):
            timeframe_seconds(bad)


def test_rust_int_grammar_is_stricter_than_python_int() -> None:
    # The old Python parser used int(), which tolerated padding and digit
    # separators. The kernel rejects both — a malformed label resolves to None
    # instead of a granularity no database can produce.
    assert timeframe_seconds(" 15m ") is None
    assert timeframe_seconds("1_5m") is None
    assert timeframe_seconds("15m\u00b2") is None


def test_generated_labels_round_trip_through_the_kernel() -> None:
    for seconds in (120, 5_400, 172_800, 1_209_600, 45):
        label = generate_label(seconds)
        assert timeframe_seconds(label) == seconds
        assert timeframe_name(seconds) in (None, label)


# ── higher-timeframe query plan (frozen pre-migration rule as oracle) ──────


def _ref_plan(label: str, base: int | None, limit: int | None) -> tuple[Any, ...]:
    """Verbatim copy of the old `get_candles_timeframe` decision (test-only)."""
    seconds = timeframe_seconds(label)
    if seconds is None or base is None or seconds <= base:
        return (True, seconds or 0, None, None)
    ratio = seconds // base
    if limit is None:
        return (False, seconds, None, None)
    return (False, seconds, (limit + 1) * ratio, None if limit == 0 else limit)


def _plan_tuple(label: str, base: int | None, limit: int | None) -> tuple[Any, ...]:
    plan = fetch_plan(label, base, limit)
    return (plan.plain, plan.seconds, plan.row_budget, plan.keep_last)


def test_plan_at_or_below_base_reads_rows_as_stored() -> None:
    assert _plan_tuple("15m", 900, 10) == (True, 900, None, None)
    assert _plan_tuple("5m", 900, 10) == (True, 300, None, None)
    assert _plan_tuple("garbage", 60, 10) == (True, 0, None, None)
    assert _plan_tuple("1h", None, 10) == (True, 3600, None, None)


def test_plan_over_fetches_one_bucket_and_keeps_the_tail() -> None:
    assert _plan_tuple("1h", 900, 3) == (False, 3600, 16, 3)
    assert _plan_tuple("1D", 60, 500) == (False, 86_400, 721_440, 500)
    assert _plan_tuple("30m", 900, None) == (False, 1800, None, None)
    # The bars[-0:] quirk: a zero limit cuts no tail but still over-fetches.
    assert _plan_tuple("30m", 900, 0) == (False, 1800, 2, None)


def test_unusable_base_duration_falls_back_instead_of_dividing() -> None:
    # The old rule divided by the detected base, so a 0 base raised
    # ZeroDivisionError; the kernel treats it as "nothing to aggregate".
    assert _plan_tuple("1h", 0, 5) == (True, 3600, None, None)


def test_fetch_plan_fuzz() -> None:
    labels = list(TIMEFRAME_LADDER) + [generate_label(seconds) for seconds in (45, 120, 5_400)]
    bases = [None, 1, 5, 15, 60, 300, 900, 3_600]
    limits = [None, 0, 1, 7, 500, 4_096]
    for label in labels:
        for base in bases:
            if base is not None and base <= 0:
                continue
            for limit in limits:
                expected = _ref_plan(label, base, limit)
                assert _plan_tuple(label, base, limit) == expected, (label, base, limit)
