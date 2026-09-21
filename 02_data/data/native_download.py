"""Rust-backed download kernel bridge (constitution §1: Data).

The trading-day vocabulary, the market-hours window, the target window, the
coverage percent, the scanner verdict, the stored ``candle_time`` shape, the
on-disk filename token, the chunk plan and the queued job ranges are all
decided by Rust (``rust/vayren-core``, ``download`` module).
This module holds no rule of its own: it reads the host clock, converts naive
wall-clock datetimes to the seconds the kernel expects, and marshals the
answer back.

Crossing conventions: ``datetime`` in, ``i64`` naive-as-UTC seconds out (no
timezone interpretation on either side); holidays as one NUL-joined list of
``"YYYY-MM-DD"`` labels; multi-field answers as one-line documents; ``_ABSENT``
stands for Python's ``None`` in a timestamp slot.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_cal_market_open",
    "vy_cal_is_trading_day",
    "vy_cal_count_trading_days",
    "vy_cal_target_start",
    "vy_cal_today_end",
    "vy_dl_chunk_count",
    "vy_dl_chunk_windows",
    "vy_dl_coverage_pct",
    "vy_dl_decide_coverage",
    "vy_dl_head_sweep_eligible",
    "vy_dl_job_rank",
    "vy_dl_job_window",
    "vy_dl_validate_range",
    "vy_sym_filename",
    "vy_ts_candle_time",
    "vy_ts_from_secs",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no download kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )

_EPOCH = datetime(1970, 1, 1)
_SECONDS_PER_DAY = 86400
_ABSENT = -(2**63)  # the kernel's `None` for a timestamp slot


def _secs(value: datetime) -> int:
    """Naive wall-clock seconds: the date as it reads, no timezone maths."""
    local = value.replace(tzinfo=None)
    return (
        (local - _EPOCH).days * _SECONDS_PER_DAY
        + local.hour * 3600
        + local.minute * 60
        + local.second
    )


def _datetime_of(secs: int) -> datetime:
    """Inverse of `_secs`: a plain wall-clock datetime, as the engine stores."""
    return _EPOCH + timedelta(seconds=int(secs))


def _holiday_blob(holidays: Iterable[str]) -> tuple[bytes, int]:
    payload = "\0".join(holidays).encode("utf-8")
    return payload, len(payload)


def _text(value: str | None) -> tuple[bytes | None, int]:
    if value is None:
        return None, -1
    payload = value.encode("utf-8")
    return payload, len(payload)


def market_open(
    now: datetime,
    open_h: int,
    open_m: int,
    close_h: int,
    close_m: int,
) -> bool:
    """True inside the exchange's trading window; the kernel owns the rule."""
    return bool(
        int(
            _lib.vy_cal_market_open(
                _secs(now), int(open_h), int(open_m), int(close_h), int(close_m)
            )
        )
    )


def is_trading_day(day: datetime, holidays: Iterable[str]) -> bool:
    """True on a weekday that is not a listed holiday."""
    payload, length = _holiday_blob(holidays)
    code = int(_lib.vy_cal_is_trading_day(_secs(day), payload, length))
    if code < 0:
        raise NativeBridgeError(f"calendar kernel rejected the holiday set: {code}")
    return bool(code)


def count_trading_days(start: datetime, end: datetime, holidays: Iterable[str]) -> int:
    """Trading days in ``[start, end)``, stepping whole days."""
    payload, length = _holiday_blob(holidays)
    count = int(_lib.vy_cal_count_trading_days(_secs(start), _secs(end), payload, length))
    if count < 0:
        raise NativeBridgeError("calendar kernel rejected the holiday set")
    return count


def target_start(now: datetime, max_history_years: int) -> datetime:
    """Midnight the configured history window opens at."""
    return _datetime_of(int(_lib.vy_cal_target_start(_secs(now), int(max_history_years))))


def today_end(now: datetime) -> datetime:
    """23:59:59 of the caller's day."""
    return _datetime_of(int(_lib.vy_cal_today_end(_secs(now))))


def coverage_pct(trading_days: int, max_history_years: int) -> float:
    """Share of the target history the database already holds, capped at 100."""
    return float(_lib.vy_dl_coverage_pct(int(trading_days), int(max_history_years)))


@dataclass(frozen=True)
class CoverageVerdict:
    """The kernel's answer for one scanned candle database."""

    state: str
    missing_head: bool
    missing_tail: bool
    coverage_pct: float
    listing_start_verified: bool


