"""OverlayRenderer + LabelRenderer tests — timestamp formatting + paint smoke."""

from market.models.bar import Bar
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from chart.models.crosshair_value import CrosshairValue
from chart.renderer.label_renderer import LabelRenderer
from chart.renderer.overlay_renderer import OverlayRenderer


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


def _value(timestamp: str) -> CrosshairValue:
    bar = _bar(timestamp)
    return CrosshairValue(
        bar_index=0,
        price=bar.close,
        timestamp=bar.timestamp,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
    )


def _app() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is not None:
        return app  # type: ignore[return-value]
    return QGuiApplication([])


def test_format_intraday_single_line() -> None:
    ts = "2026-08-04 10:15:00"
    formatted = OverlayRenderer._format_timestamp(ts, "30m")
    assert "Aug" in formatted
    assert "10:15" in formatted
    assert formatted.count("\n") == 0


def test_format_timestamp_iso_separator() -> None:
    ts = "2026-08-04T10:15:00"
    formatted = OverlayRenderer._format_timestamp(ts, "1h")
    assert "Aug" in formatted
    assert "10:15" in formatted


def test_format_timestamp_garbage_returns_raw() -> None:
    ts = "not-a-date"
    assert OverlayRenderer._format_timestamp(ts, "1d") == "not-a-date"


def test_format_daily_shows_date_only() -> None:
    ts = "2026-08-04 10:15:00"
    formatted = OverlayRenderer._format_timestamp(ts, "1d")
    assert "\n" not in formatted
    assert "Aug" in formatted


def test_format_weekly_shows_week_and_year_single_line() -> None:
    ts = "2026-08-04 10:15:00"
    formatted = OverlayRenderer._format_timestamp(ts, "1w")
    assert "Week" in formatted
    assert "2026" in formatted
    assert formatted.count("\n") == 0


def test_format_monthly_shows_month_and_year() -> None:
    ts = "2026-08-04 10:15:00"
    formatted = OverlayRenderer._format_timestamp(ts, "1mo")
    assert "Aug" in formatted
    assert "2026" in formatted


def test_format_intraday_shows_time() -> None:
    ts = "2026-08-04 13:00:00"
    formatted = OverlayRenderer._format_timestamp(ts, "15m")
    assert "13:00" in formatted
    assert formatted.count("\n") == 0


def test_paint_symbol_info_smoke() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    OverlayRenderer.paint_symbol_info(painter, "TCS", "30m", "NSE", QRect(0, 0, 400, 24))
    painter.end()
    assert not image.isNull()


def test_paint_ohlc_smoke() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    OverlayRenderer.paint_ohlc(painter, _bar("2026-08-04 10:15:00"), QRect(0, 0, 400, 24))
    painter.end()
    assert not image.isNull()


def test_paint_price_smoke() -> None:
    _app()
    image = QImage(100, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    chart_rect = QRect(0, 0, 100, 200)
    OverlayRenderer.paint_price(painter, _value("2026-08-04 10:15:00"), 100, chart_rect)
    painter.end()
    assert not image.isNull()


def test_paint_time_centers_on_crosshair_in_left_half() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    axis_rect = QRect(0, 0, 400, 24)
    captured: list[float] = []
    original = LabelRenderer.paint_centered

    def recording(_p, _text, _font, center_x: float, *_args, **_kwargs) -> QRect:
        captured.append(center_x)
        return original(_p, _text, _font, center_x, *_args, **_kwargs)

    LabelRenderer.paint_centered = staticmethod(recording)  # type: ignore[assignment]
    try:
        OverlayRenderer.paint_time(painter, _value("2026-08-04 10:15:00"), "1h", 20, axis_rect)
    finally:
        LabelRenderer.paint_centered = staticmethod(original)  # type: ignore[assignment]
        painter.end()
    assert captured == [20.0]


def test_paint_time_clamps_label_within_axis_bounds() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    axis_rect = QRect(0, 0, 400, 24)
    rect = OverlayRenderer.paint_time(painter, _value("2026-08-04 10:15:00"), "1h", 5, axis_rect)
    painter.end()
    assert rect.left() >= axis_rect.left()
    assert rect.right() <= axis_rect.right()


def test_paint_time_smoke() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    OverlayRenderer.paint_time(
        painter, _value("2026-08-04 10:15:00"), "1h", 200, QRect(0, 0, 400, 24)
    )
    painter.end()
    assert not image.isNull()


def test_label_renderer_returns_rect() -> None:
    _app()
    image = QImage(200, 50, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    fm = painter.fontMetrics()
    text = "Hello"
    w = fm.horizontalAdvance(text)
    rect = LabelRenderer.paint(painter, text, painter.font(), QRectF(200 - w - 12, 0, 80, 24))
    painter.end()
    assert rect.width() > 0
    assert rect.height() > 0
