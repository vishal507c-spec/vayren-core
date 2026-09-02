"""CandleChartWidget — candlestick viewport: zoom, pan, touch, crosshair + overlays."""

import contextlib
from logging import getLogger
from math import hypot

from market.models.bar import Bar
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
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
from chart.models.chart_viewport import ChartOverlay, ChartViewport
from chart.models.crosshair_value import CrosshairValue
from chart.renderer.candle_renderer import CandleRenderer
from chart.renderer.crosshair_renderer import CrosshairRenderer
from chart.renderer.overlay_renderer import OverlayRenderer
from chart.renderer.time_axis_renderer import TimeAxisRenderer
from chart.widgets.indicator_visibility_panel import IndicatorVisibilityPanel

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

    session_changed = Signal()
    indicator_added = Signal(str)

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
        self._overlay: ChartOverlay | None = None
        self._overlays: dict[str, ChartOverlay] = {}
        # ── indicator visibility (TradingView-style, dynamic) ─────────
        self._indicator_visible: dict[str, bool] = {}
        self._visibility_panel = IndicatorVisibilityPanel(self)
        # start hidden — no active indicator (TradingView exact)
        self._visibility_panel.hide()
        self._visibility_panel.visibility_changed.connect(self._on_indicator_visibility_changed)
        self._visibility_panel.indicator_removed.connect(self._on_indicator_removed)
        self._visibility_panel.settings_requested.connect(self._on_indicator_settings)
        self._visibility_panel.source_requested.connect(self._on_indicator_source)
        self._visibility_panel.more_requested.connect(self._on_indicator_more)
        self._position_visibility_panel()
        self._reset_action = QAction("↩ Reset chart view", self)
        self._reset_action.setShortcut(QKeySequence(Qt.Modifier.ALT | Qt.Key.Key_R))
        self._reset_action.triggered.connect(self.reset_view)
        self.addAction(self._reset_action)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    # ── overlay hook ────────────────────────────────────────────────

    @property
    def overlay(self) -> ChartOverlay | None:
        """Currently installed chart overlay, if any."""
        return self._overlay

    def set_overlay(self, overlay: ChartOverlay | None) -> None:
        """Install or remove a :class:`ChartOverlay` extension.

        The overlay's :meth:`paint_overlay` is called on every paint with a
        viewport snapshot (bars, window, price range, rects) so it can map
        indices/prices → pixels using the chart's own math.
        """
        self._overlay = overlay
        # keep per-indicator map in sync — TradeOverlay is treated as "OBR" indicator
        if overlay is None:
            self._overlays.pop("OBR", None)
        else:
            # store under OBR key (canonical strategy overlay); also keep generic
            self._overlays["OBR"] = overlay
        self.update()

    def set_named_overlay(self, name: str, overlay: ChartOverlay | None) -> None:
        """Install or remove a named overlay (per-indicator layer).

        Each name corresponds to one row in the visibility panel. Visibility
        toggle hides only that name's overlay — per-indicator isolation.
        Volume ("Vol") is not an overlay (handled via volume strip).
        """
        if overlay is None:
            self._overlays.pop(name, None)
            if self._overlay is not None and name == "OBR":
                self._overlay = None
        else:
            self._overlays[name] = overlay
            if name == "OBR":
                self._overlay = overlay
        self.update()

    # ── indicator visibility (TradingView-style, dynamic) ────────────

    @property
    def visibility_panel(self) -> IndicatorVisibilityPanel:
        """Floating indicator visibility list (top-left)."""
        return self._visibility_panel

    @property
    def indicator_visibility(self) -> dict[str, bool]:
        """Current visibility per indicator (copy)."""
        return dict(self._indicator_visible)

    def is_indicator_visible(self, name: str) -> bool:
        return self._indicator_visible.get(name, True)

    @property
    def volume_visible(self) -> bool:
        """Whether the volume strip is currently rendered."""
        return self._indicator_visible.get("Vol", True)

    @property
    def overlay_visible(self) -> bool:
        """Whether the strategy overlay (OBR) is currently rendered."""
        return self._indicator_visible.get("OBR", True)

    def _normalize_indicator_name(self, name: str) -> str:
        return "Vol" if name.lower() == "volume" else name

    def add_indicator(self, name: str) -> None:
        """Add indicator to panel (dynamic, TradingView exact)."""
        key = self._normalize_indicator_name(name)
        if key in self._indicator_visible and self._visibility_panel.has_indicator(key):
            return
        self._indicator_visible[key] = True
        self._visibility_panel.add_indicator(key)
        # ensure overlay mapping for known strategy indicators
        if key == "OBR" and self._overlay is not None and key not in self._overlays:
            self._overlays[key] = self._overlay
        self._position_visibility_panel()
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        self.indicator_added.emit(key)
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def remove_indicator(self, name: str) -> None:
        """Remove indicator completely (Delete action)."""
        key = self._normalize_indicator_name(name)
        if not self._visibility_panel.has_indicator(key) and key not in self._indicator_visible:
            return
        # delegate to panel which will emit indicator_removed
        self._visibility_panel.remove_indicator(key)

    def clear_indicators(self) -> None:
        """Remove all indicators (panel hidden)."""
        self._indicator_visible.clear()
        self._visibility_panel.clear()
        self._overlays.clear()
        if self._overlay is not None:
            # keep single overlay but not mapped — hidden until re-added
            pass
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def set_indicator_visible(self, name: str, visible: bool) -> None:
        """Set visibility for `name` — updates eye panel and chart rendering.

        Only visual rendering is affected; underlying data/calculation untouched.
        Name stays in list even when hidden (crossed-eye).
        """
        key = self._normalize_indicator_name(name)
        if self._indicator_visible.get(key, True) == visible:
            return
        self._indicator_visible[key] = visible
        panel_row = self._visibility_panel.row(key)
        if panel_row is not None and panel_row.is_visible != visible:
            panel_row.set_visible(visible)
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def set_indicators(self, names: tuple[str, ...]) -> None:
        """Replace indicator list (panel rows) — preserves visibility where possible."""
        # normalize
        norm = tuple(self._normalize_indicator_name(n) for n in names)
        previous = dict(self._indicator_visible)
        self._indicator_visible = {n: previous.get(n, True) for n in norm}
        self._visibility_panel.set_indicators(norm)
        # restore overlay mappings for strategy indicators present
        for n in norm:
            if n == "OBR" and self._overlay is not None:
                self._overlays[n] = self._overlay
        self._position_visibility_panel()
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_indicator_visibility_changed(self, name: str, visible: bool) -> None:
        """Panel eye clicked — update rendering only, never data/calculation."""
        key = self._normalize_indicator_name(name)
        self._indicator_visible[key] = visible
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_indicator_removed(self, name: str) -> None:
        """Panel Delete clicked — remove indicator completely (owner-aware)."""
        key = self._normalize_indicator_name(name)
        if key in ("Vol", "OBR"):
            self._indicator_visible[key] = False
        else:
            self._indicator_visible.pop(key, None)
        # For PlotOverlay (single instance handling many owners), just remove that owner's series
        # Don't pop the overlay itself — keep it for other owners
        if key in self._overlays:
            ov = self._overlays.get(key)
            if ov is not None and hasattr(ov, "remove_owner"):
                with contextlib.suppress(Exception):
                    ov.remove_owner(key)
                # keep PlotOverlay in dict — don't pop, it may hold other owners' series
                # only pop if it's not a PlotOverlay (i.e., TradeOverlay)
                pass
            else:
                self._overlays.pop(key, None)
        # owner-aware chart cleanup — remove all plot series for this owner from any PlotOverlay
        try:
            for ov in list(self._overlays.values()):
                if hasattr(ov, "remove_owner"):
                    with contextlib.suppress(Exception):
                        ov.remove_owner(key)  # type: ignore[attr-defined]
            if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                with contextlib.suppress(Exception):
                    self._plot_overlay.remove_owner(key)  # type: ignore[attr-defined]
        except Exception:
            pass
        # If we removed the overlay entry and it was the only one, keep PlotOverlay for other owners
        # No need to pop PLOT overlay when removing OBR — keep it
        self._position_visibility_panel()
        self._static_cache = None
        self._static_key = None
        self._grid_cache = None
        self._grid_key = None
        self.update()
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_indicator_settings(self, name: str) -> None:
        logger.info("Indicator settings requested: %s", name)

    def _on_indicator_source(self, name: str) -> None:
        logger.info("Indicator source requested: %s", name)

    def _on_indicator_more(self, name: str, pos: object) -> None:
        logger.info("Indicator more requested: %s at %s", name, pos)

    def _position_visibility_panel(self) -> None:
        """Place panel top-left, just below header strip, compact, not covering price."""
        x = 8
        y = self.SYMBOL_HEIGHT + 6
        self._visibility_panel.move(x, y)
        self._visibility_panel.raise_()
        if self._visibility_panel.indicators:
            self._visibility_panel.show()
        else:
            self._visibility_panel.hide()

    def _paint_strategy_overlay(
        self,
        painter: QPainter,
        chart_rect: QRect,
        volume_rect: QRect,
        axis_rect: QRect,
        price_low: float,
        price_high: float,
        volume_max: int,
    ) -> None:
        """Delegate painting to the installed overlay(s), respecting per-indicator visibility.

        Builds a :class:`ChartViewport` snapshot from the current model /
        window / price range and forwards it. No-ops when no overlay or no
        model is loaded — cheap enough to call on every paint.
        Hidden indicators skip their overlay only — other indicators unchanged.
        """
        if self._model is None or not self._model.bars:
            return
        # collect overlays whose indicator is visible
        to_paint: list[ChartOverlay] = []
        if self._overlays:
            for name, ov in self._overlays.items():
                if self._indicator_visible.get(name, True):
                    to_paint.append(ov)
            if (
                self._overlay is not None
                and self._overlay not in self._overlays.values()
                and self._indicator_visible.get("OBR", True)
            ):
                to_paint.append(self._overlay)
        elif self._overlay is not None and self._indicator_visible.get("OBR", True):
            to_paint.append(self._overlay)
        if not to_paint:
            return
        viewport = ChartViewport(
            bars=self._model.bars,
            first=self._first,
            last=self._last,
            price_low=price_low,
            price_high=price_high,
            volume_max=volume_max,
            chart_rect=chart_rect,
            volume_rect=volume_rect,
            axis_rect=axis_rect,
        )
        for overlay in to_paint:
            try:
                overlay.paint_overlay(painter, viewport)
            except Exception:  # noqa: BLE001
                logger.exception("Overlay paint failed")

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
        # keep panel top-left floating, never covering price scale
        self._position_visibility_panel()
        painter = QPainter(self)
        painter.fillRect(self.rect(), CandleRenderer.BACKGROUND)
        if self._model is None:
            self._paint_empty_state(painter, "Loading chart…")
            return
        if not self._model.bars:
            tf = getattr(self._model, "timeframe", "")
            msg = f"No {tf} data available" if tf else "No data available"
            self._paint_empty_state(painter, msg)
            return
        first, last = self._visible_range()
        if last <= first:
            self._paint_empty_state(painter, "No data in viewport")
            return
        chart_rect, volume_rect, axis_rect = self._chart_rects()
        price_low, price_high = self._price_range()
        _, _, volume_max_raw = self._window_stats()
        volume_max = volume_max_raw if self._indicator_visible.get("Vol", True) else 0
        painter.drawPixmap(
            0,
            0,
            self._static_pixmap(
                chart_rect, volume_rect, axis_rect, price_low, price_high, volume_max
            ),
        )
        self._paint_header(painter, chart_rect)
        self._paint_volume_value_label(painter, volume_rect, volume_max_raw)
        self._paint_strategy_overlay(
            painter, chart_rect, volume_rect, axis_rect, price_low, price_high, volume_max_raw
        )
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
        changes (all part of the key). Volume visibility is part of key.
        """
        vol_visible = self._indicator_visible.get("Vol", True)
        key = (
            id(self._model),
            self._first,
            self._last,
            price_low,
            price_high,
            volume_max,
            vol_visible,
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

    def _paint_volume_value_label(
        self, painter: QPainter, volume_rect: QRect, volume_max: int
    ) -> None:
        """Paint selected-candle volume value at right edge of volume pane (TradingView exact).

        Compact rounded-rect, right-aligned, vertically at selected volume level
        (crosshair's candle), green/red per that candle's direction, formatted
        as 950 / 8.02 K / 1.25 M / 2.50 B. Hidden when crosshair outside
        (TradingView hide/reset) — no fixed latest label.
        No-ops when no model, no volume, Vol hidden, or no crosshair.
        """
        if self._model is None or not self._model.bars:
            return
        if not self._indicator_visible.get("Vol", True):
            return
        if volume_rect.isEmpty() or volume_max <= 0:
            return
        if self._crosshair_pos is None or self._crosshair_value is None:
            return
        idx = self._crosshair_value.bar_index
        if idx < 0 or idx >= len(self._model.bars):
            return
        bar = self._model.bars[idx]
        is_bull = bar.close >= bar.open
        CandleRenderer.paint_volume_value(painter, bar.volume, is_bull, volume_max, volume_rect)

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

    def _paint_empty_state(self, painter: QPainter, message: str) -> None:
        """Centered subtle message for empty/loading states.

        Uses the VAYREN muted text on the chart background so it never looks
        like an unexplained black void. The chart header is intentionally not
        drawn in this state — the message itself communicates the status.
        """
        from PySide6.QtGui import QColor, QFont

        chart_rect, _, _ = self._chart_rects()
        # Use full widget rect if chart_rect is degenerate (e.g., zero size)
        target = chart_rect if chart_rect.width() > 40 and chart_rect.height() > 40 else self.rect()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont("Segoe UI", 9)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        painter.setPen(QColor("#5d6778"))
        painter.drawText(target, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()

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
        self._refresh_crosshair_after_viewport()
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
        self._refresh_crosshair_after_viewport()
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
        self._position_visibility_panel()
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

    def _refresh_crosshair_after_viewport(self) -> None:
        """Re-snap crosshair after pan/zoom so volume tracking stays synced (TradingView)."""
        if self._crosshair_pos is not None:
            self._snap_crosshair(self._crosshair_pos)

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
        """Smallest region covering previous/next crosshair + overlays + volume label."""
        chart_rect, volume_rect, axis_rect = self._chart_rects()
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
            rects.append(
                QRect(volume_rect.right() - 80, volume_rect.top(), 80, volume_rect.height())
            )
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
