"""CandleChartWidget viewport tests — right margin, follow-latest, zoom/pan,
touch gestures, trackpad pinch and wheel behaviour.

State-level tests (no pixel grabs — unreliable offscreen).
"""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import (
    QMouseEvent,
    QNativeGestureEvent,
    QPointingDevice,
    QResizeEvent,
    QWheelEvent,
)
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
    widget.resize(1600, 320)
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


def test_new_symbol_shows_latest_initial_bars() -> None:
    widget = _widget(count=2000)
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS
    assert widget._first > 0
    assert widget._visible_range()[1] == 2000
    assert widget._follow_latest
    assert _latest_fraction(widget) <= 1.0 - _MARGIN


def test_new_symbol_large_history_skips_first_candle() -> None:
    widget = _widget(count=10000)
    assert widget._first == widget._anchor_first(10000, CandleChartWidget.INITIAL_BARS)
    assert widget._first > 0
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS
    assert widget._model is not None
    assert widget._model.bars[0].timestamp != widget._model.bars[widget._first].timestamp
    assert _latest_fraction(widget) <= 1.0 - _MARGIN


def test_follow_latest_reanchors_when_new_bars_arrive() -> None:
    widget = _widget(count=2000)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    first_before = widget._first
    widget.set_model(_model(count=2020))
    assert widget._first == first_before + 20
    assert abs(_latest_fraction(widget) - (1.0 - _MARGIN)) < 0.01
    assert widget._follow_latest


def test_manual_pan_keeps_viewport_when_new_bars_arrive() -> None:
    widget = _widget(count=2000)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=350, y=100)
    assert not widget._follow_latest
    first_before = widget._first
    widget.set_model(_model(count=2100))
    assert widget._first == first_before + 100
    assert _latest_fraction(widget) != 1.0 - _MARGIN


def test_switch_symbol_resets_to_latest_initial_bars() -> None:
    widget = _widget(count=500)
    _drag(widget, from_x=250, to_x=350, y=100)
    other = ChartModel(symbol="TCS", bars=_bars(2000), timeframe="15m", exchange="NSE")
    widget.set_model(other)
    assert widget._first == widget._anchor_first(2000, CandleChartWidget.INITIAL_BARS)
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS
    assert widget._follow_latest


def test_timeframe_change_resets_to_latest_initial_bars() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    widget._zoom_at_px(250.0, 0.5)
    zoomed_count = widget._last - widget._first
    assert zoomed_count < CandleChartWidget.INITIAL_BARS
    other = ChartModel(symbol="SPY", bars=_bars(2000), timeframe="1h", exchange="NSE")
    widget.set_model(other)
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS
    assert widget._first == widget._anchor_first(2000, CandleChartWidget.INITIAL_BARS)
    assert widget._follow_latest


def test_same_series_reload_keeps_zoom_window() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    widget._zoom_at_px(250.0, 0.5)
    zoomed_count = widget._last - widget._first
    assert zoomed_count < CandleChartWidget.INITIAL_BARS
    widget.set_model(_model(count=500))
    assert widget._last - widget._first == zoomed_count


def test_initial_view_reaches_older_candles_by_pan_and_zoom() -> None:
    widget = _widget(count=3000)
    initial_first = widget._first
    assert initial_first > 0
    _drag(widget, from_x=250, to_x=10000, y=100)
    assert widget._first == 0
    _drag(widget, from_x=250, to_x=80, y=100)
    widget._zoom_at_px(250.0, 2.0)
    assert widget._last - widget._first > CandleChartWidget.INITIAL_BARS


def test_pan_clamps_at_left_edge() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=4000, y=100)
    assert widget._first == 0


def test_pan_clamps_at_right_margin() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=700, y=100)
    assert widget._first < widget._max_first()
    _drag(widget, from_x=250, to_x=-500, y=100)
    assert widget._first == widget._max_first()


def test_drag_to_right_edge_reengages_follow() -> None:
    widget = _widget(count=2000)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    _drag(widget, from_x=250, to_x=80, y=100)
    assert widget._follow_latest
    first_before = widget._first
    widget.set_model(_model(count=2030))
    assert widget._first == first_before + 30


