"""Calendar helpers — IST time, market hours and NSE trading days.

Preserved verbatim from the original engine (TIME / CALENDAR HELPERS), now
parameterised by ``DownloadSettings`` instead of module-level constants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(tz=IST)


def is_market_open(settings) -> bool:
    """True inside NSE market hours (Mon–Fri, open..close IST).

    NOTE: the close time 12:40 IST is preserved exactly as the original
    engine had it — a documented quirk of that engine, not a change.
    """
    n = ist_now()
    if n.weekday() >= 5:
        return False
    mo = n.replace(
        hour=settings.market_open_h, minute=settings.market_open_m, second=0, microsecond=0
    )
    mc = n.replace(
        hour=settings.market_close_h, minute=settings.market_close_m, second=0, microsecond=0
    )
    return mo <= n <= mc


def is_trading_day(d: datetime, settings) -> bool:
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in settings.holidays


def target_start_dt(settings) -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=settings.max_history_years * 365
    )


def today_end_dt() -> datetime:
    return datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)


def count_trading_days(d1: datetime, d2: datetime, settings) -> int:
    count, cur = 0, d1
    while cur < d2:
        if is_trading_day(cur, settings):
            count += 1
        cur += timedelta(days=1)
    return count


def dt_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")
