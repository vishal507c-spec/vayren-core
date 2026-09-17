"""CandleChartWidget crosshair snap + overlay labels integration tests.

Widget pixel grabbing is unreliable offscreen, so we verify state and the
recording of renderer calls rather than pixels.
"""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent, QPaintEvent, QPointingDevice
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
from chart.renderer.crosshair_renderer import CrosshairRenderer
from chart.renderer.overlay_renderer import OverlayRenderer
from chart.widgets.candle_chart_widget import CandleChartWidget


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


def _hours(count: int) -> tuple[Bar, ...]:
    start = datetime(2026, 4, 8, 9, 15, 0)
    return tuple(_bar((start + timedelta(hours=i)).isoformat(sep=" ")) for i in range(count))


def _model(count: int = 200) -> ChartModel:
    return ChartModel(symbol="SPY", bars=_hours(count), timeframe="1h", exchange="NSE")


def _app() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _mouse_event(
    event_type: QEvent.Type,
    x: float,
    y: float,
    button: Qt.MouseButton = Qt.MouseButton.NoButton,
    buttons: Qt.MouseButton = Qt.MouseButton.NoButton,
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


def test_snap_to_nearest_candle() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    center_x = chart_rect.left() + slot_width / 2.0
    bar_x = center_x + slot_width
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, chart_rect.center().y()))
    assert widget._crosshair_value is not None
    expected_index = widget._first + 1
    assert widget._crosshair_value.bar_index == expected_index


def test_crosshair_price_interpolated_from_y() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    center_x = chart_rect.left() + slot_width / 2.0
    bar_x = center_x
    y_top = chart_rect.top()
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, y_top))
    value = widget._crosshair_value
    assert value is not None
    assert value.price > 0


def test_leave_clears_crosshair_and_value() -> None:
    _app()
    widget = _widget()
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove, 200, 100, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
        )
    )
    assert widget._crosshair_pos is not None
    assert widget._crosshair_value is not None
    widget.leaveEvent(QEvent(QEvent.Type.Leave))
    assert widget._crosshair_pos is None
    assert widget._crosshair_value is None


def test_crosshair_value_has_ohlc_fields() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    bar_x = chart_rect.left() + slot_width / 2.0
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, chart_rect.center().y()))
    value = widget._crosshair_value
    assert value is not None
    assert widget._model is not None
    bar = widget._model.bars[value.bar_index]
    assert value.open == bar.open
    assert value.high == bar.high
    assert value.low == bar.low
    assert value.close == bar.close
    assert value.timestamp == bar.timestamp


def test_paint_renders_overlay_labels() -> None:
    _app()
    widget = _widget()
    symbol_calls: list = []
    ohlc_calls: list = []
    price_calls: list = []
    time_calls: list = []
    crosshair_calls: list = []
    orig_symbol = OverlayRenderer.paint_symbol_info
    orig_ohlc = OverlayRenderer.paint_ohlc
    orig_price = OverlayRenderer.paint_price
    orig_time = OverlayRenderer.paint_time
    orig_crosshair = CrosshairRenderer.paint

    def rec_symbol(*args):
        symbol_calls.append(args)
        return orig_symbol(*args)

    def rec_ohlc(*args, **kwargs):
        ohlc_calls.append((args, kwargs))
        return orig_ohlc(*args, **kwargs)

    def rec_price(*args):
        price_calls.append(args)
        return orig_price(*args)

    def rec_time(*args):
        time_calls.append(args)
        return orig_time(*args)

    def rec_crosshair(*args) -> None:
        crosshair_calls.append(args)
        orig_crosshair(*args)

    OverlayRenderer.paint_symbol_info = staticmethod(rec_symbol)  # type: ignore[assignment]
    OverlayRenderer.paint_ohlc = staticmethod(rec_ohlc)  # type: ignore[assignment]
    OverlayRenderer.paint_price = staticmethod(rec_price)  # type: ignore[assignment]
    OverlayRenderer.paint_time = staticmethod(rec_time)  # type: ignore[assignment]
    CrosshairRenderer.paint = staticmethod(rec_crosshair)  # type: ignore[assignment]
    try:
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 250, 150, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(orig_symbol)  # type: ignore[assignment]
        OverlayRenderer.paint_ohlc = staticmethod(orig_ohlc)  # type: ignore[assignment]
        OverlayRenderer.paint_price = staticmethod(orig_price)  # type: ignore[assignment]
        OverlayRenderer.paint_time = staticmethod(orig_time)  # type: ignore[assignment]
        CrosshairRenderer.paint = staticmethod(orig_crosshair)  # type: ignore[assignment]
    assert len(crosshair_calls) == 1
    assert len(symbol_calls) == 1
    assert len(ohlc_calls) == 1
    assert len(price_calls) == 1
    assert len(time_calls) == 1