# ── zoom / pan math ───────────────────────────────────────────────────────


def test_zoom_keeps_bar_under_anchor() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    anchor_x = 800.0
    fraction = anchor_x / widget.width()
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
    first_before = widget._first
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
    assert widget._first == first_before  # no horizontal movement
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
    widget = _widget(count=2000)
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


# ── TradingView-style visible candle density ──────────────────────────


def _density_widget(width: int, count: int = 2000) -> CandleChartWidget:
    """Widget at an explicit viewport width with a large history loaded."""
    _app()
    widget = CandleChartWidget()
    widget.resize(width, 320)
    widget.set_model(_model(count))
    return widget


def test_density_max_visible_matches_geometry() -> None:
    for width in (800, 1200, 1600, 1920):
        widget = _density_widget(width)
        expected = min(width, CandleChartWidget.MAX_VISIBLE_CANDLES)
        assert widget.max_visible_bars() == expected


def test_density_slot_never_below_minimum() -> None:
    for width in (800, 1200, 1600, 1920):
        widget = _density_widget(width)
        assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_initial_view_never_overcompressed() -> None:
    widget = _density_widget(800, count=10000)
    assert widget._last - widget._first == widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_initial_view_shows_1400_on_wide_screen() -> None:
    widget = _density_widget(1600, count=10000)
    assert widget._last - widget._first == 1400
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS


def test_density_extreme_zoom_out_stops_at_minimum() -> None:
    widget = _density_widget(1200, count=10000)
    for _ in range(60):
        widget._zoom_at_px(600.0, 2.0)
    assert widget._last - widget._first == widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_zoom_in_again_after_extreme_out() -> None:
    widget = _density_widget(1200, count=10000)
    for _ in range(60):
        widget._zoom_at_px(600.0, 2.0)
    capped = widget._last - widget._first
    assert capped == widget.max_visible_bars()
    widget._zoom_at_px(600.0, 0.5)
    assert widget._last - widget._first < capped
    assert widget._last - widget._first >= CandleChartWidget.MIN_VISIBLE_BARS
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_zoom_out_keeps_anchor_and_data() -> None:
    widget = _density_widget(1200, count=2000)
    assert widget._model is not None
    first_bar = widget._model.bars[0]
    last_bar = widget._model.bars[-1]
    # move away from the right margin so the margin clamp does not engage
    _drag(widget, from_x=600, to_x=900, y=100)
    anchor_x = 300.0
    fraction = anchor_x / widget.width()
    anchor_before = widget._first + fraction * (widget._last - widget._first)
    widget._zoom_at_px(anchor_x, 1.5)
    anchor_after = widget._first + fraction * (widget._last - widget._first)
    assert round(anchor_after) == round(anchor_before)
    # viewport-only: history fully intact, same bar objects
    assert len(widget._model.bars) == 2000
    assert widget._model.bars[0] is first_bar
    assert widget._model.bars[-1] is last_bar


def test_density_extreme_zoom_out_never_shows_whole_history() -> None:
    widget = _density_widget(1200, count=10000)
    for _ in range(100):
        widget._zoom_at_px(600.0, 5.0)
    assert widget._last - widget._first < 10000
    assert widget._last - widget._first == widget.max_visible_bars()


def test_density_pan_after_zoom_keeps_stable_width() -> None:
    widget = _density_widget(1200, count=2000)
    for _ in range(10):
        widget._zoom_at_px(600.0, 2.0)
    count = widget._last - widget._first
    assert count == widget.max_visible_bars()
    _drag(widget, from_x=600, to_x=700, y=100)
    assert widget._last - widget._first == count
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT
    _drag(widget, from_x=600, to_x=500, y=100)
    assert widget._last - widget._first == count
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_resize_narrower_shrinks_window() -> None:
    widget = _density_widget(1600, count=2000)
    for _ in range(10):
        widget._zoom_at_px(800.0, 2.0)
    assert widget._last - widget._first == widget.max_visible_bars()
    widget.resize(800, 320)
    widget.resizeEvent(QResizeEvent(QSize(800, 320), QSize(1600, 320)))
    assert widget._last - widget._first == widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_resize_wider_keeps_window() -> None:
    widget = _density_widget(800, count=2000)
    initial = widget._last - widget._first
    widget.resize(1920, 320)
    widget.resizeEvent(QResizeEvent(QSize(1920, 320), QSize(800, 320)))
    # widening never adds candles by itself — only the limit grows
    assert widget._last - widget._first == initial
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_small_window_floors_at_min_visible() -> None:
    widget = _density_widget(480, count=2000)
    assert widget.max_visible_bars() >= CandleChartWidget.MIN_VISIBLE_BARS
    assert widget._last - widget._first <= widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT


