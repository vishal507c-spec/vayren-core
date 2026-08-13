"""CandleChartWidget context menu tests — right-click menu, single reset
action, Alt+R shortcut, viewport-only reset.

State-level tests (no pixel grabs — unreliable offscreen).
"""

from datetime import datetime, timedelta

from market.models.bar import Bar
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QContextMenuEvent,
    QKeySequence,
    QMouseEvent,
    QPointingDevice,
)
from PySide6.QtWidgets import QApplication

from chart.models.chart_model import ChartModel
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
    assert widget._model is not None
    total = len(widget._model.bars or ())
    count = min(int(window_bars), total)
    widget._first = widget._anchor_first(total, count)
    widget._last = widget._first + count
    widget._follow_latest = True


def _right_click(widget: CandleChartWidget, x: float = 250.0, y: float = 100.0) -> QMouseEvent:
    device = QPointingDevice.primaryPointingDevice()
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
        device,
    )
    widget.mousePressEvent(event)
    return event


# ── menu contents ───────────────────────────────────────────────────────


def test_reset_action_text_and_shortcut() -> None:
    widget = _widget()
    assert "Reset chart view" in widget._reset_action.text()
    assert widget._reset_action.shortcut() == QKeySequence(Qt.Modifier.ALT | Qt.Key.Key_R)
    assert widget._reset_action.shortcut().toString() == "Alt+R"


def test_context_menu_has_single_reset_action() -> None:
    widget = _widget()
    menu = widget._context_menu()
    actions = menu.actions()
    assert len(actions) == 1
    assert actions[0] is widget._reset_action
    assert "Reset chart view" in actions[0].text()
    assert actions[0].shortcut().toString() == "Alt+R"


def test_context_menu_event_is_suppressed() -> None:
    widget = _widget()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5))
    widget.contextMenuEvent(event)
    assert event.isAccepted()


# ── right-click wiring ──────────────────────────────────────────────────


def test_right_click_shows_context_menu(monkeypatch) -> None:
    widget = _widget()
    shown: list[QMouseEvent] = []
    monkeypatch.setattr(widget, "_show_context_menu", lambda event: shown.append(event))
    event = _right_click(widget)
    assert len(shown) == 1
    assert event.isAccepted()


def test_right_click_does_not_clear_crosshair(monkeypatch) -> None:
    widget = _widget()
    monkeypatch.setattr(widget, "_show_context_menu", lambda _event: None)
    widget._crosshair_pos = QPoint(120, 100)
    _right_click(widget)
    assert widget._crosshair_pos == QPoint(120, 100)


def test_right_click_leaves_drag_state_untouched(monkeypatch) -> None:
    widget = _widget()
    monkeypatch.setattr(widget, "_show_context_menu", lambda _event: None)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    first_before = widget._first
    _right_click(widget)
    assert widget._drag_origin_x is None
    assert widget._price_drag_active is False
    assert widget._first == first_before


# ── reset view ──────────────────────────────────────────────────────────


def test_action_trigger_resets_viewport() -> None:
    widget = _widget(count=500)
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    widget._zoom_at_px(125.0, 0.5)
    widget._zoom_price_at(60.0, 0.5)
    assert widget._price_manual is not None
    assert widget._last - widget._first < CandleChartWidget.INITIAL_BARS
    widget._reset_action.trigger()
    assert widget._first == widget._anchor_first(500, CandleChartWidget.INITIAL_BARS)
    assert widget._last - widget._first == CandleChartWidget.INITIAL_BARS
    assert widget._first > 0
    assert widget._follow_latest
    assert widget._price_manual is None


def test_reset_view_restores_price_auto_fit() -> None:
    widget = _widget(count=500)
    widget._zoom_price_at(60.0, 0.5)
    assert widget._price_manual is not None
    widget.reset_view()
    assert widget._price_manual is None
    low, high = widget._price_range()
    first, last = widget._visible_range()
    assert widget._model is not None
    bars = widget._model.bars[first:last]
    assert min(bar.low for bar in bars) >= low
    assert max(bar.high for bar in bars) <= high


def test_reset_view_keeps_model_untouched() -> None:
    widget = _widget(count=500)
    assert widget._model is not None
    model = widget._model
    bars = model.bars
    _trailing(widget, CandleChartWidget.INITIAL_BARS)
    widget.reset_view()
    assert widget._model is model
    assert model.bars is bars
    assert model.symbol == "SPY"
    assert model.timeframe == "15m"
    assert model.exchange == "NSE"


def test_reset_view_no_model_is_noop() -> None:
    _app()
    widget = CandleChartWidget()
    widget.reset_view()
    assert widget._model is None
