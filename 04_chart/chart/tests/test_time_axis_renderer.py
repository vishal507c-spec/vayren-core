"""TimeAxisRenderer tests — pure tick math + paint smoke."""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from chart.renderer.time_axis_renderer import TimeAxisRenderer


def _bar(timestamp: str) -> Bar:
    return Bar(
        symbol="SPY",
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
        timestamp=timestamp,
    )


def _bars(start: datetime, count: int, seconds: int) -> tuple[Bar, ...]:
    return tuple(
        _bar((start + timedelta(seconds=seconds * i)).isoformat(sep=" ")) for i in range(count)
    )


def test_select_step_grows_with_zoom_out() -> None:
    assert TimeAxisRenderer.select_step(avg_seconds=900, slot_px=200.0) == 900
    assert TimeAxisRenderer.select_step(avg_seconds=900, slot_px=1.0) > 900
    assert TimeAxisRenderer.select_step(avg_seconds=900, slot_px=0.0) == 31536000


def test_select_step_is_calendar_friendly() -> None:
    assert TimeAxisRenderer.select_step(avg_seconds=86400, slot_px=200.0) == 86400
    step = TimeAxisRenderer.select_step(avg_seconds=86400, slot_px=8.0)
    assert step in (1209600, 2592000, 7776000, 15552000, 31536000)


def test_format_ladder_matches_zoom_level() -> None:
    assert TimeAxisRenderer.format_for_step(900) == "%H:%M"
    assert TimeAxisRenderer.format_for_step(3600) == "%H:%M"
    assert TimeAxisRenderer.format_for_step(86400) == "%d %b"
    assert TimeAxisRenderer.format_for_step(172800) == "%d %b"
    assert TimeAxisRenderer.format_for_step(259200) == "%b"
    assert TimeAxisRenderer.format_for_step(604800) == "%b"
    assert TimeAxisRenderer.format_for_step(1209600) == "%b"
    assert TimeAxisRenderer.format_for_step(2592000) == "%b %Y"
    assert TimeAxisRenderer.format_for_step(7776000) == "%b %Y"
    assert TimeAxisRenderer.format_for_step(15552000) == "%Y"
    assert TimeAxisRenderer.format_for_step(31536000) == "%Y"


def test_tick_times_intraday_aligned_to_local_clock() -> None:
    first = datetime(2026, 4, 8, 9, 15, 0)
    last = datetime(2026, 4, 8, 11, 0, 0)
    ticks = TimeAxisRenderer.tick_times(first, last, 900)
    assert ticks[0] == datetime(2026, 4, 8, 9, 15, 0)
    assert [t.minute % 15 for t in ticks] == [0] * len(ticks)
    assert ticks[-1] <= last


def test_tick_times_intraday_step_not_multiple_of_bar() -> None:
    first = datetime(2026, 4, 8, 9, 15, 0)
    last = datetime(2026, 4, 8, 18, 0, 0)
    ticks = TimeAxisRenderer.tick_times(first, last, 10800)
    assert ticks[0] == datetime(2026, 4, 8, 9, 0, 0)
    assert ticks[1] == datetime(2026, 4, 8, 12, 0, 0)


def test_tick_times_daily_aligned_to_midnight() -> None:
    first = datetime(2026, 4, 8, 9, 15, 0)
    last = datetime(2026, 4, 10, 15, 30, 0)
    ticks = TimeAxisRenderer.tick_times(first, last, 86400)
    assert ticks == [datetime(2026, 4, 8), datetime(2026, 4, 9), datetime(2026, 4, 10)]


def test_tick_times_monthly_aligned_to_first_of_month() -> None:
    first = datetime(2026, 1, 15, 9, 15, 0)
    last = datetime(2026, 5, 20, 15, 30, 0)
    ticks = TimeAxisRenderer.tick_times(first, last, 2592000)
    assert ticks == [datetime(2026, m, 1) for m in range(1, 6)]


def test_tick_times_yearly_aligned_to_jan_first() -> None:
    first = datetime(2024, 3, 1, 9, 15, 0)
    last = datetime(2026, 2, 1, 9, 15, 0)
    ticks = TimeAxisRenderer.tick_times(first, last, 31536000)
    assert ticks == [datetime(2025, 1, 1), datetime(2026, 1, 1)]


def test_ticks_never_overlap_for_visible_window() -> None:
    bars = _bars(datetime(2026, 4, 6, 9, 15, 0), count=4000, seconds=900)
    first, last = 0, 4000
    slot = 10.0
    span = datetime.fromisoformat(bars[last - 1].timestamp) - datetime.fromisoformat(
        bars[first].timestamp
    )
    avg = span.total_seconds() / (last - first)
    step = TimeAxisRenderer.select_step(avg, slot)
    start_dt = datetime.fromisoformat(bars[first].timestamp)
    end_dt = datetime.fromisoformat(bars[last - 1].timestamp)
    ticks = TimeAxisRenderer.tick_times(start_dt, end_dt, step)
    centers = []
    for tick in ticks:
        index = TimeAxisRenderer._nearest_bar(bars, first, last, tick)
        if index is not None:
            centers.append((index - first + 0.5) * slot)
    gaps = [b - a for a, b in zip(centers, centers[1:], strict=False)]
    assert gaps and all(g >= TimeAxisRenderer.MIN_LABEL_PX for g in gaps)


def test_paint_smoke_with_qimage() -> None:
    app = QGuiApplication.instance()
    if app is None:
        QGuiApplication([])
    image = QImage(1000, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xFF101418)
    painter = QPainter(image)
    bars = _bars(datetime(2026, 4, 6, 9, 15, 0), count=500, seconds=900)
    TimeAxisRenderer.paint(painter, bars, 0, len(bars), QRect(0, 0, 1000, 24))
    TimeAxisRenderer.paint(painter, bars, 0, 0, QRect(0, 0, 1000, 24))
    painter.end()
    assert not image.isNull()


def test_paint_ignores_unparseable_timestamps() -> None:
    app = QGuiApplication.instance()
    if app is None:
        QGuiApplication([])
    image = QImage(1000, 24, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    bars = (_bar("garbage"), _bar("2026-04-08 10:00:00"))
    TimeAxisRenderer.paint(painter, bars, 0, 2, QRect(0, 0, 1000, 24))
    painter.end()
    assert not image.isNull()
