"""Calendar helpers — IST time, market hours and NSE trading days.

The rules live in the Rust data kernel (``rust/vayren-core``, ``download``
module) and reach Python through ``data.native_download``: the trading-day
vocabulary, the inclusive market-hours window, the history window and the
trading-day count are all the kernel's. What stays here is reading the host
clock — the one input the kernel is not allowed to sample itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from data.native_download import count_trading_days as _kernel_count
from data.native_download import is_trading_day as _kernel_is_trading_day
from data.native_download import market_open as _kernel_market_open
from data.native_download import target_start as _kernel_target_start
from data.native_download import today_end as _kernel_today_end

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(tz=IST)


def is_market_open(settings) -> bool:
    """True inside NSE market hours (Mon–Fri, open..close IST).

    The window's own seconds already fall outside it — the kernel keeps the
    quirk the original engine had, including the 12:40 IST close.
    """
    return _kernel_market_open(
        ist_now(),
        settings.market_open_h,
        settings.market_open_m,
        settings.market_close_h,
        settings.market_close_m,
    )


def is_trading_day(d: datetime, settings) -> bool:
    """Weekday and not a listed holiday, decided on the kernel's calendar."""
    return _kernel_is_trading_day(d, settings.holidays)


def target_start_dt(settings) -> datetime:
    """Midnight the configured history window opens at."""
    return _kernel_target_start(datetime.now(), settings.max_history_years)


def today_end_dt() -> datetime:
    """End of today as the engine records it: 23:59:59."""
    return _kernel_today_end(datetime.now())


def count_trading_days(d1: datetime, d2: datetime, settings) -> int:
    """Trading days in ``[d1, d2)``, stepped whole days by the kernel."""
    return _kernel_count(d1, d2, settings.holidays)