def decide_coverage(
    row_count: int,
    trading_days: int,
    corrupt: int,
    earliest: datetime | None,
    latest: datetime | None,
    boundary: dict[str, Any] | None,
    target_start_at: datetime,
    today_end_at: datetime,
    max_history_years: int,
    head_tolerance_trading_days: int,
    tail_lag_tolerance_days: int,
    holidays: Iterable[str],
) -> CoverageVerdict:
    """Full coverage verdict: state, head/tail gaps, percent, boundary flag.

    The ``<=`` listing-boundary rule, the tolerance compares and the state
    choice are the kernel's; ``boundary`` travels as the raw stored row.
    """
    payload, length = _holiday_blob(holidays)
    label, label_len = _text(None if boundary is None else str(boundary["boundary_date"]))
    verified = 0 if boundary is None else int(boundary["verified"])

    def invoke(buf: Any, cap: int) -> int:
        return int(
            _lib.vy_dl_decide_coverage(
                int(row_count),
                int(trading_days),
                int(corrupt),
                _ABSENT if earliest is None else _secs(earliest),
                _ABSENT if latest is None else _secs(latest),
                label,
                label_len,
                verified,
                _secs(target_start_at),
                _secs(today_end_at),
                int(max_history_years),
                int(head_tolerance_trading_days),
                int(tail_lag_tolerance_days),
                payload,
                length,
                buf,
                cap,
            )
        )

    needed = invoke(None, 0)
    if needed < 0:
        raise NativeBridgeError(f"coverage kernel rejected the call: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if invoke(buf, needed + 1) != needed:
        raise NativeBridgeError("coverage kernel length drift")
    state, missing_head, missing_tail, pct, verified_flag = buf.raw[:needed].split(b"|")
    return CoverageVerdict(
        state=state.decode("utf-8"),
        missing_head=missing_head == b"1",
        missing_tail=missing_tail == b"1",
        coverage_pct=float(pct),
        listing_start_verified=verified_flag == b"1",
    )


def _read(invoke: Any) -> str:
    """Probe for the length, then fill a buffer; the kernel's two-call form."""
    needed = int(invoke(None, 0))
    if needed < 0:
        raise NativeBridgeError(f"download kernel rejected the call: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if int(invoke(buf, needed + 1)) != needed:
        raise NativeBridgeError("download kernel length drift")
    return buf.raw[:needed].decode("utf-8")


def candle_time(raw: str) -> str:
    """Canonical ``candle_time`` for a provider timestamp as text."""
    payload = raw.encode("utf-8")
    return _read(lambda b, c: _lib.vy_ts_candle_time(payload, len(payload), b, c))


def candle_time_for(moment: datetime) -> str:
    """Canonical ``candle_time`` for a wall-clock datetime."""
    return _read(lambda b, c: _lib.vy_ts_from_secs(_secs(moment), b, c))


def filename_token(symbol: str) -> str:
    """The ``[A-Za-z0-9_]`` token a symbol becomes on disk."""
    payload = symbol.encode("utf-8")
    return _read(lambda b, c: _lib.vy_sym_filename(payload, len(payload), b, c))


def chunk_count(start: datetime, end: datetime, chunk_days: int) -> int:
    """How many chunk windows a sweep will run."""
    return int(_lib.vy_dl_chunk_count(_secs(start), _secs(end), int(chunk_days)))


def chunk_windows(
    start: datetime, end: datetime, chunk_days: int
) -> list[tuple[datetime, datetime]]:
    """The sweep's ``(start, end)`` windows, in order."""
    document = _read(
        lambda b, c: _lib.vy_dl_chunk_windows(_secs(start), _secs(end), int(chunk_days), b, c)
    )
    if not document:
        return []
    return [
        (_datetime_of(int(s)), _datetime_of(int(e)))
        for pair in document.split(";")
        for s, e in [pair.split("|")]
    ]


def head_sweep_eligible(
    new_rows: int, earliest_before: datetime | None, after: datetime | None
) -> bool:
    """May a finished head sweep write the ``LISTING_START`` boundary?"""
    return bool(
        int(
            _lib.vy_dl_head_sweep_eligible(
                int(new_rows),
                _ABSENT if earliest_before is None else _secs(earliest_before),
                _ABSENT if after is None else _secs(after),
            )
        )
    )


@dataclass(frozen=True)
class JobWindow:
    """The range one queued job covers, and why it exists."""

    from_dt: datetime
    to_dt: datetime
    reason: str


def job_window(
    kind: str, bound: datetime | None, target_start_at: datetime, today_end_at: datetime
) -> JobWindow:
    """Range of a ``full``/``head``/``tail`` job; ``bound`` is the observed DB edge."""
    kinds = {"full": 0, "head": 1, "tail": 2}
    if kind not in kinds:
        raise NativeBridgeError(f"unknown job kind: {kind!r}")
    document = _read(
        lambda b, c: _lib.vy_dl_job_window(
            kinds[kind],
            _ABSENT if bound is None else _secs(bound),
            _secs(target_start_at),
            _secs(today_end_at),
            b,
            c,
        )
    )
    from_secs, to_secs, reason = document.split("|")
    return JobWindow(
        from_dt=_datetime_of(int(from_secs)), to_dt=_datetime_of(int(to_secs)), reason=reason
    )


@dataclass(frozen=True)
class RangeWindow:
    """The window two `"YYYY-MM-DD"` bounds cover, or why they cover nothing."""

    from_dt: datetime | None
    to_dt: datetime | None
    reason: str


def validate_range(from_date: str, to_date: str) -> RangeWindow:
    """End-of-day window for two date bounds; ``reason`` says why there is none."""
    start, start_len = _text(from_date)
    end, end_len = _text(to_date)
    document = _read(lambda b, c: _lib.vy_dl_validate_range(start, start_len, end, end_len, b, c))
    ok, from_secs, to_secs, reason = document.split("|")
    if ok == "1":
        return RangeWindow(_datetime_of(int(from_secs)), _datetime_of(int(to_secs)), reason)
    return RangeWindow(None, None, reason)


def job_rank(state: str) -> int:
    """Queue rank of a download state's spelling; the lower rank runs first."""
    payload, length = _text(state)
    rank = int(_lib.vy_dl_job_rank(payload, length))
    if rank < 0:
        raise NativeBridgeError("download kernel could not read the state name")
    return rank


__all__ = [
    "CoverageVerdict",
    "RangeWindow",
    "JobWindow",
    "candle_time",
    "candle_time_for",
    "chunk_count",
    "chunk_windows",
    "coverage_pct",
    "count_trading_days",
    "decide_coverage",
    "filename_token",
    "head_sweep_eligible",
    "is_trading_day",
    "job_rank",
    "job_window",
    "market_open",
    "target_start",
    "today_end",
    "validate_range",
]
