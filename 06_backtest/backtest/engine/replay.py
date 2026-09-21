"""Backtest window slicing — the range rule is Rust's, the bars are Python's."""

from market import Bar

from backtest.native_replay import window as _native_window


def slice_bars(
    bars: tuple[Bar, ...],
    start_date: str | None,
    end_date: str | None,
) -> tuple[Bar, ...]:
    """Return the bars whose timestamp date falls within [start, end].

    ``start_date`` and ``end_date`` are ISO date strings (``YYYY-MM-DD``); a
    None bound means open-ended. Which bars survive is decided by
    `backtest_engine::slice_indices`; input order is preserved.
    """
    kept = _native_window([bar.timestamp for bar in bars], start_date, end_date)
    return tuple(bars[index] for index in kept)