def test_header_painted_without_crosshair() -> None:
    _app()
    widget = _widget()
    symbol_calls: list = []
    original = OverlayRenderer.paint_symbol_info

    def recording(*args):
        symbol_calls.append(args)
        return original(*args)

    OverlayRenderer.paint_symbol_info = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(original)  # type: ignore[assignment]
    assert len(symbol_calls) == 1


def test_header_painted_immediately_after_model_load() -> None:
    _app()
    widget = _widget()
    symbol_calls: list = []
    original = OverlayRenderer.paint_symbol_info

    def recording(*args):
        symbol_calls.append(args)
        return original(*args)

    OverlayRenderer.paint_symbol_info = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(original)  # type: ignore[assignment]
    assert len(symbol_calls) == 1
    symbol, timeframe, exchange = symbol_calls[0][1], symbol_calls[0][2], symbol_calls[0][3]
    assert symbol == "SPY"
    assert timeframe == "1h"
    assert exchange == "NSE"


def test_header_shows_latest_bar_ohlc() -> None:
    _app()
    widget = _widget()
    ohlc_calls: list = []
    original = OverlayRenderer.paint_ohlc

    def recording(_painter, bar, _top_bar, **_kwargs) -> None:
        ohlc_calls.append(bar)
        original(_painter, bar, _top_bar, **_kwargs)

    OverlayRenderer.paint_ohlc = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_ohlc = staticmethod(original)  # type: ignore[assignment]
    assert len(ohlc_calls) == 1
    assert widget._model is not None
    assert ohlc_calls[0] is widget._model.bars[-1]


def test_header_updates_when_model_changes() -> None:
    _app()
    widget = _widget()
    widget.set_model(ChartModel(symbol="TCS", bars=_hours(90), timeframe="30m", exchange="NSE"))
    symbol_calls: list = []
    ohlc_calls: list = []
    orig_symbol = OverlayRenderer.paint_symbol_info
    orig_ohlc = OverlayRenderer.paint_ohlc

    def rec_symbol(*args):
        symbol_calls.append(args)
        return orig_symbol(*args)

    def rec_ohlc(_painter, bar, _top_bar, **_kwargs) -> None:
        ohlc_calls.append(bar)
        orig_ohlc(_painter, bar, _top_bar, **_kwargs)

    OverlayRenderer.paint_symbol_info = staticmethod(rec_symbol)  # type: ignore[assignment]
    OverlayRenderer.paint_ohlc = staticmethod(rec_ohlc)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(orig_symbol)  # type: ignore[assignment]
        OverlayRenderer.paint_ohlc = staticmethod(orig_ohlc)  # type: ignore[assignment]
    assert len(symbol_calls) == 1
    assert symbol_calls[0][1] == "TCS"
    assert symbol_calls[0][2] == "30m"
    assert symbol_calls[0][3] == "NSE"
    assert len(ohlc_calls) == 1
    assert widget._model is not None
    assert ohlc_calls[0] is widget._model.bars[-1]


def test_header_independent_of_crosshair_visibility() -> None:
    _app()
    widget = _widget()
    symbol_calls: list = []
    original = OverlayRenderer.paint_symbol_info

    def recording(*args):
        symbol_calls.append(args)
        return original(*args)

    OverlayRenderer.paint_symbol_info = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 250, 150, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
        widget.leaveEvent(QEvent(QEvent.Type.Leave))
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(original)  # type: ignore[assignment]
    assert len(symbol_calls) == 3


def test_no_overlays_without_model() -> None:
    _app()
    widget = CandleChartWidget()
    widget.resize(500, 320)
    symbol_calls: list = []
    original = OverlayRenderer.paint_symbol_info

    def recording(*args) -> None:
        symbol_calls.append(args)
        original(*args)

    OverlayRenderer.paint_symbol_info = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove, 200, 100, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton
            )
        )
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_symbol_info = staticmethod(original)  # type: ignore[assignment]
    assert symbol_calls == []


def test_price_label_y_aligns_with_crosshair() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    bar_x = chart_rect.left() + slot_width / 2.0
    crosshair_y = chart_rect.center().y()
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, crosshair_y))
    price_calls: list = []
    orig = OverlayRenderer.paint_price

    def recording(_p, _v, y: int, _r) -> None:
        price_calls.append(y)
        orig(_p, _v, y, _r)

    OverlayRenderer.paint_price = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_price = staticmethod(orig)  # type: ignore[assignment]
    assert len(price_calls) == 1
    assert price_calls[0] == crosshair_y


