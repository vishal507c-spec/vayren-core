"""CandleChartWidget viewport tests — right margin, follow-latest, zoom/pan,
touch gestures, trackpad pinch and wheel behaviour.

State-level tests (no pixel grabs — unreliable offscreen).
"""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
from chart.widgets.candle_chart_widget import CandleChartWidget

_MARGIN = CandleChartWidget.RIGHT_MARGIN_FRACTION

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


def _model(count: int = 200) -> ChartModel:
    return ChartModel(symbol="SPY", bars=_bars(count), timeframe="15m", exchange="NSE")


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _widget(count: int = 200) -> CandleChartWidget:
    _app()
    widget = CandleChartWidget()
    widget.resize(500, 320)
    widget.set_model(_model(count))
    return widget


def _trailing(widget: CandleChartWidget, window_bars: int) -> None:
    """Zoom the widget into a `window_bars`-bar trailing window (older engine)."""
    assert widget._model is not None
    total = len(widget._model.bars or ())
    count = min(int(window_bars), total)
    widget._first = widget._anchor_first(total, count)
    widget._last = widget._first + count
    widget._follow_latest = True


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


def _drag(widget: CandleChartWidget, from_x: float, to_x: float, y: float = 100.0) -> None:
    widget.mousePressEvent(
        _mouse_event(
            QEvent.Type.MouseButtonPress,
            from_x,
            y,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove, to_x, y, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            to_x,
            y,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )


def _latest_fraction(widget: CandleChartWidget) -> float:
    assert widget._model is not None
    total = len(widget._model.bars or ())
    count = widget._last - widget._first
    return (total - 0.5 - widget._first) / count


# ── BUG 4: right margin + follow latest ───────────────────────────────────


def test_new_symbol_viewport_spans_entire_history() -> None:
    widget = _widget(count=500)
    assert widget._first == 0
    assert widget._last - widget._first >= 500
    assert widget._follow_latest
    assert _latest_fraction(widget) <= 1.0 - _MARGIN


def test_new_symbol_large_history_shows_first_candle() -> None:
    widget = _widget(count=10000)
    assert widget._first == 0
    assert widget._visible_range() == (0, 10000)
    assert widget._model is not None
    assert widget._model.bars[0].timestamp == datetime(2026, 4, 6, 9, 15, 0).isoformat(sep=" ")


def test_follow_latest_reanchors_when_new_bars_arrive() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    first_before = widget._first
    widget.set_model(_model(count=520))
    assert widget._first == first_before + 20
    assert _latest_fraction(widget) == 1.0 - _MARGIN
    assert widget._follow_latest


def test_manual_pan_keeps_viewport_when_new_bars_arrive() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=350, y=100)
    assert not widget._follow_latest
    first_before = widget._first
    widget.set_model(_model(count=600))
    assert widget._first == first_before + 100
    assert _latest_fraction(widget) != 1.0 - _MARGIN


def test_switch_symbol_resets_viewport() -> None:
    widget = _widget(count=500)
    _drag(widget, from_x=250, to_x=350, y=100)
    other = ChartModel(symbol="TCS", bars=_bars(300), timeframe="15m", exchange="NSE")
    widget.set_model(other)
    assert widget._first == 0
    assert widget._last - widget._first >= 300
    assert widget._follow_latest


def test_pan_clamps_at_left_edge() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=1500, y=100)
    assert widget._first == 0


def test_pan_clamps_at_right_margin() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=700, y=100)
    assert widget._first < widget._max_first()
    _drag(widget, from_x=250, to_x=-500, y=100)
    assert widget._first == widget._max_first()


def test_drag_to_right_edge_reengages_follow() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=80, y=100)
    assert widget._follow_latest
    first_before = widget._first
    widget.set_model(_model(count=530))
    assert widget._first == first_before + 30


# ── zoom / pan math ───────────────────────────────────────────────────────


def test_zoom_keeps_bar_under_anchor() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    anchor_x = 125.0
    fraction = anchor_x / 500.0
    anchor_bar_before = widget._first + fraction * (widget._last - widget._first)
    widget._zoom_at_px(anchor_x, 0.5)
    anchor_bar_after = widget._first + fraction * (widget._last - widget._first)
    assert round(anchor_bar_after) == round(anchor_bar_before)


