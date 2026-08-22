"""Chart overlay hook — verifies the extension point without breaking rendering.

State + direct-painter verification only: offscreen ``render()`` /
``sendPostedEvents`` paths are the documented Windows access-violation
triggers in this repo (see ai_memory Known Oddities).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market.models.bar import Bar
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
from chart.models.chart_viewport import ChartViewport
from chart.widgets.candle_chart_widget import CandleChartWidget


def _qt_app() -> QApplication:
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication([])


def _bars(n: int = 20) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol="TEST",
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=1000,
            timestamp=f"2026-01-{i + 1:02d} 09:15:00",
        )
        for i in range(n)
    )


class _RecordingOverlay:
    """Minimal ChartOverlay double: records paint calls."""

    def __init__(self) -> None:
        self.calls: list[ChartViewport] = []

    def paint_overlay(self, painter: object, viewport: ChartViewport) -> None:
        _ = painter
        self.calls.append(viewport)


def test_overlay_installed_and_cleared() -> None:
    _qt_app()
    widget = CandleChartWidget()
    assert widget.overlay is None
    overlay = _RecordingOverlay()
    widget.set_overlay(overlay)
    assert widget.overlay is overlay
    widget.set_overlay(None)
    assert widget.overlay is None


def test_overlay_paint_receives_viewport_snapshot() -> None:
    _qt_app()
    widget = CandleChartWidget()
    widget.resize(400, 300)
    model = ChartModel(symbol="TEST", bars=_bars(20), timeframe="15m", exchange="NSE")
    widget.set_model(model)
    overlay = _RecordingOverlay()
    widget.set_overlay(overlay)

    chart_rect, volume_rect, axis_rect = widget._chart_rects()
    pixmap = QPixmap(widget.size())
    painter = QPainter(pixmap)
    try:
        widget._paint_strategy_overlay(
            painter, chart_rect, volume_rect, axis_rect, 90.0, 130.0, 1000
        )
    finally:
        painter.end()

    assert len(overlay.calls) == 1
    viewport = overlay.calls[0]
    assert viewport.bars == model.bars
    assert viewport.chart_rect == chart_rect


def test_overlay_none_short_circuits_painting() -> None:
    _qt_app()
    widget = CandleChartWidget()
    widget.resize(400, 300)
    widget.set_model(ChartModel(symbol="TEST", bars=_bars(5), timeframe="15m", exchange="NSE"))
    chart_rect, volume_rect, axis_rect = widget._chart_rects()
    pixmap = QPixmap(widget.size())
    painter = QPainter(pixmap)
    try:
        widget._paint_strategy_overlay(
            painter, chart_rect, volume_rect, axis_rect, 90.0, 130.0, 1000
        )
    finally:
        painter.end()
