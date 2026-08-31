"""PlotRenderer — TradingView-style plot series painting.

Renders generic plot series produced by StrategyVM's plot() primitive.
Each series is a dict of bar_index -> value, stable identity per title.
Handles session gaps (no line across missing bars), zoom/pan, and viewport.
Pure painting: no state, no calculation.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen

from chart.models.chart_viewport import ChartViewport

# TradingView-like palette for plot series — distinct, not OBR-specific
_PLOT_COLORS: tuple[QColor, ...] = (
    QColor("#26a69a"),  # teal
    QColor("#ef5350"),  # red
    QColor("#ffb74d"),  # amber
    QColor("#42a5f5"),  # blue
    QColor("#ab47bc"),  # purple
    QColor("#66bb6a"),  # green
    QColor("#ff7043"),  # orange
)

_PLOT_WIDTH = 1.6
_PLOT_STYLE = Qt.PenStyle.SolidLine


def _price_y(price: float, low: float, high: float, rect: QRectF) -> float:
    span = high - low
    if span <= 0:
        return rect.center().y()
    frac = (price - low) / span
    return rect.bottom() - frac * rect.height()


def _bar_x(index: int, first: int, last: int, rect: QRectF) -> float:
    count = max(1, last - first)
    return rect.left() + (index - first + 0.5) / count * rect.width()


class PlotOverlay:
    """Owner-aware chart overlay — each series belongs to an owner instance.

    Key is (owner_id, title) -> {bar_index: value}.  Gaps break line.
    No OBR-specific hardcode — generic.
    """

    def __init__(self, is_visible: Any | None = None) -> None:
        # owner-aware: (owner_id, title) -> {bar_index: value}
        self._series: dict[tuple[str, str], dict[int, float]] = {}
        self._meta: dict[tuple[str, str], dict[str, str]] = {}
        self._is_visible = is_visible  # callable(owner_or_title) -> bool

    def set_series(
        self, series: dict[tuple[str, str], dict[int, float]], meta: dict[tuple[str, str], dict[str, str]] | None = None
    ) -> None:
        """Replace all series. `series` is (owner_id, title) -> {bar_index: value}."""
        self._series = {k: dict(v) for k, v in series.items()}
        self._meta = {k: dict(v) for k, v in (meta or {}).items()}

    def set_series_legacy(self, series: dict[str, dict[int, float]], meta: dict[str, dict[str, str]] | None = None) -> None:
        """Legacy title-only for tests."""
        # map title -> (title, title) owner==title
        conv = {(k, k): dict(v) for k, v in series.items()}
        conv_meta = {(k, k): dict(v) for k, v in (meta or {}).items()}
        self.set_series(conv, conv_meta)

    def set_visibility_checker(self, checker: Any) -> None:
        """Set callable(title)->bool for per-series visibility (e.g., widget.is_indicator_visible)."""
        self._is_visible = checker

    def set_from_chart_series(self, chart_series: tuple) -> None:
        """Set from BacktestResult.chart_series — owner-aware."""
        d: dict[tuple[str, str], dict[int, float]] = {}
        m: dict[tuple[str, str], dict[str, str]] = {}
        for cs in chart_series:
            try:
                title = str(getattr(cs, "title", "plot"))
                vals = getattr(cs, "values", ())
                mp: dict[int, float] = {}
                for item in vals:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        try:
                            mp[int(item[0])] = float(item[1])
                        except Exception:
                            continue
                    elif isinstance(item, dict):
                        try:
                            mp[int(item.get("bar_index", item.get("index", 0)))] = float(item.get("value", 0))
                        except Exception:
                            continue
                strat = str(getattr(cs, "strategy", "")).strip() or title
                # owner is strategy id/name, title is series title
                key = (strat, title)
                d[key] = mp
                m[key] = {
                    "style": str(getattr(cs, "style", "line")),
                    "extend": str(getattr(cs, "extend", "session")),
                    "strategy": strat,
                    "title": title,
                }
            except Exception:
                continue
        self.set_series(d, m)

    def add_from_chart_series(self, chart_series: tuple) -> None:
        """Add (append) chart series without clearing existing — for multiple active strategies."""
        # merge with existing
        d = dict(self._series)
        m = dict(self._meta)
        for cs in chart_series:
            try:
                title = str(getattr(cs, "title", "plot"))
                vals = getattr(cs, "values", ())
                mp: dict[int, float] = {}
                for item in vals:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        try:
                            mp[int(item[0])] = float(item[1])
                        except Exception:
                            continue
                strat = str(getattr(cs, "strategy", "")).strip() or title
                key = (strat, title)
                d[key] = mp
                m[key] = {
                    "style": str(getattr(cs, "style", "line")),
                    "extend": str(getattr(cs, "extend", "session")),
                    "strategy": strat,
                    "title": title,
                }
            except Exception:
                continue
        self.set_series(d, m)

    def remove_owner(self, owner_id: str) -> None:
        """Remove all series belonging to owner_id."""
        to_del = [k for k in self._series if k[0] == owner_id or k[1] == owner_id]
        for k in to_del:
            self._series.pop(k, None)
            self._meta.pop(k, None)

    def clear_owner(self, owner_id: str) -> None:
        self.remove_owner(owner_id)

    def clear(self) -> None:
        self._series.clear()
        self._meta.clear()

    def is_empty(self) -> bool:
        return not self._series

    def paint_overlay(self, painter, viewport: ChartViewport) -> None:  # type: ignore[no-untyped-def]
        if not self._series:
            return
        rect = QRectF(viewport.chart_rect)
        first, last = viewport.first, viewport.last
        low, high = viewport.price_low, viewport.price_high
        visible = set(range(first, min(last, len(viewport.bars))))
        for idx, (key, series) in enumerate(self._series.items()):
            if not series:
                continue
            # key is (owner_id, title) or legacy title
            if isinstance(key, tuple) and len(key) == 2:
                owner_id, title = key
            else:
                owner_id, title = str(key), str(key)
                key = (owner_id, title)
            if self._is_visible is not None:
                try:
                    meta = self._meta.get(key, {})
                    strat = meta.get("strategy", owner_id) if isinstance(meta, dict) else owner_id
                    controlling = strat if strat and strat != title else title
                    # for OBR plots, controlling is OBR (strategy), not REF HIGH
                    # check controlling visibility; if hidden, skip
                    if not self._is_visible(controlling):
                        continue
                except Exception:
                    pass
            color = _PLOT_COLORS[idx % len(_PLOT_COLORS)]
            # allow per-series color override via meta? keep simple for now
            pen = QPen(color, _PLOT_WIDTH, _PLOT_STYLE)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            # Collect points in visible range, break on gaps
            # need to handle session gaps: only connect consecutive bar_indices where both have values and are consecutive
            # For OBR, values exist for each bar in session after ref, so consecutive
            # For gap (e.g., bar 14 -> 17 missing 15,16), break
            sorted_items = sorted((k, v) for k, v in series.items() if k in visible)
            if not sorted_items:
                continue
            # Build segments: break when bar_index gap >1 or value is None
            segments: list[list[tuple[float, float]]] = []
            cur: list[tuple[float, float]] = []
            prev_idx: int | None = None
            for bar_idx, price in sorted_items:
                if prev_idx is not None and bar_idx != prev_idx + 1:
                    # gap -> break segment
                    if cur:
                        segments.append(cur)
                    cur = []
                x = _bar_x(bar_idx, first, last, rect)
                y = _price_y(price, low, high, rect)
                cur.append((x, y))
                prev_idx = bar_idx
            if cur:
                segments.append(cur)
            for seg in segments:
                if len(seg) < 2:
                    # single point: draw small dot?
                    if len(seg) == 1:
                        x, y = seg[0]
                        painter.drawEllipse(QRectF(x - 1.5, y - 1.5, 3, 3))
                    continue
                for i in range(len(seg) - 1):
                    x1, y1 = seg[i]
                    x2, y2 = seg[i + 1]
                    painter.drawLine(int(x1), int(y1), int(x2), int(y2))