def test_zoom_clamps_to_min_visible_bars() -> None:
    widget = _widget(count=500)
    for _ in range(50):
        widget._zoom_at_px(250.0, 0.5)
    assert widget._last - widget._first == CandleChartWidget.MIN_VISIBLE_BARS


def test_wheel_vertical_zooms_in() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    wheel = QWheelEvent(
        QPointF(250, 100),
        QPointF(250, 100),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(wheel)
    assert widget._last - widget._first < count_before


def test_wheel_horizontal_pans() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    first_before = widget._first
    wheel = QWheelEvent(
        QPointF(250, 100),
        QPointF(250, 100),
        QPoint(0, 0),
        QPoint(120, 0),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(wheel)
    assert widget._first != first_before


# ── BUG 3: gestures ───────────────────────────────────────────────────────


def test_native_pinch_zoom_gesture() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    device = QPointingDevice.primaryPointingDevice()
    gesture = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        0,
        QPointF(250, 100),
        QPointF(250, 100),
        QPointF(250, 100),
        0.2,
        QPointF(0, 0),
    )
    assert widget.event(gesture) is True
    assert widget._last - widget._first < count_before


def test_native_gesture_zero_value_is_ignored() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    device = QPointingDevice.primaryPointingDevice()
    gesture = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        0,
        QPointF(250, 100),
        QPointF(250, 100),
        QPointF(250, 100),
        0.0,
        QPointF(0, 0),
    )
    widget.event(gesture)
    assert widget._last - widget._first == count_before


def test_touch_one_finger_moves_crosshair() -> None:
    widget = _widget(count=500)
    widget._touch_points = {1: QPointF(120, 100)}
    widget._handle_touch_points()
    assert widget._crosshair_pos == QPoint(120, 100)


def test_touch_two_fingers_pan() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    first_before = widget._first
    widget._touch_points = {1: QPointF(100, 100), 2: QPointF(200, 100)}
    widget._handle_touch_points()
    assert widget._crosshair_pos is None
    widget._touch_points = {1: QPointF(110, 100), 2: QPointF(210, 100)}
    widget._handle_touch_points()
    assert widget._first != first_before


def test_touch_pinch_zooms() -> None:
    widget = _widget(count=500)
    widget._touch_points = {1: QPointF(100, 100), 2: QPointF(200, 100)}
    widget._handle_touch_points()
    count_before = widget._last - widget._first
    widget._touch_points = {1: QPointF(90, 100), 2: QPointF(210, 100)}
    widget._handle_touch_points()
    assert widget._last - widget._first < count_before


def test_touch_release_resets_state() -> None:
    widget = _widget(count=500)
    widget._touch_points = {1: QPointF(100, 100), 2: QPointF(200, 100)}
    widget._handle_touch_points()
    widget._touch_points.clear()
    widget._handle_touch_points()
    assert widget._touch_centroid is None
    assert widget._touch_dist is None


# ── Phase 5C: price scale interaction ───────────────────────────────────────


def _price_span(widget: CandleChartWidget) -> float:
    low, high = widget._price_range()
    return high - low


