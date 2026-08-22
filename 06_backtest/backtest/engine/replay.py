"""Backtest time-range filtering and window slicing."""

from market.models.bar import Bar


def slice_bars(
    bars: tuple[Bar, ...],
    start_date: str | None,
    end_date: str | None,
) -> tuple[Bar, ...]:
    """Return the bars whose timestamp date falls within [start, end].

    ``start_date`` and ``end_date`` are ISO date strings (``YYYY-MM-DD``).
    A None bound means open-ended. The input is already ascending, so the
    output preserves that order. Empty input returns empty.
    """
    if not bars:
        return ()
    if start_date is None and end_date is None:
        return bars
    filtered: list[Bar] = []
    for bar in bars:
        day = bar.timestamp[:10]
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        filtered.append(bar)
    return tuple(filtered)
