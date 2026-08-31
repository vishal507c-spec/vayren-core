"""TimeAxisRenderer — stateless QPainter painting of the date/time axis.

Draws TradingView-style adaptive tick labels below the candles: tick
spacing and label format are chosen from the visible time span, ticks
are aligned to calendar boundaries, and labels never overlap.

Pure painting: no state, no SQL, no events. Only the X-axis.
"""

from datetime import datetime, timedelta
from math import ceil

from market.models.bar import Bar
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QPainter, QPen

from chart.renderer.candle_renderer import CandleRenderer

_TICK_LADDER = (
    60,
    120,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    10800,
    21600,
    43200,
    86400,
    172800,
    259200,
    604800,
    1209600,
    2592000,
    7776000,
    15552000,
    31536000,
)

_FORMAT_BY_STEP = (
    (86400, "%H:%M"),
    (3 * 86400, "%d %b"),
    (21 * 86400, "%b"),
    (180 * 86400, "%b %Y"),
    (float("inf"), "%Y"),
)

_SECONDS_PER_DAY = 86400
_MONTH_AVG_DAYS = 30


class TimeAxisRenderer:
    """Draws the bottom time axis: grid ticks aligned to calendar boundaries.

    Tick step is picked so labels keep at least `MIN_LABEL_PX` apart;
    the format tier follows the step (intraday → daily → weekly → yearly).
    All tick math is O(visible ticks) with binary search over bars, so it
    stays cheap even with millions of candles.
    """

    MIN_LABEL_PX = 96.0
    LABEL_PAD_PX = 12.0
    AXIS_FONT_SIZE = 8

    BACKGROUND = CandleRenderer.BACKGROUND
    GRID = CandleRenderer.GRID
    TEXT = CandleRenderer.TEXT

    _FONT = QFont("Segoe UI", AXIS_FONT_SIZE)
    _grid_pen = QPen(GRID, 1)
    _text_pen = QPen(TEXT, 1)

    @staticmethod
    def select_step(avg_seconds: float, slot_px: float) -> int:
        """Return the smallest tick step (seconds) with spacing >= MIN_LABEL_PX."""
        if slot_px <= 0.0 or avg_seconds <= 0.0:
            return _TICK_LADDER[-1]
        required = avg_seconds * max(1.0, ceil(TimeAxisRenderer.MIN_LABEL_PX / slot_px))
        for step in _TICK_LADDER:
            if step >= required:
                return step
        return _TICK_LADDER[-1]

    @staticmethod
    def format_for_step(step: int) -> str:
        """Return the strftime format tier for a tick step (seconds)."""
        for max_step, fmt in _FORMAT_BY_STEP:
            if step < max_step:
                return fmt
        return "%Y"

    @staticmethod
    def tick_times(first_dt: datetime, last_dt: datetime, step: int) -> list[datetime]:
        """Generate calendar-aligned tick times covering [first_dt, last_dt]."""
        if step < _SECONDS_PER_DAY:
            midnight = first_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_since_midnight = (first_dt - midnight).total_seconds()
            start = first_dt - timedelta(seconds=seconds_since_midnight % step)
            ticks: list[datetime] = []
            tick = start
            while tick <= last_dt:
                ticks.append(tick)
                tick += timedelta(seconds=step)
            return ticks
        if step <= 14 * _SECONDS_PER_DAY:
            base = datetime(first_dt.year, first_dt.month, first_dt.day)
            days = int(step // _SECONDS_PER_DAY)
            ticks = []
            while base <= last_dt:
                ticks.append(base)
                base += timedelta(days=days)
            return ticks
        if step <= 180 * _SECONDS_PER_DAY:
            months = int(step / (_MONTH_AVG_DAYS * _SECONDS_PER_DAY))
            year, month = first_dt.year, first_dt.month
            ticks = []
            while datetime(year, month, 1) <= last_dt:
                ticks.append(datetime(year, month, 1))
                month += months
                year += (month - 1) // 12
                month = (month - 1) % 12 + 1
            return ticks
        ticks = [datetime(year, 1, 1) for year in range(first_dt.year, last_dt.year + 1)]
        return [t for t in ticks if first_dt <= t <= last_dt]

    @staticmethod
    def paint(
        painter: QPainter,
        bars: tuple[Bar, ...],
        first: int,
        last: int,
        axis_rect: QRect,
    ) -> None:
        """Draw the time axis for the visible bar window.

        `last` is the right window slot and may exceed `len(bars)` (the
        right-side margin); bars are clamped internally, the slot math keeps
        ticks aligned with the candles.
        """
        count = last - first
        bar_last = min(last, len(bars))
        if count <= 1 or bar_last <= first or axis_rect.isEmpty():
            return
        slot_width = axis_rect.width() / count
        if slot_width <= 0.0:
            return

        first_dt, last_dt = TimeAxisRenderer._parse_times(bars, first, bar_last)
        if first_dt is None or last_dt is None or last_dt <= first_dt:
            return

        avg_seconds = (last_dt - first_dt).total_seconds() / (bar_last - first)
        step = TimeAxisRenderer.select_step(avg_seconds, slot_width)
        fmt = TimeAxisRenderer.format_for_step(step)

        painter.save()
        painter.setFont(TimeAxisRenderer._FONT)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(axis_rect, TimeAxisRenderer.BACKGROUND)

        painter.setPen(TimeAxisRenderer._grid_pen)
        painter.drawLine(axis_rect.left(), axis_rect.top(), axis_rect.right(), axis_rect.top())

        last_x = axis_rect.left() - 10000.0
        for tick in TimeAxisRenderer.tick_times(first_dt, last_dt, step):
            index = TimeAxisRenderer._nearest_bar(bars, first, bar_last, tick)
            if index is None:
                continue
            center_x = axis_rect.left() + (index - first + 0.5) * slot_width
            label = tick.strftime(fmt)
            text_width = (
                painter.fontMetrics().horizontalAdvance(label) + TimeAxisRenderer.LABEL_PAD_PX
            )
            min_gap = max(TimeAxisRenderer.MIN_LABEL_PX, text_width)
            if center_x - last_x < min_gap:
                continue

            painter.setPen(TimeAxisRenderer._grid_pen)
            painter.drawLine(int(center_x), axis_rect.top(), int(center_x), axis_rect.bottom())
            painter.setPen(TimeAxisRenderer._text_pen)
            label_rect = QRect(
                int(center_x) - int(text_width) // 2,
                axis_rect.top(),
                int(text_width),
                axis_rect.height(),
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
            last_x = center_x

        painter.restore()

    @staticmethod
    def _nearest_bar(bars: tuple[Bar, ...], first: int, last: int, tick: datetime) -> int | None:
        """Return the visible bar index closest to `tick`, or None if out of range."""
        last = min(last, len(bars))
        lo, hi = first, last - 1
        if lo > hi:
            return None
        tick_str = tick.isoformat(sep=" ")
        if tick_str <= bars[lo].timestamp:
            return lo
        if tick_str >= bars[hi].timestamp:
            return hi
        left, right = first, last
        while left < right:
            mid = (left + right) // 2
            if bars[mid].timestamp < tick_str:
                left = mid + 1
            else:
                right = mid
        candidate = left
        if candidate <= first:
            return first
        if candidate >= last:
            return last - 1
        try:
            prev_ts = bars[candidate - 1].timestamp
            next_ts = bars[candidate].timestamp
            dist_before = (tick - datetime.fromisoformat(prev_ts)).total_seconds()
            dist_after = (datetime.fromisoformat(next_ts) - tick).total_seconds()
        except (ValueError, TypeError):
            return candidate
        return candidate - 1 if dist_before < dist_after else candidate

    @staticmethod
    def _parse_times(
        bars: tuple[Bar, ...], first: int, last: int
    ) -> tuple[datetime | None, datetime | None]:
        """Parse the first and last visible timestamps (cheap, two parses only)."""
        try:
            first_dt = datetime.fromisoformat(bars[first].timestamp)
            last_dt = datetime.fromisoformat(bars[last - 1].timestamp)
        except (ValueError, TypeError):
            return None, None
        return first_dt, last_dt