def test_time_label_x_centers_on_crosshair() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    bar_x = chart_rect.left() + slot_width / 2.0
    crosshair_y = chart_rect.center().y()
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, crosshair_y))
    time_calls: list = []
    orig = OverlayRenderer.paint_time

    def recording(_p, _v, _tf, x: int, _a) -> None:
        time_calls.append(x)
        orig(_p, _v, _tf, x, _a)

    OverlayRenderer.paint_time = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_time = staticmethod(orig)  # type: ignore[assignment]
    assert len(time_calls) == 1
    assert widget._crosshair_pos is not None
    assert time_calls[0] == widget._crosshair_pos.x()


def test_ohlc_positioned_in_top_bar() -> None:
    _app()
    widget = _widget()
    chart_rect = widget._chart_rects()[0]
    slot_width = chart_rect.width() / (widget._last - widget._first)
    bar_x = chart_rect.left() + slot_width / 2.0
    widget.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, bar_x, chart_rect.center().y()))
    ohlc_calls: list = []
    orig = OverlayRenderer.paint_ohlc

    def recording(_p, _b, top_bar: QRect, **kwargs) -> None:
        ohlc_calls.append(top_bar)
        orig(_p, _b, top_bar, **kwargs)

    OverlayRenderer.paint_ohlc = staticmethod(recording)  # type: ignore[assignment]
    try:
        widget.paintEvent(QPaintEvent(widget.rect()))
    finally:
        OverlayRenderer.paint_ohlc = staticmethod(orig)  # type: ignore[assignment]
    assert len(ohlc_calls) == 1
    top_bar = ohlc_calls[0]
    assert top_bar.top() == chart_rect.top()
    assert top_bar.height() == CandleChartWidget.SYMBOL_HEIGHT


def test_query_visible_includes_spanning_ray_past_left_edge() -> None:
    """OBR left-edge parity: a RAY anchored just before the viewport whose
    extension reaches into the window must be returned even when a MARKER
    record is interleaved at a slightly higher anchor. The store's spanning
    scan must skip point plots, not terminate on them (the Slint view culls
    the same way; both renderers must agree at the left edge).
    """
    _app()
    from strategy.models.plot_event import (
        MarkerType,
        PlotEvent,
        PlotLifecycle,
        PlotType,
        RenderLayer,
        make_event_id,
    )

    from chart.renderer.plot_renderer import PlotOverlay

    def _ev(bar: int, plot_id: str, plot_type: PlotType, price: float, marker=None, ext=None):
        return PlotEvent(
            event_id=make_event_id("OBR", "X", "30m", bar, plot_id),
            source_strategy="OBR",
            plot_id=plot_id,
            plot_type=plot_type,
            symbol="X",
            timeframe="30m",
            bar_index=bar if plot_type is PlotType.MARKER else None,
            start_bar=bar if plot_type is PlotType.RAY else None,
            price=price if plot_type is PlotType.MARKER else None,
            start_price=price if plot_type is PlotType.RAY else None,
            marker_type=marker,
            lifecycle=PlotLifecycle.ACTIVE,
            layer=RenderLayer.MARKER,
            extend_bars=ext,
        )

    # MARKER at 89 (a point plot just below the window) and a RAY at 88 whose
    # 5-bar extension covers [88, 92] — it reaches into the [90, 100) window.
    events = (
        _ev(88, "ref_high", PlotType.RAY, 100.0, ext=5),
        _ev(89, "buy_89", PlotType.MARKER, 101.0, marker=MarkerType.UP_ARROW),
        _ev(95, "eod_95", PlotType.MARKER, 102.0, marker=MarkerType.TRIANGLE_BLUE),
    )
    overlay = PlotOverlay()
    overlay.ingest_plot_events(events)
    visible = overlay._store.query_visible(90, 100)
    by_id = {r.plot_id: r for r in visible}
    assert "eod_95" in by_id, "marker inside the window must be present"
    ref = by_id.get("ref_high")
    assert ref is not None, (
        "ray anchored at 88 with extend_bars=5 covers bar 92 and must be "
        "returned for the [90,100) window (left-edge sliver parity)"
    )
    assert ref.plot_type == "RAY"  # store normalizes to uppercase strings
    assert ref.covered == (88, 92)