def test_density_symbol_and_timeframe_switch_within_limit() -> None:
    widget = _density_widget(800, count=10000)
    other = ChartModel(symbol="TCS", bars=_bars(5000), timeframe="15m", exchange="NSE")
    widget.set_model(other)
    assert widget._last - widget._first <= widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT
    assert widget._model is not None
    assert len(widget._model.bars) == 5000  # history intact
    switched = ChartModel(symbol="TCS", bars=_bars(5000), timeframe="1h", exchange="NSE")
    widget.set_model(switched)
    assert widget._last - widget._first <= widget.max_visible_bars()
    assert len(widget._model.bars) == 5000


def test_density_price_range_uses_visible_only() -> None:
    widget = _density_widget(1200, count=2000)
    low, high = widget._price_range()
    first, last = widget._visible_range()
    assert widget._model is not None
    visible = widget._model.bars[first:last]
    assert min(bar.low for bar in visible) >= low
    assert max(bar.high for bar in visible) <= high


def test_density_focus_on_trade_respects_limit() -> None:
    widget = _density_widget(800, count=2000)
    assert widget.focus_on_trade(1500, 1520) is True
    assert widget._last - widget._first <= widget.max_visible_bars()
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT
    # entry bar stays inside the viewport
    assert widget._first <= 1500 < widget._last


# ── hard maximum visible candle limit (1800) ──────────────────────────


def test_hard_max_constant_is_1800() -> None:
    assert CandleChartWidget.MAX_VISIBLE_CANDLES == 1800


def test_hard_max_binds_ultra_wide_viewport() -> None:
    # 12000px / 6px slot = 2000 density — the hard cap must win.
    widget = _density_widget(12000, count=10000)
    assert widget.max_visible_bars() == 1800


def test_hard_max_zoom_out_never_exceeds_1800() -> None:
    widget = _density_widget(12000, count=10000)
    for _ in range(100):
        widget._zoom_at_px(6000.0, 5.0)
    assert widget._last - widget._first == 1800
    assert widget._last - widget._first <= CandleChartWidget.MAX_VISIBLE_CANDLES
    assert widget.candle_slot_width() >= CandleChartWidget.MIN_CANDLE_SLOT
    assert widget._model is not None
    assert len(widget._model.bars) == 10000  # history intact, nothing deleted


def test_hard_max_zoom_in_below_1800() -> None:
    widget = _density_widget(12000, count=10000)
    for _ in range(100):
        widget._zoom_at_px(6000.0, 5.0)
    assert widget._last - widget._first == 1800
    widget._zoom_at_px(6000.0, 0.5)
    assert widget._last - widget._first < 1800
    assert widget._last - widget._first >= CandleChartWidget.MIN_VISIBLE_BARS


def test_hard_max_pan_keeps_1800_window() -> None:
    widget = _density_widget(12000, count=10000)
    for _ in range(100):
        widget._zoom_at_px(6000.0, 5.0)
    assert widget._last - widget._first == 1800
    _drag(widget, from_x=6000, to_x=7000, y=100)
    assert widget._last - widget._first == 1800
    _drag(widget, from_x=6000, to_x=5000, y=100)
    assert widget._last - widget._first == 1800


def test_hard_max_normal_widths_unaffected() -> None:
    # Real screens bind on density first; the hard cap changes nothing there.
    for width in (800, 1200, 1600, 1920):
        widget = _density_widget(width, count=10000)
        assert widget.max_visible_bars() == min(width, CandleChartWidget.MAX_VISIBLE_CANDLES)
