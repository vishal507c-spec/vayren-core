"""CandleChartWidget — candlestick viewport: zoom, pan, touch, crosshair + overlays."""

from logging import getLogger
from math import hypot

from market.models.bar import Bar
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QAction,
    QBrush,
    QContextMenuEvent,
    QKeySequence,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QTouchEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from chart.models.chart_model import ChartModel
from chart.models.crosshair_value import CrosshairValue
from chart.renderer.candle_renderer import CandleRenderer
from chart.renderer.crosshair_renderer import CrosshairRenderer
from chart.renderer.overlay_renderer import OverlayRenderer
from chart.renderer.time_axis_renderer import TimeAxisRenderer

logger = getLogger(__name__)


class CandleChartWidget(QWidget):
    """Renders a ChartModel and manages the viewport.

    Interactions:
     wheel              — zoom anchored at the cursor
     wheel over price scale — vertical price zoom only (time viewport untouched)
     horizontal wheel   — pan
     left-drag          — pan; dropping at the right edge re-engages follow
     left-drag over the price scale — manual vertical scaling (up compresses,
                          down expands)
     double-click over the price scale — reset to auto-fit
     right-click        — chart context menu (single action: reset view)
     Alt+R              — reset chart view (same action as the menu)
     touch screen       — one finger crosshair, two fingers pan, pinch zoom
     precise trackpad   — native pinch zoom; horizontal scroll pans

    The latest bar is kept at ``RIGHT_MARGIN_FRACTION`` of the plot width with
    empty space to its right; when new bars arrive the view re-anchors while
    follow mode is engaged. A permanent header at the top of the plot always
    shows the symbol, timeframe, exchange and the latest bar's OHLC — it is
    independent of the crosshair, which only paints its price/time labels.
    Holds no events, no SQL, no data loading.
    """

    MIN_VISIBLE_BARS = 10
    ZOOM_STEP = 1.25
    VOLUME_RATIO = 0.15
    INITIAL_BARS = 150
    RIGHT_MARGIN_FRACTION = 0.15
    TIME_AXIS_HEIGHT = 24
    SYMBOL_HEIGHT = 24
    PRICE_STRIP_WIDTH = 96
    PRICE_ZOOM_STEP = 1.25
    PRICE_EDGE_MARGIN = 0.05

    _STRIP_BRUSH = QBrush(OverlayRenderer.STRIP_BG)
    _STRIP_BORDER_PEN = QPen(OverlayRenderer.STRIP_BORDER, 1)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: ChartModel | None = None
        self._first = 0
        self._last = 0
        self._follow_latest = True
        self._drag_origin_x: float | None = None
        self._drag_origin_y: float | None = None
        self._drag_first = 0
        self._drag_price_low = 0.0
        self._drag_price_high = 0.0
        self._crosshair_pos: QPoint | None = None
        self._crosshair_value: CrosshairValue | None = None
        self._grid_cache: QPixmap | None = None
        self._grid_key: tuple[int, int, int, int, float, float] | None = None
        self._static_cache: QPixmap | None = None
        self._static_key: tuple[object, ...] | None = None
        self._stats_cache: tuple[float, float, int] | None = None
        self._stats_key: tuple[object, ...] | None = None
        self._touch_points: dict[int, QPointF] = {}
        self._touch_centroid: QPointF | None = None
        self._touch_dist: float | None = None
        self._price_manual: tuple[float, float] | None = None
        self._price_drag_active = False
        self._price_drag_anchor_y = 0.0
        self._reset_action = QAction("↩ Reset chart view", self)
        self._reset_action.setShortcut(QKeySequence(Qt.Modifier.ALT | Qt.Key.Key_R))
        self._reset_action.triggered.connect(self.reset_view)
        self.addAction(self._reset_action)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    # ── model + viewport ──────────────────────────────────────────────

    def set_model(self, model: ChartModel) -> None:
        """Replace the chart data and reset the viewport.

        A fresh lifecycle (first load, new symbol or timeframe change) opens
        at the latest ``INITIAL_BARS``, never the whole history. For the same
        symbol/timeframe: follow-latest re-anchors the latest bar at the right
        margin, otherwise the window is shifted so the same bars stay in place.
        """
        previous = self._model
        previous_total = len(previous.bars) if previous else 0
        previous_count = self._window_size() or self.INITIAL_BARS
        same_symbol = previous is not None and previous.symbol == model.symbol
        same_series = same_symbol and previous is not None and previous.timeframe == model.timeframe
        self._model = model
        self._price_manual = None
        total = len(model.bars)
        if not same_series or self._follow_latest:
            if same_series:
                count = max(self.MIN_VISIBLE_BARS, min(previous_count, max(total, 1)))
            else:
                count = self._initial_count(total)
            first = self._anchor_first(total, count)
            self._first = first
            self._last = first + count
            self._follow_latest = True
        else:
            added = total - previous_total
            self._first = self._clamp_first(self._first + added)
            self._last = self._first + previous_count
        self._clear_crosshair()
        self._grid_cache = None
        self._grid_key = None
        self._static_cache = None
        self._static_key = None
        self._stats_cache = None
        self._stats_key = None
        self._log_data_range(model)
        self.update()

    def reset_view(self) -> None:
        """Restore the fresh-chart viewport — latest ``INITIAL_BARS``, price
        auto-fit, follow-latest.

        Mirrors the initial viewport that ``set_model`` builds for a fresh
        lifecycle. Viewport-only: never reloads data, never changes the
        symbol/timeframe, never touches the model.
        """
        if self._model is None:
            return
        total = len(self._model.bars)
        count = self._initial_count(total)
        self._first = self._anchor_first(total, count)
        self._last = self._first + count
        self._follow_latest = True
        self._price_manual = None
        self._clear_crosshair()
        self._grid_cache = None
        self._grid_key = None
        self._static_cache = None
        self._static_key = None
        self._stats_cache = None
        self._stats_key = None
        self.update()

    def _initial_count(self, total: int) -> int:
        """Window count for a fresh viewport: the latest ``INITIAL_BARS``.

        The whole history stays loaded; only the visible window is limited.
        """
        if total <= 0:
            return self.INITIAL_BARS
        return max(self.MIN_VISIBLE_BARS, min(self.INITIAL_BARS, total))

    def _log_data_range(self, model: ChartModel) -> None:
        first, last = self._visible_range()
        bars = model.bars
        if bars:
            logger.info(
                "Model for %s: history %s .. %s (total %d), visible %d..%d "
                "(count %d, first index %d)",
                model.symbol,
                bars[0].timestamp,
                bars[-1].timestamp,
                len(bars),
                first,
                last,
                self._window_size(),
                self._first,
            )

    def _window_size(self) -> int:
        return max(0, self._last - self._first)

    def _anchor_first(self, total: int, count: int) -> int:
        """First window index that puts the latest bar at the right margin."""
        if total <= 0:
            return 0
        target = round((1.0 - self.RIGHT_MARGIN_FRACTION) * count + 0.5)
        return max(0, min(total - target, total - 1))

    def _max_first(self) -> int:
        """Rightmost allowed first-index (latest bar at the right margin)."""
        if self._model is None:
            return 0
        total = len(self._model.bars)
        if total <= 0:
            return 0
        return self._anchor_first(total, self._window_size())

    def _clamp_first(self, first: int) -> int:
        return max(0, min(first, self._max_first()))

    def _visible_range(self) -> tuple[int, int]:
        if self._model is None:
            return (0, 0)
        total = len(self._model.bars)
        first = max(0, min(self._first, total - 1))
        last = min(self._last, total)
        return (first, last)

    def _chart_rects(self) -> tuple[QRect, QRect, QRect]:
        width = self.width()
        height = self.height()
        volume_height = int(height * self.VOLUME_RATIO)
        axis_height = self.TIME_AXIS_HEIGHT
        chart = QRect(0, 0, width, height - volume_height - axis_height)
        volume = QRect(0, height - volume_height - axis_height, width, volume_height)
        axis = QRect(0, height - axis_height, width, axis_height)
        return chart, volume, axis

    # ── context menu ─────────────────────────────────────────────────

    def _context_menu(self) -> QMenu:
        """The chart's context menu — exactly one action: reset view."""
        menu = QMenu(self)
        menu.addAction(self._reset_action)
        return menu

    def _show_context_menu(self, event: QMouseEvent) -> None:
        """Open the context menu at the cursor.

        Qt closes it on selection, Escape or clicking outside.
        """
        self._context_menu().exec(event.globalPosition().toPoint())

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Suppress the default context menu — right-click is handled on press."""
        event.accept()

    # ── price scale ───────────────────────────────────────────────────

    def _price_range(self) -> tuple[float, float]:
        """Effective vertical price range for the plot area.

        ``_price_manual`` overrides the automatic fit (user zoomed/dragged the
        price scale). Auto-fit spans visible bars plus a small top/bottom
        margin so every visible candle stays inside the chart. The visible
        extremes come from the cached window stats — mouse-only repaints
        never rescan the window.
        """
        if self._price_manual is not None:
            low, high = self._price_manual
            if high > low:
                return low, high
        if self._model is None:
            return 0.0, 1.0
        first, last = self._visible_range()
        if last <= first:
            return 0.0, 1.0
        low, high, _ = self._window_stats()
        span = high - low
        if span <= 0.0:
            span = abs(high) * 0.01 or 0.01
        pad = span * self.PRICE_EDGE_MARGIN
        return low - pad, high + pad

    def _window_stats(self) -> tuple[float, float, int]:
        """Visible-window extremes ``(price_low, price_high, volume_max)``.

        A single pass over the visible bars computes all three; the result is
        cached and only recomputed when the model, window or price range
        changes — so crosshair-move repaints never touch the bars.
        """
        key = (id(self._model), self._first, self._last, self._price_manual)
        if self._stats_key == key and self._stats_cache is not None:
            return self._stats_cache
        if self._model is None:
            result = (0.0, 1.0, 0)
        else:
            first, last = self._visible_range()
            if last <= first:
                result = (0.0, 1.0, 0)
            elif self._price_manual is not None:
                low, high = self._price_manual
                if high <= low:
                    result = (0.0, 1.0, 0)
                else:
                    bars = self._model.bars
                    volume_max = 0
                    for index in range(first, last):
                        volume = bars[index].volume
                        if volume > volume_max:
                            volume_max = volume
                    result = (low, high, volume_max)
            else:
                bars = self._model.bars
                low = bars[first].low
                high = bars[first].high
                volume_max = bars[first].volume
                for index in range(first + 1, last):
                    bar = bars[index]
                    if bar.low < low:
                        low = bar.low
                    if bar.high > high:
                        high = bar.high
                    if bar.volume > volume_max:
                        volume_max = bar.volume
                result = (low, high, volume_max)
        self._stats_cache = result
        self._stats_key = key
        return result

    def _over_price_strip(self, x: float, y: float) -> bool:
        """True when (x, y) hits the right-side price scale column."""
        chart_rect, _, _ = self._chart_rects()
        return (
            chart_rect.width() > 0
            and chart_rect.top() <= y <= chart_rect.bottom()
            and chart_rect.right() - self.PRICE_STRIP_WIDTH <= x <= chart_rect.right()
        )

    def _zoom_price_at(self, anchor_y: float, factor: float) -> None:
        """Zoom the price range around the pixel `anchor_y`.

        The price under the cursor stays under the cursor; only the vertical
        range changes. The candle/time viewport is never touched.
        """
        if factor <= 0.0 or factor == 1.0:
            return
        chart_rect, _, _ = self._chart_rects()
        if chart_rect.height() <= 0:
            return
        low, high = self._price_range()
        span = high - low
        if span <= 0.0:
            return
        height = chart_rect.height()
        fraction = (chart_rect.bottom() - anchor_y) / height
        fraction = max(0.0, min(1.0, fraction))
        anchor_price = low + fraction * span
        new_span = max(span * factor, span * 0.01)
        new_low = anchor_price - fraction * new_span
        new_high = new_low + new_span
        if (new_low, new_high) == (low, high):
            return
        self._price_manual = (new_low, new_high)
        self._grid_cache = None
        self._grid_key = None
        self.update(chart_rect)

    def _reset_price_scale(self) -> None:
        """Return to the auto-fit price range (double-click on the scale)."""
        if self._price_manual is None:
            return
        self._price_manual = None
        self._grid_cache = None
        self._grid_key = None
        chart_rect, _, _ = self._chart_rects()
        self.update(chart_rect)

    def _drag_price_from(self, cursor_y: float) -> None:
        """Compress (drag up) or expand (drag down) the price scale."""
        delta_y = cursor_y - self._price_drag_anchor_y
        factor = self.PRICE_ZOOM_STEP ** (delta_y / 120.0)
        self._zoom_price_at(self._price_drag_anchor_y, factor)

    # ── painting ──────────────────────────────────────────────────────

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._model is None or not self._model.bars:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), CandleRenderer.BACKGROUND)
        first, last = self._visible_range()
        if last <= first:
            return
        chart_rect, volume_rect, axis_rect = self._chart_rects()
        price_low, price_high = self._price_range()
        _, _, volume_max = self._window_stats()
        painter.drawPixmap(
            0,
            0,
            self._static_pixmap(
                chart_rect, volume_rect, axis_rect, price_low, price_high, volume_max
            ),
        )
        self._paint_header(painter, chart_rect)
        crosshair = self._crosshair_pos
        if crosshair is not None and chart_rect.contains(crosshair):
            CrosshairRenderer.paint(painter, crosshair, chart_rect)
            if self._crosshair_value is not None:
                self._paint_overlays(painter, crosshair, chart_rect, axis_rect)

    def _static_pixmap(
        self,
        chart_rect: QRect,
        volume_rect: QRect,
        axis_rect: QRect,
        price_low: float,
        price_high: float,
        volume_max: int,
    ) -> QPixmap:
        """Cached widget-sized frame: grid + candles + time axis.

        Everything that does not change on mouse-only repaints is baked into
        one pixmap, so crosshair movement costs a single blit instead of a
        full redraw. Rebuilt whenever the model, window, price range or size
        changes (all part of the key).
        """
        key = (
            id(self._model),
            self._first,
            self._last,
            price_low,
            price_high,
            volume_max,
            self.width(),
            self.height(),
        )
        if self._static_cache is not None and self._static_key == key:
            return self._static_cache
        pixmap = QPixmap(self.size())
        pixmap.fill(CandleRenderer.BACKGROUND)
        painter = QPainter(pixmap)
        painter.drawPixmap(
            chart_rect,
            self._grid_pixmap(chart_rect, price_low, price_high),
            QRect(QPoint(0, 0), chart_rect.size()),
        )
        bars = self._model.bars if self._model is not None else ()
        CandleRenderer.paint_bars(
            painter,
            bars,
            self._first,
            self._last,
            price_low,
            price_high,
            volume_max,
            chart_rect,
            volume_rect,
        )
        TimeAxisRenderer.paint(painter, bars, self._first, self._last, axis_rect)
        painter.end()
        self._static_cache = pixmap
        self._static_key = key
        return pixmap

    def _grid_pixmap(self, chart_rect: QRect, price_low: float, price_high: float) -> QPixmap:
        """Return the cached static grid pixmap for the current viewport."""
        key = (
            chart_rect.x(),
            chart_rect.y(),
            chart_rect.width(),
            chart_rect.height(),
            price_low,
            price_high,
        )
        if self._grid_cache is None or self._grid_key != key:
            pixmap = QPixmap(chart_rect.size())
            pixmap.fill(CandleRenderer.BACKGROUND)
            grid_painter = QPainter(pixmap)
            CandleRenderer.paint_grid(
                grid_painter,
                QRect(0, 0, chart_rect.width(), chart_rect.height()),
                price_low,
                price_high,
            )
            grid_painter.end()
            self._grid_cache = pixmap
            self._grid_key = key
        return self._grid_cache

    def _paint_header(self, painter: QPainter, chart_rect: QRect) -> None:
        """Paint the permanent top info strip: symbol • timeframe • exchange
        and the latest bar's OHLC (real model data).

        Rendered on every paint once a model is loaded — independent of the
        crosshair. Updates automatically when the model changes because the
        text is derived from the current model. The strip is a solid panel
        with a hairline at its bottom edge, framing the plot below it.
        """
        if self._model is None or not self._model.bars:
            return
        model = self._model
        top_bar = QRect(
            chart_rect.left(),
            chart_rect.top(),
            chart_rect.width(),
            self.SYMBOL_HEIGHT,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._STRIP_BRUSH)
        painter.drawRoundedRect(QRectF(top_bar), 3.0, 3.0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(self._STRIP_BORDER_PEN)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            top_bar.left() + 2,
            top_bar.bottom() - 1,
            top_bar.right() - 2,
            top_bar.bottom() - 1,
        )
        painter.restore()
        symbol_rect = OverlayRenderer.paint_symbol_info(
            painter,
            model.symbol,
            model.timeframe,
            model.exchange,
            top_bar,
        )
        OverlayRenderer.paint_ohlc(
            painter,
            model.bars[-1],
            top_bar,
            left_margin=symbol_rect.right(),
        )

    def _paint_overlays(
        self,
        painter: QPainter,
        crosshair_pos: QPoint,
        chart_rect: QRect,
        axis_rect: QRect,
    ) -> None:
        """Paint the crosshair-only labels: right price and bottom time.

        The top info bar belongs to the permanent header (``_paint_header``),
        so the crosshair overlays stay fully independent of it.
        """
        if self._model is None or self._crosshair_value is None:
            return
        value = self._crosshair_value
        OverlayRenderer.paint_price(painter, value, crosshair_pos.y(), chart_rect)
        OverlayRenderer.paint_time(
            painter,
            value,
            self._model.timeframe,
            crosshair_pos.x(),
            axis_rect,
        )

    # ── zoom / pan ────────────────────────────────────────────────────

    def _zoom_at_px(self, anchor_x: float, scale: float) -> None:
        """Zoom around `anchor_x` (the bar under it stays under the cursor)."""
        if self._model is None or not self._model.bars or scale <= 0.0:
            return
        chart_rect, _, _ = self._chart_rects()
        count = self._window_size()
        total = len(self._model.bars)
        if chart_rect.width() <= 0 or count <= 0:
            return
        fraction = (anchor_x - chart_rect.left()) / chart_rect.width()
        fraction = max(0.0, min(1.0, fraction))
        anchor_bar = self._first + fraction * count
        anchor_bar = max(float(self._first), min(float(total - 1), anchor_bar))
        new_count = max(self.MIN_VISIBLE_BARS, min(total, round(count * scale)))
        new_first_raw = round(anchor_bar - fraction * new_count)
        new_first = max(0, min(new_first_raw, self._anchor_first(total, new_count)))
        if new_first == self._first and new_count == count:
            return
        self._first = new_first
        self._last = new_first + new_count
        self._follow_latest = new_first >= self._max_first()
        self.update()

    def _pan_delta_px(self, delta_x_px: float) -> None:
        """Pan the viewport by a pixel delta (content follows the movement)."""
        if self._model is None or not self._model.bars:
            return
        chart_rect, _, _ = self._chart_rects()
        count = self._window_size()
        if chart_rect.width() <= 0 or count <= 0:
            return
        delta_bars = -delta_x_px / chart_rect.width() * count
        new_first = self._clamp_first(self._first + round(delta_bars))
        if new_first == self._first:
            return
        self._first = new_first
        self._last = new_first + count
        self._follow_latest = new_first >= self._max_first()
        self.update()

    # ── input events ──────────────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._model is None or not self._model.bars:
            return
        angle = event.angleDelta()
        position = event.position()
        if angle.y() != 0:
            steps = angle.y() / 120.0
            if self._over_price_strip(position.x(), position.y()):
                self._zoom_price_at(position.y(), self.PRICE_ZOOM_STEP**-steps)
            else:
                self._zoom_at_px(position.x(), self.ZOOM_STEP**-steps)
            event.accept()
            return
        if angle.x() != 0:
            pixel = event.pixelDelta()
            delta = pixel.x() if pixel.x() != 0 else float(angle.x())
            self._pan_delta_px(delta)
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        if self._over_price_strip(position.x(), position.y()):
            self._reset_price_scale()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._model is None:
            return
        position = event.position()
        if self._over_price_strip(position.x(), position.y()):
            self._price_drag_active = True
            self._price_drag_anchor_y = position.y()
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            return
        self._drag_origin_x = position.x()
        self._drag_origin_y = position.y()
        self._drag_first = self._first
        price_low, price_high = self._price_range()
        self._drag_price_low = price_low
        self._drag_price_high = price_high
        self.grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._clear_crosshair()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._price_drag_active:
            self._drag_price_from(event.position().y())
            return
        if self._drag_origin_x is not None:
            position = event.position()
            self._pan_from_drag(position.x(), position.y())
            return
        position = QPoint(int(event.position().x()), int(event.position().y()))
        if position != self._crosshair_pos:
            old_pos = self._crosshair_pos
            self._crosshair_pos = position
            self._snap_crosshair(position)
            self.update(self._crosshair_dirty_rect(old_pos, position))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._price_drag_active:
            self._price_drag_active = False
            self.unsetCursor()
            return
        self._drag_origin_x = None
        self._drag_origin_y = None
        self.releaseMouse()
        self.unsetCursor()
        if self._model is not None and self._first >= self._max_first():
            self._follow_latest = True

    def leaveEvent(self, _event: QEvent) -> None:
        self._clear_crosshair()

    def resizeEvent(self, _event: QResizeEvent) -> None:
        self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.NativeGesture and isinstance(event, QNativeGestureEvent):
            value = event.value()
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture and value != 0.0:
                self._zoom_at_px(event.localPos().x(), 1.0 / (1.0 + value))
            return True
        return super().event(event)

    def touchEvent(self, event: QTouchEvent) -> None:
        active: dict[int, QPointF] = {}
        for point in event.points():
            if point.state() != Qt.TouchPointState.TouchPointReleased:
                active[point.id()] = point.position()
        if event.type() == QEvent.Type.TouchEnd or not active:
            self._touch_points.clear()
            self._touch_centroid = None
            self._touch_dist = None
            event.accept()
            return
        self._touch_points = active
        self._handle_touch_points()
        event.accept()

    def _handle_touch_points(self) -> None:
        """Drive crosshair (1 finger) or pan + pinch (2+ fingers)."""
        points = self._touch_points
        if not points:
            self._touch_centroid = None
            self._touch_dist = None
            return
        if len(points) == 1:
            point = next(iter(points.values()))
            position = QPoint(int(point.x()), int(point.y()))
            if position != self._crosshair_pos:
                old_pos = self._crosshair_pos
                self._crosshair_pos = position
                self._snap_crosshair(position)
                self.update(self._crosshair_dirty_rect(old_pos, position))
            return
        ids = list(points)
        first_point = points[ids[0]]
        second_point = points[ids[1]]
        centroid = QPointF(
            (first_point.x() + second_point.x()) / 2.0,
            (first_point.y() + second_point.y()) / 2.0,
        )
        distance = CandleChartWidget._point_distance(first_point, second_point)
        if self._touch_centroid is None or self._touch_dist is None:
            self._touch_centroid = centroid
            self._touch_dist = distance
            self._follow_latest = False
            self._clear_crosshair()
            return
        previous_dist = self._touch_dist
        previous_centroid = self._touch_centroid
        self._touch_centroid = centroid
        self._touch_dist = distance
        if previous_dist > 0.0:
            self._zoom_at_px(centroid.x(), previous_dist / distance)
        delta_x = centroid.x() - previous_centroid.x()
        self._pan_delta_px(delta_x)
        delta_y = centroid.y() - previous_centroid.y()
        self._pan_price_delta_px(delta_y)

    @staticmethod
    def _point_distance(first: QPointF, second: QPointF) -> float:
        return hypot(second.x() - first.x(), second.y() - first.y())

    # ── crosshair ─────────────────────────────────────────────────────

    def _pan_from_drag(self, cursor_x: float, cursor_y: float) -> None:
        """Pan the viewport so the chart content follows the drag.

        Horizontal movement shifts the visible time window; vertical movement
        shifts the visible price range by the same span (zoom unchanged). The
        pointer is grabbed, so dragging continues even when it leaves the chart.
        """
        if self._model is None or self._drag_origin_x is None or self._drag_origin_y is None:
            return
        chart_rect, _, _ = self._chart_rects()
        count = self._window_size()
        if chart_rect.width() <= 0 or count <= 0:
            return
        changed = False
        delta_x = cursor_x - self._drag_origin_x
        delta_bars = -delta_x / chart_rect.width() * count
        new_first = self._clamp_first(self._drag_first + round(delta_bars))
        if new_first != self._first:
            self._first = new_first
            self._last = new_first + count
            self._follow_latest = new_first >= self._max_first()
            changed = True
        height = chart_rect.height()
        span = self._drag_price_high - self._drag_price_low
        if height > 0 and span > 0.0:
            delta_y = cursor_y - self._drag_origin_y
            if delta_y != 0.0:
                shift = span / height * delta_y
                self._price_manual = (
                    self._drag_price_low + shift,
                    self._drag_price_high + shift,
                )
                self._grid_cache = None
                self._grid_key = None
                changed = True
        if changed:
            self.update()

    def _pan_price_delta_px(self, delta_y_px: float) -> None:
        """Pan the visible price range vertically by a pixel delta (span unchanged)."""
        chart_rect, _, _ = self._chart_rects()
        height = chart_rect.height()
        low, high = self._price_range()
        span = high - low
        if height <= 0 or span <= 0.0 or delta_y_px == 0.0:
            return
        shift = span / height * delta_y_px
        new_manual = (low + shift, high + shift)
        if new_manual == self._price_manual:
            return
        self._price_manual = new_manual
        self._grid_cache = None
        self._grid_key = None
        self.update(chart_rect)

    def _snap_crosshair(self, position: QPoint) -> None:
        """Snap the crosshair to the nearest candle and compute its value."""
        if self._model is None or not self._model.bars:
            self._crosshair_value = None
            return
        first, last = self._visible_range()
        if last <= first:
            self._crosshair_value = None
            return
        chart_rect, _, _ = self._chart_rects()
        count = self._window_size()
        if chart_rect.width() <= 0 or count <= 0:
            self._crosshair_value = None
            return
        slot_width = chart_rect.width() / count
        x_center = chart_rect.left() + slot_width / 2.0
        fraction = (position.x() - x_center) / slot_width
        raw_index = first + round(fraction)
        bar_index = max(first, min(last - 1, raw_index))
        bar = self._model.bars[bar_index]
        price = self._price_from_y(bar, position.y(), chart_rect)
        self._crosshair_value = CrosshairValue(
            bar_index=bar_index,
            price=price,
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
        )

    def _price_from_y(self, bar: Bar, y: int, chart_rect: QRect) -> float:
        """Return the price at vertical pixel `y` within the chart rect."""
        if self._model is None:
            return bar.close
        low, high = self._price_range()
        span = high - low
        if span <= 0.0:
            return bar.close
        y_top = chart_rect.top()
        y_bottom = chart_rect.bottom()
        fraction = (y_bottom - y) / (y_bottom - y_top)
        fraction = max(0.0, min(1.0, fraction))
        return low + fraction * span

    def _crosshair_dirty_rect(self, old_pos: QPoint | None, new_pos: QPoint | None) -> QRect:
        """Smallest region covering the previous and next crosshair + overlays."""
        chart_rect, _, axis_rect = self._chart_rects()
        rects: list[QRect] = []
        for pos in (old_pos, new_pos):
            if pos is None:
                continue
            rects.append(QRect(pos.x(), chart_rect.top(), 1, chart_rect.height()))
            rects.append(QRect(chart_rect.left(), pos.y(), chart_rect.width(), 1))
        if self._crosshair_value is not None:
            rects.append(
                QRect(
                    chart_rect.right() - self.PRICE_STRIP_WIDTH,
                    chart_rect.top(),
                    self.PRICE_STRIP_WIDTH,
                    chart_rect.height(),
                )
            )
            rects.append(axis_rect)
        if not rects:
            return self.rect()
        dirty = rects[0]
        for rect in rects[1:]:
            dirty = dirty.united(rect)
        return dirty

    def _clear_crosshair(self) -> None:
        if self._crosshair_pos is not None or self._crosshair_value is not None:
            dirty = self._crosshair_dirty_rect(self._crosshair_pos, None)
            self._crosshair_pos = None
            self._crosshair_value = None
            self.update(dirty)
