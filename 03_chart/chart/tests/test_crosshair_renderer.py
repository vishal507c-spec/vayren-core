"""CrosshairRenderer tests — pixel-level renderer checks + widget wiring.

Widget pixel grabbing is unreliable in the offscreen Qt platform, so the
widget is verified through state transitions and paintEvent invocation
(mocking CrosshairRenderer.paint to record arguments).
"""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPaintEvent, QPointingDevice
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
from chart.renderer.crosshair_renderer import CrosshairRenderer
from chart.widgets.candle_chart_widget import CandleChartWidget

_BG = (16, 20, 24)
_LINE = (93, 95, 97)  # RGBA(180,180,180,120) blended over _BG, tolerance below


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


def _bars(count: int) -> tuple[Bar, ...]:
    start = datetime(2026, 4, 6, 9, 15, 0)
    return tuple(
        _bar((start + timedelta(seconds=900 * i)).isoformat(sep=" ")) for i in range(count)
    )


def _model(count: int = 200) -> ChartModel:
    return ChartModel(symbol="SPY", bars=_bars(count), timeframe="1d", exchange="NSE")


def _app() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _mouse_event(
    event_type: QEvent.Type,
    x: float,
    y: float,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    device = QPointingDevice.primaryPointingDevice()
    return QMouseEvent(
        event_type,
        QPointF(x, y),
        QPointF(x, y),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
        device,
    )


def _widget() -> CandleChartWidget:
    widget = CandleChartWidget()
    widget.resize(500, 320)
    widget.set_model(_model())
    return widget


def _is_line_pixel(r: int, g: int, b: int) -> bool:
    return all(abs(c - e) <= 2 for c, e in zip((r, g, b), _LINE, strict=False))


def _count_matches(data: bytes) -> int:
    total = 0
    for i in range(0, len(data), 4):
        if _is_line_pixel(data[i + 2], data[i + 1], data[i]):
            total += 1
    return total


def test_paint_draws_full_height_and_full_width_lines() -> None:
    image = QImage(300, 150, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xFF101418)
    painter = QPainter(image)
    CrosshairRenderer.paint(painter, QPoint(100, 60), QRect(0, 0, 300, 150))
    painter.end()
    data = bytes(image.constBits())
    assert _count_matches(data) == 150 + 300 - 2  # intersection is double-blended


def test_paint_is_idempotent_and_reuses_cached_pen() -> None:
    image = QImage(300, 150, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xFF101418)
    painter = QPainter(image)
    rect = QRect(0, 0, 300, 150)
    CrosshairRenderer.paint(painter, QPoint(100, 60), rect)
    painter.end()
    first = bytes(image.constBits())
    image.fill(0xFF101418)
    painter = QPainter(image)
    CrosshairRenderer.paint(painter, QPoint(100, 60), rect)
    painter.end()
    assert bytes(image.constBits()) == first
    assert CrosshairRenderer._pen is not None


def test_widget_tracks_mouse_and_clears_on_leave() -> None:
    _app()
    widget = _widget()
    assert widget.hasMouseTracking()
    assert widget._crosshair_pos is None
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove, 120, 100, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
        )
    )
    assert widget._crosshair_pos == QPoint(120, 100)
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove, 40, 40, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
        )
    )
    assert widget._crosshair_pos == QPoint(40, 40)
    widget.leaveEvent(QEvent(QEvent.Type.Leave))
    assert widget._crosshair_pos is None


def test_paint_renders_crosshair_after_candles() -> None:
    _app()
    widget = _widget()
    calls: list[tuple[QPoint, QRect]] = []
    original = CrosshairRenderer.paint

    def recording_paint(painter: QPainter, position: QPoint, plot_rect: QRect) -> None:
        calls.append((position, plot_rect))
        original(painter, position, plot_rect)

    CrosshairRenderer.paint = staticmethod(recording_paint)
    try:
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 120, 100, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        CrosshairRenderer.paint = original
    chart_rect = widget._chart_rects()[0]
    assert calls == [(QPoint(120, 100), chart_rect)]


def test_paint_skips_crosshair_in_axis_strip() -> None:
    _app()
    widget = _widget()
    calls: list[tuple[QPoint, QRect]] = []
    original = CrosshairRenderer.paint

    def recording_paint(painter: QPainter, position: QPoint, plot_rect: QRect) -> None:
        calls.append((position, plot_rect))
        original(painter, position, plot_rect)

    CrosshairRenderer.paint = staticmethod(recording_paint)
    try:
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 200, 310, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        CrosshairRenderer.paint = original
    assert calls == []


def test_paint_skips_crosshair_without_model() -> None:
    _app()
    widget = CandleChartWidget()
    calls: list[tuple[QPoint, QRect]] = []
    original = CrosshairRenderer.paint

    def recording_paint(painter: QPainter, position: QPoint, plot_rect: QRect) -> None:
        calls.append((position, plot_rect))
        original(painter, position, plot_rect)

    CrosshairRenderer.paint = staticmethod(recording_paint)
    try:
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 100, 100, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        CrosshairRenderer.paint = original
    assert calls == []


def test_pan_still_works_with_crosshair() -> None:
    _app()
    widget = _widget()
    widget.set_model(_model(count=500))
    first_before = widget._first
    widget.mousePressEvent(
        _mouse_event(
            QEvent.Type.MouseButtonPress,
            250,
            100,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove, 300, 100, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
        )
    )
    assert widget._first != first_before