def test_wheel_over_price_strip_zooms_price_only() -> None:
    widget = _widget(count=500)
    first_before, last_before = widget._first, widget._last
    span_before = _price_span(widget)
    x = widget.width() - 20
    wheel_up = QWheelEvent(
        QPointF(x, 60),
        QPointF(x, 60),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(wheel_up)
    assert widget._price_manual is not None
    assert _price_span(widget) < span_before
    assert widget._first == first_before and widget._last == last_before


def test_wheel_over_price_strip_zoom_out_expands_range() -> None:
    widget = _widget(count=500)
    span_before = _price_span(widget)
    x = widget.width() - 2
    wheel_down = QWheelEvent(
        QPointF(x, 60),
        QPointF(x, 60),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(wheel_down)
    assert _price_span(widget) > span_before


def test_wheel_over_plot_zooms_time_not_price() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    wheel_up = QWheelEvent(
        QPointF(150, 60),
        QPointF(150, 60),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(wheel_up)
    assert widget._price_manual is None
    assert widget._last - widget._first < count_before


def test_double_click_price_strip_resets_auto_scale() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    widget._zoom_price_at(60.0, 0.5)
    assert widget._price_manual is not None
    span_manual = _price_span(widget)
    dbl = _mouse_event(
        QEvent.Type.MouseButtonDblClick,
        widget.width() - 20,
        60.0,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    widget.mouseDoubleClickEvent(dbl)
    assert widget._price_manual is None
    low, high = widget._price_range()
    first, last = widget._visible_range()
    assert widget._model is not None
    bars = widget._model.bars[first:last]
    assert min(bar.low for bar in bars) >= low
    assert max(bar.high for bar in bars) <= high
    assert _price_span(widget) > span_manual
    assert widget._last - widget._first == count_before


def test_drag_price_strip_expands_and_compresses() -> None:
    widget = _widget(count=500)
    first_before = widget._first
    x = widget.width() - 20
    span_auto = _price_span(widget)
    widget.mousePressEvent(
        _mouse_event(
            QEvent.Type.MouseButtonPress,
            x,
            100,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove,
            x,
            220,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            x,
            220,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    assert _price_span(widget) > span_auto
    assert widget._first == first_before
    widget._reset_price_scale()
    widget.mousePressEvent(
        _mouse_event(
            QEvent.Type.MouseButtonPress,
            x,
            60,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QEvent.Type.MouseMove,
            x,
            10,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            x,
            10,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    assert _price_span(widget) < span_auto
    assert widget._first == first_before


def test_auto_price_scale_fits_visible_candles_with_margin() -> None:
    widget = _widget(count=200)
    low, high = widget._price_range()
    span = high - low
    assert span > 0.0
    first, last = widget._visible_range()
    assert widget._model is not None
    bars = widget._model.bars[first:last]
    assert min(bar.low for bar in bars) >= low
    assert max(bar.high for bar in bars) <= high
    assert (high - max(bar.high for bar in bars)) > 0.0
    assert (min(bar.low for bar in bars) - low) > 0.0


def test_price_reset_leaves_every_visible_candle_inside() -> None:
    widget = _widget(count=500)
    widget._zoom_price_at(60.0, 4.0)
    widget._reset_price_scale()
    low, high = widget._price_range()
    first, last = widget._visible_range()
    assert widget._model is not None
    bars = widget._model.bars[first:last]
    assert min(bar.low for bar in bars) >= low
    assert max(bar.high for bar in bars) <= high


# ── TradingView-style free panning ──────────────────────────────────────


def test_drag_vertical_pans_price_keeps_span_and_zoom() -> None:
    widget = _widget(count=500)
    count_before = widget._last - widget._first
    low_before, high_before = widget._price_range()
    span_before = high_before - low_before
    # Drag straight down by 100 px -> content follows -> price range shifts up.
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
            QEvent.Type.MouseMove, 250, 200, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            250,
            200,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    low_after, high_after = widget._price_range()
    assert widget._price_manual is not None
    assert low_after > low_before
    assert high_after > high_before
    assert high_after - low_after == span_before  # zoom (span) unchanged
    assert widget._first == 0  # no horizontal movement
    assert widget._last - widget._first == count_before  # time zoom unchanged


def test_drag_horizontal_only_keeps_price_auto_scale() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
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
            QEvent.Type.MouseMove, 350, 100, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            350,
            100,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    assert widget._first != 0
    assert widget._price_manual is None  # no vertical movement -> auto-fit stays


def test_drag_diagonal_pans_both_axes() -> None:
    widget = _widget(count=500)
    span_before = _price_span(widget)
    widget._first = 64
    widget._last = 64 + CandleChartWidget.INITIAL_BARS
    count_before = widget._last - widget._first
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
            QEvent.Type.MouseMove, 150, 60, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QEvent.Type.MouseButtonRelease,
            150,
            60,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    assert widget._first > first_before  # horizontal pan right
    assert widget._last - widget._first == count_before  # time zoom unchanged
    assert widget._price_manual is not None
    assert _price_span(widget) == span_before  # vertical pan, zoom unchanged


def test_touch_two_fingers_pans_vertically() -> None:
    widget = _widget(count=500)
    span_before = _price_span(widget)
    low_before, _ = widget._price_range()
    widget._touch_points = {1: QPointF(100, 100), 2: QPointF(200, 100)}
    widget._handle_touch_points()
    widget._touch_points = {1: QPointF(100, 130), 2: QPointF(200, 130)}
    widget._handle_touch_points()
    low_after, _ = widget._price_range()
    assert widget._price_manual is not None
    assert low_after > low_before
    assert _price_span(widget) == span_before
