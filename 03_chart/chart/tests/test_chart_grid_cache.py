"""CandleChartWidget grid-cache tests — the static grid is cached and only
repainted when the viewport or price span actually changes."""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPaintEvent, QPixmap, QPointingDevice
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
from chart.renderer.candle_renderer import CandleRenderer
from chart.widgets.candle_chart_widget import CandleChartWidget

_KEEP_APP: QCoreApplication | None = None


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
    stamps = ((start + timedelta(seconds=900 * i)).isoformat(sep=" ") for i in range(count))
    return tuple(_bar(timestamp) for timestamp in stamps)


def _trending_bars(count: int) -> tuple[Bar, ...]:
    """Bars whose price drifts up, so each viewport window has its own span."""

    start = datetime(2026, 4, 6, 9, 15, 0)
    bars: list[Bar] = []
    for i in range(count):
        stamp = (start + timedelta(seconds=900 * i)).isoformat(sep=" ")
        base = 100.0 + i * 0.5
        bars.append(
            Bar(
                symbol="SPY",
                open=base,
                high=base + 2.0,
                low=base - 2.0,
                close=base,
                volume=1000,
                timestamp=stamp,
            )
        )
    return tuple(bars)


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _widget() -> CandleChartWidget:
    widget = CandleChartWidget()
    widget.resize(500, 320)
    widget.set_model(ChartModel(symbol="SPY", bars=_bars(500), timeframe="15m", exchange="NSE"))
    return widget


def test_grid_painted_once_across_static_repaints(
    monkeypatch,
) -> None:
    _app()
    widget = _widget()
    calls: list[object] = []
    original = CandleRenderer.paint_grid

    def recording(painter, rect, price_low, price_high) -> None:
        calls.append((rect, price_low, price_high))
        original(painter, rect, price_low, price_high)

    monkeypatch.setattr(CandleRenderer, "paint_grid", staticmethod(recording))
    widget.paintEvent(QPaintEvent(widget.rect()))
    widget.paintEvent(QPaintEvent(widget.rect()))
    assert len(calls) == 1


def test_crosshair_move_reuses_grid_cache(monkeypatch) -> None:
    _app()
    widget = _widget()
    calls: list[object] = []
    original = CandleRenderer.paint_grid

    def recording(painter, rect, price_low, price_high) -> None:
        calls.append(rect)
        original(painter, rect, price_low, price_high)

    monkeypatch.setattr(CandleRenderer, "paint_grid", staticmethod(recording))
    widget.paintEvent(QPaintEvent(widget.rect()))
    device = QPointingDevice.primaryPointingDevice()
    widget.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(120, 100),
            QPointF(120, 100),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            device,
        )
    )
    widget.paintEvent(QPaintEvent(widget.rect()))
    assert len(calls) == 1


def test_zoom_invalidates_grid_cache(monkeypatch) -> None:
    _app()
    widget = CandleChartWidget()
    widget.resize(500, 320)
    widget.set_model(
        ChartModel(symbol="SPY", bars=_trending_bars(500), timeframe="15m", exchange="NSE")
    )
    calls: list[object] = []
    original = CandleRenderer.paint_grid

    def recording(painter, rect, price_low, price_high) -> None:
        calls.append((rect, price_low, price_high))
        original(painter, rect, price_low, price_high)

    monkeypatch.setattr(CandleRenderer, "paint_grid", staticmethod(recording))
    widget.paintEvent(QPaintEvent(widget.rect()))
    widget._zoom_at_px(250.0, 0.5)
    widget.paintEvent(QPaintEvent(widget.rect()))
    assert len(calls) == 2
    assert widget._grid_cache is not None
    assert isinstance(widget._grid_cache, QPixmap)


def test_set_model_invalidates_grid_cache(monkeypatch) -> None:
    _app()
    widget = _widget()
    calls: list[object] = []
    real_grid = CandleRenderer.paint_grid

    def recording(painter, rect, price_low, price_high) -> None:
        calls.append(1)
        real_grid(painter, rect, price_low, price_high)

    monkeypatch.setattr(CandleRenderer, "paint_grid", staticmethod(recording))
    widget.paintEvent(QPaintEvent(widget.rect()))
    widget.set_model(ChartModel(symbol="SPY", bars=_bars(250), timeframe="15m", exchange="NSE"))
    widget.paintEvent(QPaintEvent(widget.rect()))
    assert len(calls) == 2
