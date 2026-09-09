"""PlotRenderer — TradingView-style plot series painting.

Renders generic plot series produced by Python strategies's plot() primitive.
Each series is a dict of bar_index -> value, stable identity per title.
Handles session gaps (no line across missing bars), zoom/pan, and viewport.
Pure painting: no state, no calculation.

Marker style contract (generic, opt-in per series via meta ``style``):
``"marker_up"``, ``"marker_down"``, ``"marker_square"``, ``"marker"``,
``"marker_circle"``, ``"marker_circle_x"``, ``"marker_diamond"``,
``"marker_triangle_up"``, ``"marker_triangle_down"``,
``"marker_triangle_blue"`` or ``"marker_dot"``,
optionally followed by ``"|"`` + a label format, e.g.
``"marker_up|BUY {v:.2f}"``. The format supports ``{t}`` (series title),
``{v:.2f}``, ``{v:+.2f}`` and ``{v:,.2f}`` (point value); default is
``"{t} {v:.2f}"``. A trailing ``"|"`` with an empty format (or ``"|none"``)
means glyph-only: no text pill is painted (text defaults to none — a pill
appears only when the strategy explicitly requests text). Any other style
(including the default ``"line"``) keeps the historical line/ray/dot path
byte-identical. Markers paint one glyph (+ optional pill) per visible
point on its exact bar — no repaint, no future-bar use. Marker pills are
de-collided deterministically across all marker series ordered by explicit
render layer then paint order: the glyph never moves, only the pill nudges
vertically, with a thin stem when displaced.

Universal plot contract (strategy-owned, renderer-agnostic): strategies
emit generic ``PlotEvent`` instructions (LINE/RAY/SEGMENT/MARKER/SHAPE/
LABEL/ZONE/HORIZONTAL_LEVEL/VERTICAL_MARK/AREA with generic marker types);
``PlotStore`` transports/stores/indexes them, the viewport selects the
visible set, and this renderer only converts generic instructions into
pixels. The renderer never branches on strategy names or signal meanings.

Extend contract (generic, per series via meta ``extend``):
``"session"``/``"right"``/``"extend"``/``"horizontal"`` keep the legacy
uncapped horizontal rays for sparse series; ``"bars:N"`` (e.g.
``"bars:5"``) caps every ray at N candles from its originating bar;
anything else (including ``"none"``) keeps the dense connected path with
gap breaks and no ray overhang.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

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

# Marker + label-pill palette — same theme tokens as TradeOverlay labels.
# Pills render as solid marker-color fills with white text (reference style);
# glyphs use the same colors so each visual reads as one unit.
_MARKER_UP = QColor("#26a69a")
_MARKER_DOWN = QColor("#ef5350")
_MARKER_SQUARE = QColor("#ffb74d")
_MARKER_CIRCLE = QColor("#42a5f5")
_MARKER_CIRCLE_X = QColor("#ffb74d")
_MARKER_DIAMOND = QColor("#ab47bc")
_MARKER_TRIANGLE_BLUE = QColor("#42a5f5")
_MARKER_DOT = QColor("#8a93a6")
_LABEL_TEXT = QColor("#ffffff")

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


_MARKER_STYLES = (
    "marker",
    "marker_up",
    "marker_down",
    "marker_square",
    "marker_circle",
    "marker_circle_x",
    "marker_diamond",
    "marker_triangle_up",
    "marker_triangle_down",
    "marker_triangle_blue",
    "marker_dot",
)

# Generic render layers (lowest paints first) — no strategy semantics.
_LAYER_ORDER: dict[str, int] = {
    "background": 0,
    "zone": 10,
    "area": 10,
    "level": 20,
    "line": 30,
    "ray": 30,
    "segment": 40,
    "marker": 60,
    "label": 70,
    "overlay": 80,
}

# Level-of-detail: above this visible-bar count, text-less dense markers
# keep their glyphs but skip pill measuring (glyphs are never dropped).
_LOD_BAR_THRESHOLD = 5000
_LOD_PILL_THRESHOLD = 800


def _parse_extend(meta: Any) -> tuple[bool, int | None]:
    """Split a series ``extend`` into (ray_eligible, cap_bars).

    ``"session"`` → ``(True, None)`` (legacy uncapped ray);
    ``"bars:5"`` → ``(True, 5)`` (ray stops 5 candles from its origin);
    anything else → ``(False, None)`` (dense path, no overhang).
    """
    try:
        extend = str((meta or {}).get("extend", "none")).lower()
    except Exception:
        return False, None
    if extend in ("session", "right", "extend", "horizontal"):
        return True, None
    if extend.startswith("bars:"):
        try:
            cap = int(extend.split(":", 1)[1])
        except Exception:
            return False, None
        return (True, cap) if cap >= 1 else (False, None)
    return False, None


def _parse_marker_style(meta: Any) -> tuple[str | None, str | None]:  # fmt may be None (glyph-only)
    """Split a series ``style`` into (marker kind, label format or None).

    ``"marker_up|BUY {v:.2f}"`` → ``("marker_up", "BUY {v:.2f}")``.
    ``"marker_circle_x|"`` / ``"marker_circle_x|none"`` → glyph-only
    ``("marker_circle_x", None)`` (no text pill unless requested).
    Anything else (``"line"``, missing, unknown) → ``(None, None)`` so the
    caller keeps the historical line/ray/dot path.
    """
    try:
        style = str((meta or {}).get("style", "line"))
    except Exception:
        return None, ""
    kind, sep, fmt = style.partition("|")
    if kind not in _MARKER_STYLES:
        return None, ""
    if not sep:
        return kind, "{t} {v:.2f}"
    cleaned = fmt.strip()
    if not cleaned or cleaned.lower() == "none":
        return kind, None
    return kind, cleaned


def _format_marker_label(fmt: str, title: str, value: float) -> str:
    """Render a marker label — fixed token set, no eval."""
    text = fmt.replace("{t}", title)
    text = text.replace("{v:.2f}", f"{value:.2f}")
    text = text.replace("{v:+.2f}", f"{value:+.2f}")
    text = text.replace("{v:,.2f}", f"{value:,.2f}")
    return text


def _marker_priority(title: str) -> int:
    """Paint order for marker pills: entries first, then exits, then notes.

    Generic title convention (documented, opt-in by naming): BUY/SELL are
    entries, EXIT* are exits, NO TRADE is a note; anything else is neutral.
    Priority only decides which pill keeps its preferred spot on overlap —
    nothing is ever hidden.
    """
    upper = str(title).upper()
    if upper in ("BUY", "SELL"):
        return 0
    if upper.startswith("EXIT"):
        return 1
    if upper == "NO TRADE":
        return 2
    return 3


def _marker_order(title: str, layer: int) -> int:
    """Global pill order: explicit generic render layer first, title second.

    The universal path passes each plot's layer (background < zone < level
    < lines < markers < labels < overlays) so cross-strategy ordering never
    depends on strategy naming; the legacy title order only breaks ties
    inside one layer, preserving historical layouts.
    """
    try:
        base = int(layer)
    except (TypeError, ValueError):
        base = 60
    return base * 10 + _marker_priority(title)


@dataclass(frozen=True)
class _MarkerJob:
    """One marker awaiting pill placement — the glyph position is final."""

    x: float
    y: float
    width: float
    height: float
    below: bool  # preferred pill side
    priority: int
    order: int  # global paint order (series order, then bar order)
    kind: str
    text: str


def _collect_marker_jobs(
    jobs: list[_MarkerJob],
    series: dict[int, float],
    title: str,
    kind: str,
    fmt: str | None,
    measure: Any,
    first: int,
    last: int,
    low: float,
    high: float,
    rect: QRectF,
    layer: int = 60,
) -> None:
    """Append one job per visible point; glyph coordinates are exact.

    ``fmt`` None means glyph-only (no text pill — the strategy requested no
    text). Only visible bars are collected (viewport culling).
    """
    # Reference style (TradingView-like): UP-family pills sit below the anchor,
    # DOWN-family above; neutral glyphs default below. Screen-space only.
    below = kind not in ("marker_down", "marker_triangle_down")
    priority = _marker_order(title, layer)
    for bar_idx, price in sorted(series.items()):
        if not (first <= bar_idx < last):
            continue
        try:
            value = float(price)
        except Exception:
            continue
        if not math.isfinite(value):
            continue
        if fmt is None:
            text, width, height = "", 0.0, 0.0
        else:
            text = _format_marker_label(fmt, title, value)
            width, height = measure(text)
        jobs.append(
            _MarkerJob(
                x=_bar_x(bar_idx, first, last, rect),
                y=_price_y(value, low, high, rect),
                width=width,
                height=height,
                below=below,
                priority=priority,
                order=len(jobs),
                kind=kind,
                text=text,
            )
        )


def _layout_marker_pills(
    jobs: list[_MarkerJob], bounds: QRectF | None = None
) -> list[tuple[_MarkerJob, float, float, bool]]:
    """Place pills deterministically; returns (job, pill_x, pill_y, displaced).

    The glyph (job.x/job.y) never moves — only the pill nudges vertically.
    Order is fixed (priority, then paint order), candidates are fixed, so the
    same jobs always produce the same layout. When ``bounds`` (the chart rect)
    is given, pills are additionally clamped horizontally inside it so edge
    markers stay fully visible; clamping marks the pill displaced.
    """
    placed: list[tuple[_MarkerJob, float, float, bool]] = []
    taken: list[QRectF] = []
    for job in sorted(jobs, key=lambda j: (j.priority, j.order)):
        if not job.text:
            # Glyph-only (strategy requested no text): no pill, no collision.
            placed.append((job, job.x, job.y, False))
            continue
        base_x = job.x - job.width / 2
        gap = 9.0  # glyph half-size + margin (matches the legacy offsets)
        above = (base_x, job.y - gap - job.height)
        below = (base_x, job.y + gap)
        candidates = [above if not job.below else below, below if not job.below else above]
        step = 1
        while len(candidates) < 6:
            candidates.append((base_x, job.y - gap - job.height - step * (job.height + 3)))
            candidates.append((base_x, job.y + gap + step * (job.height + 3)))
            step += 1
        chosen = candidates[0]
        displaced = False
        for num, (cx, cy) in enumerate(candidates):
            rect = QRectF(cx, cy, job.width, job.height).adjusted(-1, -1, 1, 1)
            if not any(rect.intersects(other) for other in taken):
                chosen = (cx, cy)
                displaced = num > 0
                break
        else:
            displaced = True
        if bounds is not None and bounds.isValid() and job.width > 0:
            lo_x = bounds.left() + 1.0
            hi_x = max(lo_x, bounds.right() - job.width - 1.0)
            clamped_x = min(max(chosen[0], lo_x), hi_x)
            if clamped_x != chosen[0]:
                chosen = (clamped_x, chosen[1])
                displaced = True
        taken.append(QRectF(chosen[0], chosen[1], job.width, job.height))
        placed.append((job, chosen[0], chosen[1], displaced))
    return placed


def _marker_color(kind: str) -> QColor:
    if kind in ("marker_down", "marker_triangle_down"):
        return _MARKER_DOWN
    if kind in ("marker_square", "marker_circle_x"):
        return _MARKER_SQUARE
    if kind == "marker_circle":
        return _MARKER_CIRCLE
    if kind == "marker_diamond":
        return _MARKER_DIAMOND
    if kind == "marker_triangle_blue":
        return _MARKER_TRIANGLE_BLUE
    if kind == "marker_dot":
        return _MARKER_DOT
    return _MARKER_UP


def _draw_placed_markers(painter: Any, placed: list[tuple[_MarkerJob, float, float, bool]]) -> None:
    """Draw glyphs on their exact bars, pills at their placed spots.

    Glyphs are batched by callers per marker kind where practical; every
    glyph paints at its exact logical coordinate. Jobs with empty text are
    glyph-only (no pill, no stem). The marker font is set for the whole call
    so pill text is always drawn with the font it was measured with — a
    mismatched font would clip the text inside its own pill.
    """
    painter.save()
    with contextlib.suppress(Exception):
        marker_font = QFont("Segoe UI", 7)
        marker_font.setBold(True)
        painter.setFont(marker_font)
    for job, px, py, displaced in placed:
        color = _marker_color(job.kind)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = 7.0
        if job.kind == "marker_square":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRect(QRectF(job.x - size / 2, job.y - size / 2, size, size))
        elif job.kind == "marker_diamond":
            points = [
                (job.x, job.y - size),
                (job.x + size * 0.7, job.y),
                (job.x, job.y + size),
                (job.x - size * 0.7, job.y),
            ]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawPolygon(QPolygonF([QPointF(a, b) for a, b in points]))
        elif job.kind == "marker_circle":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(job.x - size / 2, job.y - size / 2, size, size))
        elif job.kind == "marker_circle_x":
            painter.setPen(QPen(color, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(job.x - size / 2, job.y - size / 2, size, size))
            arm = size * 0.32
            painter.drawLine(int(job.x - arm), int(job.y - arm), int(job.x + arm), int(job.y + arm))
            painter.drawLine(int(job.x - arm), int(job.y + arm), int(job.x + arm), int(job.y - arm))
        elif job.kind == "marker_dot":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(job.x - 2, job.y - 2, 4, 4))
        else:
            # Directional triangles are tip-anchored: the tip touches the
            # exact logical coordinate and the body extends outward, so the
            # marker hugs its candle instead of floating centered on it.
            # Screen-space only — logical coordinates never move.
            if job.kind in ("marker_down", "marker_triangle_down"):
                points = [
                    (job.x, job.y),
                    (job.x - size * 0.6, job.y - size),
                    (job.x + size * 0.6, job.y - size),
                ]
            else:
                points = [
                    (job.x, job.y),
                    (job.x - size * 0.6, job.y + size),
                    (job.x + size * 0.6, job.y + size),
                ]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawPolygon(QPolygonF([QPointF(a, b) for a, b in points]))
        if not job.text:
            painter.restore()
            continue
        pill = QRectF(px, py, job.width, job.height)
        if displaced:
            # thin stem keeps a nudged pill visibly attached to its candle
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            edge_y = py if py > job.y else py + job.height
            painter.drawLine(int(job.x), int(job.y), int(job.x), int(edge_y))
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill, 2, 2)
        painter.setPen(_LABEL_TEXT)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, job.text)
        painter.restore()
    painter.restore()


def _paint_markers(
    painter: Any,
    series: dict[int, float],
    title: str,
    kind: str,
    fmt: str | None,
    first: int,
    last: int,
    low: float,
    high: float,
    rect: QRectF,
) -> None:
    """Draw one glyph (+ optional pill) per visible point on its exact bar."""
    font = QFont("Segoe UI", 7)
    font.setBold(True)
    painter.save()
    painter.setFont(font)
    metrics = painter.fontMetrics()

    def _measure(text: str) -> tuple[float, float]:
        # Compact reference-style pill: tight padding, small radius.
        return (metrics.horizontalAdvance(text) + 6, metrics.height() + 2)

    painter.restore()
    jobs: list[_MarkerJob] = []
    _collect_marker_jobs(jobs, series, title, kind, fmt, _measure, first, last, low, high, rect)
    _draw_placed_markers(painter, _layout_marker_pills(jobs, rect))


_PLOT_TYPES = frozenset(
    {
        "LINE",
        "RAY",
        "SEGMENT",
        "MARKER",
        "SHAPE",
        "LABEL",
        "ZONE",
        "HORIZONTAL_LEVEL",
        "VERTICAL_MARK",
        "AREA",
    }
)

_MARKER_TYPES = frozenset(
    {
        "UP_ARROW",
        "DOWN_ARROW",
        "CIRCLE",
        "CIRCLE_X",
        "SQUARE",
        "DIAMOND",
        "TRIANGLE_UP",
        "TRIANGLE_DOWN",
        "TRIANGLE_BLUE",
        "DOT",
    }
)

_LIFECYCLES = frozenset({"ACTIVE", "COMPLETE", "HIDDEN", "REMOVED"})

_UNIVERSAL_MARKER_STYLE: dict[str, str] = {
    "UP_ARROW": "marker_up",
    "DOWN_ARROW": "marker_down",
    "CIRCLE": "marker_circle",
    "CIRCLE_X": "marker_circle_x",
    "SQUARE": "marker_square",
    "DIAMOND": "marker_diamond",
    "TRIANGLE_UP": "marker_triangle_up",
    "TRIANGLE_DOWN": "marker_triangle_down",
    "TRIANGLE_BLUE": "marker_triangle_blue",
    "DOT": "marker_dot",
}


@dataclass(frozen=True)
class _StoredPlot:
    """Compact immutable plot record — logical coordinates only, no pixels."""

    event_id: str
    source_strategy: str
    plot_id: str
    plot_type: str
    symbol: str
    timeframe: str
    bar_index: int | None
    start_bar: int | None
    end_bar: int | None
    price: float | None
    start_price: float | None
    end_price: float | None
    timestamp: str | None
    marker_type: str | None
    text: str | None
    layer: int
    lifecycle: str
    version: int
    extend_bars: int | None

    @property
    def anchor_bar(self) -> int:
        if self.bar_index is not None:
            return self.bar_index
        if self.start_bar is not None:
            return self.start_bar
        return 0

    @property
    def covered(self) -> tuple[int, int]:
        if self.bar_index is not None and self.start_bar is None and self.end_bar is None:
            return (self.bar_index, self.bar_index)
        first = self.start_bar if self.start_bar is not None else (self.bar_index or 0)
        last = self.end_bar if self.end_bar is not None else first
        if self.plot_type == "RAY" and self.extend_bars is not None:
            last = max(last, first + self.extend_bars - 1)
        return (first, last)


def _coerce_plot_record(event: Any) -> _StoredPlot | None:
    """Validate one duck-typed plot event into a record; None when invalid.

    Accepts strategy ``PlotEvent`` objects or ``to_dict()`` dicts without
    importing the strategy module (no runtime chart -> strategy coupling).
    Malformed plots are skipped (error isolation) — never raised.
    """
    try:
        if isinstance(event, dict):
            get = event.get
            val = lambda key, default=None: get(key, default)  # noqa: E731
        else:
            val = lambda key, default=None: getattr(event, key, default)  # noqa: E731
        plot_type = val("plot_type", None)
        if not isinstance(plot_type, str):
            name = getattr(plot_type, "value", getattr(plot_type, "name", None))
            plot_type = str(name) if name is not None else None
        plot_type = str(plot_type or "").upper()
        if plot_type not in _PLOT_TYPES:
            return None
        marker_type = val("marker_type", None)
        if marker_type is not None and not isinstance(marker_type, str):
            marker_type = getattr(marker_type, "value", getattr(marker_type, "name", None))
            marker_type = str(marker_type) if marker_type is not None else None
        if isinstance(marker_type, str):
            marker_type = marker_type.upper()
            if marker_type not in _MARKER_TYPES:
                return None
        lifecycle = val("lifecycle", "ACTIVE")
        if not isinstance(lifecycle, str):
            lifecycle = getattr(lifecycle, "value", getattr(lifecycle, "name", "ACTIVE"))
        lifecycle = str(lifecycle or "ACTIVE").upper()
        if lifecycle not in _LIFECYCLES:
            return None
        layer = val("layer", None)
        if not isinstance(layer, int):
            layer = getattr(layer, "value", None)
        try:
            layer = int(layer) if layer is not None else 60
        except (TypeError, ValueError):
            return None

        def _bar(value: Any) -> int | None:
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return value if value >= 0 else None

        def _num(value: Any) -> float | None:
            if value is None:
                return None
            if isinstance(value, bool):
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        bar_index = _bar(val("bar_index", None))
        start_bar = _bar(val("start_bar", None))
        end_bar = _bar(val("end_bar", None))
        price = _num(val("price", None))
        start_price = _num(val("start_price", None))
        end_price = _num(val("end_price", None))
        if plot_type == "MARKER":
            if marker_type is None or bar_index is None or price is None:
                return None
        elif plot_type in ("SHAPE", "LABEL"):
            if bar_index is None or price is None:
                return None
        elif plot_type in ("LINE", "SEGMENT"):
            if start_bar is None or start_price is None or end_bar is None or end_price is None:
                return None
            if end_bar < start_bar:
                return None
        elif plot_type == "RAY":
            if start_bar is None or start_price is None:
                return None
        elif plot_type == "HORIZONTAL_LEVEL":
            if start_bar is None or end_bar is None or start_price is None:
                return None
        elif plot_type == "VERTICAL_MARK":
            if bar_index is None:
                return None
        elif plot_type in ("ZONE", "AREA") and (
            start_bar is None or end_bar is None or start_price is None or end_price is None
        ):
            return None
        extend_bars = val("extend_bars", None)
        if extend_bars is not None:
            if isinstance(extend_bars, bool) or not isinstance(extend_bars, int):
                return None
            if extend_bars < 1:
                return None
        version = val("version", 1)
        try:
            version = int(version)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if version < 1:
            return None
        text = val("text", None)
        if text is not None and not isinstance(text, str):
            return None
        event_id = val("event_id", None)
        source_strategy = val("source_strategy", None)
        plot_id = val("plot_id", None)
        if not event_id or not source_strategy or not plot_id:
            return None
        timestamp = val("timestamp", None)
        if timestamp is not None and not isinstance(timestamp, str):
            return None
        return _StoredPlot(
            event_id=str(event_id),
            source_strategy=str(source_strategy),
            plot_id=str(plot_id),
            plot_type=plot_type,
            symbol=str(val("symbol", "") or ""),
            timeframe=str(val("timeframe", "") or ""),
            bar_index=bar_index,
            start_bar=start_bar,
            end_bar=end_bar,
            price=price,
            start_price=start_price,
            end_price=end_price,
            timestamp=timestamp,
            marker_type=marker_type,
            text=text,
            layer=layer,
            lifecycle=lifecycle,
            version=version,
            extend_bars=extend_bars,
        )
    except Exception:
        return None


class PlotStore:
    """Lightweight universal plot event store — transport/storage/index/query.

    Stores compact logical plot records (never rendered objects). Multi-dim
    indexes (strategy / symbol / timeframe / type / lifecycle + bar-sorted
    order) serve viewport queries without scanning history. Pure data: no
    Qt, no strategy import, no trading logic — safe on any thread for
    ingestion, read on the paint thread via immutable snapshots.

    GPU-ready: records are immutable value objects; a future batched/GPU
    renderer can consume the same visible-set tuples without contract
    changes. Zero-copy: queries return record references, never rebuilt
    geometry.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, _StoredPlot] = {}
        self._by_strategy: dict[str, set[str]] = {}
        self._by_type: dict[str, set[str]] = {}
        self._sorted: list[tuple[int, str]] = []
        self._dirty: tuple[int, int] | None = None
        self._last_ingest_ms: float = 0.0
        self._last_query_ms: float = 0.0
        self._total_ingested: int = 0
        self._dropped: int = 0

    def __len__(self) -> int:
        return len(self._by_id)

    def ingest(self, event: Any) -> str | None:
        """Validate + store one plot event; None when malformed (no crash)."""
        import time

        start = time.perf_counter()
        try:
            record = _coerce_plot_record(event)
            if record is None:
                self._dropped += 1
                return None
            previous = self._by_id.get(record.event_id)
            if previous is not None and previous.version >= record.version:
                return record.event_id
            self._by_id[record.event_id] = record
            self._by_strategy.setdefault(record.source_strategy, set()).add(record.event_id)
            self._by_type.setdefault(record.plot_type, set()).add(record.event_id)
            anchor = record.anchor_bar
            if previous is None:
                import bisect

                bisect.insort(self._sorted, (anchor, record.event_id))
            elif previous.anchor_bar != anchor:
                with contextlib.suppress(ValueError):
                    self._sorted.remove((previous.anchor_bar, record.event_id))
                import bisect

                bisect.insort(self._sorted, (anchor, record.event_id))
            first, last = record.covered
            if self._dirty is None:
                self._dirty = (first, last)
            else:
                self._dirty = (min(self._dirty[0], first), max(self._dirty[1], last))
            self._total_ingested += 1
            return record.event_id
        finally:
            import time as _time

            self._last_ingest_ms = (_time.perf_counter() - start) * 1000.0

    def ingest_batch(self, events: Any) -> list[str]:
        """Atomically validate then apply a frame of plot events.

        All valid events land together (one logical frame, no half-updated
        chart); malformed entries are skipped without losing the batch.
        """
        records: list[_StoredPlot] = []
        for event in events or ():
            record = _coerce_plot_record(event)
            if record is not None:
                records.append(record)
            else:
                self._dropped += 1
        applied: list[str] = []
        for record in records:
            previous = self._by_id.get(record.event_id)
            if previous is not None and previous.version >= record.version:
                applied.append(record.event_id)
                continue
            self._by_id[record.event_id] = record
            self._by_strategy.setdefault(record.source_strategy, set()).add(record.event_id)
            self._by_type.setdefault(record.plot_type, set()).add(record.event_id)
            if previous is None:
                self._sorted.append((record.anchor_bar, record.event_id))
            elif previous.anchor_bar != record.anchor_bar:
                with contextlib.suppress(ValueError):
                    self._sorted.remove((previous.anchor_bar, record.event_id))
                self._sorted.append((record.anchor_bar, record.event_id))
            applied.append(record.event_id)
        self._sorted.sort()
        if records:
            first = min(record.covered[0] for record in records)
            last = max(record.covered[1] for record in records)
            if self._dirty is None:
                self._dirty = (first, last)
            else:
                self._dirty = (min(self._dirty[0], first), max(self._dirty[1], last))
            self._total_ingested += len(records)
        return applied

    def update(self, event_id: str, **changes: Any) -> bool:
        """Version one live plot; False when unknown or invalid."""
        current = self._by_id.get(str(event_id))
        if current is None:
            return False
        import dataclasses

        allowed = {
            "bar_index",
            "start_bar",
            "end_bar",
            "price",
            "start_price",
            "end_price",
            "timestamp",
            "text",
            "lifecycle",
            "extend_bars",
            "layer",
            "marker_type",
        }
        safe = {key: value for key, value in changes.items() if key in allowed}
        candidate = dataclasses.replace(current, version=current.version + 1, **safe)  # type: ignore[arg-type]
        return self.ingest(candidate) is not None

    def remove(self, event_id: str) -> bool:
        """Mark one plot REMOVED (history kept, rendering skips)."""
        current = self._by_id.get(str(event_id))
        if current is None:
            return False
        return self.update(event_id, lifecycle="REMOVED")

    def remove_strategy(self, source_strategy: str) -> int:
        """Drop every plot of one strategy (visibility/deletion, no recalc)."""
        ids = set(self._by_strategy.get(str(source_strategy), ()))
        for event_id in ids:
            record = self._by_id.pop(event_id, None)
            if record is None:
                continue
            with contextlib.suppress(ValueError):
                self._sorted.remove((record.anchor_bar, event_id))
            bucket = self._by_type.get(record.plot_type)
            if bucket is not None:
                bucket.discard(event_id)
        self._by_strategy.pop(str(source_strategy), None)
        return len(ids)

    def clear(self) -> None:
        self._by_id.clear()
        self._by_strategy.clear()
        self._by_type.clear()
        self._sorted.clear()
        self._dirty = None

    def query_visible(
        self,
        first: int,
        last: int,
        source_strategy: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        plot_types: tuple[str, ...] | None = None,
        include_hidden: bool = False,
    ) -> tuple[_StoredPlot, ...]:
        """Return visible plots for [first, last) — indexed, never full scan."""
        import time

        start = time.perf_counter()
        try:
            import bisect

            lo = bisect.bisect_left(self._sorted, (first, ""))
            hi = bisect.bisect_left(self._sorted, (last, ""))
            wanted_types = {str(kind).upper() for kind in plot_types} if plot_types else None
            out: list[_StoredPlot] = []
            for _, event_id in self._sorted[lo:hi]:
                record = self._by_id.get(event_id)
                if record is None:
                    continue
                if record.lifecycle in ("REMOVED", "HIDDEN") and not include_hidden:
                    continue
                if source_strategy is not None and record.source_strategy != source_strategy:
                    continue
                if symbol is not None and record.symbol != symbol:
                    continue
                if timeframe is not None and record.timeframe != timeframe:
                    continue
                if wanted_types is not None and record.plot_type not in wanted_types:
                    continue
                # Span plots starting before the viewport may still cover it.
                out.append(record)
            # Include spanning records anchored before `first` whose cover
            # reaches into the window (bounded scan of earlier anchors only).
            for anchor, event_id in reversed(self._sorted[:lo]):
                record = self._by_id.get(event_id)
                if record is None:
                    continue
                if record.bar_index is not None:
                    break
                if record.covered[1] < first:
                    if anchor < first - 100000:
                        break
                    continue
                if record.lifecycle in ("REMOVED", "HIDDEN") and not include_hidden:
                    continue
                if source_strategy is not None and record.source_strategy != source_strategy:
                    continue
                if wanted_types is not None and record.plot_type not in wanted_types:
                    continue
                out.append(record)
            out.sort(key=lambda item: (item.layer, item.anchor_bar, item.event_id))
            return tuple(out)
        finally:
            import time as _time

            self._last_query_ms = (_time.perf_counter() - start) * 1000.0

    def consume_dirty(self) -> tuple[int, int] | None:
        """Return + clear the affected bar range since the last frame."""
        dirty, self._dirty = self._dirty, None
        return dirty

    def stats(self) -> dict[str, Any]:
        """Measured telemetry (never fabricated): counts + last timings."""
        return {
            "total": len(self._by_id),
            "strategies": len(self._by_strategy),
            "ingested": self._total_ingested,
            "dropped": self._dropped,
            "last_ingest_ms": self._last_ingest_ms,
            "last_query_ms": self._last_query_ms,
        }


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
        # Universal plot pipeline: store -> viewport query -> batched render.
        self._store = PlotStore()
        self._text_cache: dict[str, tuple[float, float]] = {}
        self._last_visible_count: int = 0

    def set_series(
        self,
        series: dict[tuple[str, str], dict[int, float]],
        meta: dict[tuple[str, str], dict[str, str]] | None = None,
    ) -> None:
        """Replace all series. `series` is (owner_id, title) -> {bar_index: value}."""
        self._series = {k: dict(v) for k, v in series.items()}
        self._meta = {k: dict(v) for k, v in (meta or {}).items()}

    def set_series_legacy(
        self, series: dict[str, dict[int, float]], meta: dict[str, dict[str, str]] | None = None
    ) -> None:
        """Legacy title-only for tests."""
        # map title -> (title, title) owner==title
        conv = {(k, k): dict(v) for k, v in series.items()}
        conv_meta = {(k, k): dict(v) for k, v in (meta or {}).items()}
        self.set_series(conv, conv_meta)

    def set_visibility_checker(self, checker: Any) -> None:
        """Set callable for per-series visibility."""
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
                            mp[int(item.get("bar_index", item.get("index", 0)))] = float(
                                item.get("value", 0)
                            )
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
        # Hiding/removing a strategy filters its plots — never recalculates.
        with contextlib.suppress(Exception):
            self._store.remove_strategy(str(owner_id))

    def clear_owner(self, owner_id: str) -> None:
        self.remove_owner(owner_id)

    def clear(self) -> None:
        self._series.clear()
        self._meta.clear()
        self._store.clear()
        self._text_cache.clear()

    def is_empty(self) -> bool:
        return not self._series and len(self._store) == 0

    # ── universal plot pipeline (strategy-owned events, generic render) ──

    def ingest_plot_events(self, events: Any) -> list[str]:
        """Store strategy-owned plot events incrementally (no full rebuild)."""
        try:
            return self._store.ingest_batch(tuple(events or ()))
        except Exception:
            return []

    def update_plot(self, event_id: str, **changes: Any) -> bool:
        """Version one live plot without recreating visuals."""
        try:
            return self._store.update(str(event_id), **changes)
        except Exception:
            return False

    def remove_plot(self, event_id: str) -> bool:
        """Mark one plot REMOVED (history kept, rendering skips)."""
        try:
            return self._store.remove(str(event_id))
        except Exception:
            return False

    def total_plot_count(self) -> int:
        """Total stored universal plots (all strategies, all history)."""
        return len(self._store)

    def visible_plot_count(self, first: int, last: int) -> int:  # noqa: ARG002
        """Universal plots visible in [first, last) (last query result)."""
        return self._last_visible_count

    def plot_stats(self) -> dict[str, Any]:
        """Measured store telemetry: counts + last ingest/query timings."""
        stats = self._store.stats()
        stats["last_visible"] = self._last_visible_count
        return stats

    def consume_plot_dirty(self) -> tuple[int, int] | None:
        """Affected bar range since the last frame (dirty-region rendering)."""
        try:
            return self._store.consume_dirty()
        except Exception:
            return None

    def paint_overlay(self, painter, viewport: ChartViewport) -> None:  # type: ignore[no-untyped-def]
        if not self._series and len(self._store) == 0:
            return
        rect = QRectF(viewport.chart_rect)
        first, last = viewport.first, viewport.last
        low, high = viewport.price_low, viewport.price_high
        visible = set(range(first, min(last, len(viewport.bars))))
        # Marker pills de-collide across ALL marker series: collect first
        # (glyph positions stay exact), draw once after the lines so markers
        # always sit on top. Undisplaced output matches the legacy offsets.
        marker_jobs: list[_MarkerJob] = []
        font = QFont("Segoe UI", 7)
        font.setBold(True)
        for idx, (key, series) in enumerate(tuple(self._series.items())):
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
            # Marker series (opt-in via meta style) collect glyph jobs on
            # their exact bars and skip the line/ray path entirely.
            marker_meta = self._meta.get(key, {})
            marker_kind, marker_fmt = _parse_marker_style(marker_meta)
            if marker_kind is not None:
                painter.save()
                painter.setFont(font)
                _collect_marker_jobs(
                    marker_jobs,
                    series,
                    title,
                    marker_kind,
                    marker_fmt,
                    lambda text: (
                        painter.fontMetrics().horizontalAdvance(text) + 6,
                        painter.fontMetrics().height() + 2,
                    ),
                    first,
                    last,
                    low,
                    high,
                    rect,
                )
                painter.restore()
                continue
            # allow per-series color override via meta? keep simple for now
            pen = QPen(color, _PLOT_WIDTH, _PLOT_STYLE)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            # Generic extend handling: sparse level series (e.g., OBR REF HIGH/LOW)
            # have extend="session" and large gaps (~13 for 30m). They must be
            # drawn as horizontal rays from each plotted index to just before the
            # next plotted index (or to end of data), not as isolated dots.
            # extend="bars:N" caps every ray at N candles from its origin bar.
            # Dense series (gap 1) remain connected diagonally.
            meta = self._meta.get(key, {}) if isinstance(self._meta.get(key, {}), dict) else {}
            ray_eligible, ray_cap = _parse_extend(meta)
            sorted_all = sorted(series.items())
            # Heuristic: sparse if average gap > 2 and extend requests rays
            is_extended = False
            if ray_eligible:
                if len(sorted_all) >= 2:
                    gaps = [
                        sorted_all[i + 1][0] - sorted_all[i][0] for i in range(len(sorted_all) - 1)
                    ]
                    avg_gap = sum(gaps) / len(gaps) if gaps else 1
                    if avg_gap > 2:
                        is_extended = True
                elif len(sorted_all) == 1:
                    # single point with extend -> treat as ray
                    is_extended = True
            if is_extended:
                # Build horizontal segments: each plotted value extends to next_idx-1
                segments: list[list[tuple[float, float]]] = []
                total_bars = len(viewport.bars)
                for i, (bar_idx, price) in enumerate(sorted_all):
                    next_idx = sorted_all[i + 1][0] if i + 1 < len(sorted_all) else total_bars
                    seg_start = bar_idx
                    seg_end = next_idx - 1
                    if ray_cap is not None:
                        # cap measured from the ORIGIN bar, before clipping
                        seg_end = min(seg_end, bar_idx + ray_cap - 1)
                    # clip to visible window
                    if seg_end < first or seg_start >= last:
                        continue
                    seg_start = max(seg_start, first)
                    seg_end = min(seg_end, last - 1)
                    if seg_start > seg_end:
                        continue
                    # price outside viewport still draw (clipped) — keep y
                    y = _price_y(price, low, high, rect)
                    # if segment length 0 (single bar), draw as dot/small line for visibility
                    if seg_start == seg_end:
                        x = _bar_x(seg_start, first, last, rect)
                        # draw 1-bar wide horizontal tick
                        x2 = (
                            _bar_x(seg_start + 1, first, last, rect)
                            if seg_start + 1 < last
                            else x + 4
                        )
                        # if only single bar visible, draw short line
                        if abs(x2 - x) < 1:
                            painter.drawEllipse(QRectF(x - 1.5, y - 1.5, 3, 3))
                        else:
                            painter.drawLine(int(x), int(y), int(x2), int(y))
                    else:
                        x1 = _bar_x(seg_start, first, last, rect)
                        x2 = _bar_x(seg_end, first, last, rect)
                        # draw horizontal line across segment
                        # use full width from center of first to center of last
                        painter.drawLine(int(x1), int(y), int(x2), int(y))
                continue
            # Fallback: original gap-breaking connected logic for dense series
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
        # ── universal store plots: viewport query -> layer-ordered batches ──
        with contextlib.suppress(Exception):
            self._paint_store(painter, viewport, rect, first, last, low, high, marker_jobs)
        if marker_jobs:
            _draw_placed_markers(painter, _layout_marker_pills(marker_jobs, rect))

    def _paint_store(  # type: ignore[no-untyped-def]
        self,
        painter,
        viewport: ChartViewport,
        rect: QRectF,
        first: int,
        last: int,
        low: float,
        high: float,
        marker_jobs: list[_MarkerJob],
    ) -> None:
        """Render the visible universal plot set in generic layer order.

        Flow: index -> visible-range query -> visible plots -> batched
        render commands. Only the visible window is ever touched (pan/zoom
        re-queries, never recalculates strategy). One malformed record can
        never break the frame (error isolation per record).
        """
        try:
            records = self._store.query_visible(first, last)
        except Exception:
            return
        self._last_visible_count = len(records)
        if not records:
            return
        total_bars = len(viewport.bars)
        lod = (last - first) > _LOD_BAR_THRESHOLD

        def _strategy_visible(name: str) -> bool:
            if self._is_visible is None:
                return True
            try:
                return bool(self._is_visible(name))
            except Exception:
                return True

        def _measure(text: str) -> tuple[float, float]:
            hit = self._text_cache.get(text)
            if hit is not None:
                return hit
            try:
                metrics = painter.fontMetrics()
                size = (metrics.horizontalAdvance(text) + 6, metrics.height() + 2)
            except Exception:
                size = (float(len(text)) * 6.0 + 6.0, 16.0)
            if len(self._text_cache) < 4096:
                self._text_cache[text] = size
            return size

        # Marker font stays active for the whole collection pass so every
        # pill is measured with the same font it is drawn with.
        painter.save()
        with contextlib.suppress(Exception):
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        order = 0
        for record in records:
            order += 1
            try:
                if not _strategy_visible(record.source_strategy):
                    continue
                color = _PLOT_COLORS[(record.layer // 10 + order) % len(_PLOT_COLORS)]
                kind = record.plot_type
                if kind in ("MARKER", "SHAPE", "LABEL"):
                    style_kind = _UNIVERSAL_MARKER_STYLE.get(
                        str(record.marker_type or ""), "marker_square"
                    )
                    if kind == "LABEL":
                        style_kind = "marker_dot"
                    fmt = record.text
                    series: dict[int, float] = {}
                    bar = record.bar_index if record.bar_index is not None else record.anchor_bar
                    value = (
                        record.price if record.price is not None else (record.start_price or 0.0)
                    )
                    if not (first <= bar < last):
                        continue
                    series[int(bar)] = float(value)
                    _collect_marker_jobs(
                        marker_jobs,
                        series,
                        record.plot_id,
                        style_kind,
                        fmt,
                        _measure,
                        first,
                        last,
                        low,
                        high,
                        rect,
                        layer=record.layer,
                    )
                elif kind in ("ZONE", "AREA"):
                    start = max(int(record.start_bar or 0), first)
                    end = min(int(record.end_bar or 0), last - 1)
                    if end < start:
                        continue
                    top = _price_y(float(record.end_price or 0.0), low, high, rect)
                    bottom = _price_y(float(record.start_price or 0.0), low, high, rect)
                    if top > bottom:
                        top, bottom = bottom, top
                    x1 = _bar_x(start, first, last, rect)
                    x2 = _bar_x(end + 1, first, last, rect) if end + 1 < last else rect.right()
                    painter.save()
                    try:
                        if lod:
                            # Extreme zoom-out: bounding lines instead of fills.
                            painter.setPen(QPen(color, _PLOT_WIDTH, _PLOT_STYLE))
                            painter.drawLine(int(x1), int(top), int(x2), int(top))
                            painter.drawLine(int(x1), int(bottom), int(x2), int(bottom))
                        else:
                            fill = QColor(color)
                            fill.setAlpha(48)
                            painter.setPen(QPen(color, 1))
                            painter.setBrush(QBrush(fill))
                            painter.drawRect(
                                QRectF(x1, top, max(1.0, x2 - x1), max(1.0, bottom - top))
                            )
                    finally:
                        painter.restore()
                elif kind == "VERTICAL_MARK":
                    bar = int(record.bar_index if record.bar_index is not None else 0)
                    if not (first <= bar < last):
                        continue
                    x = _bar_x(bar, first, last, rect)
                    painter.save()
                    try:
                        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                        painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
                    finally:
                        painter.restore()
                else:
                    # LINE / SEGMENT / RAY / HORIZONTAL_LEVEL — horizontal or
                    # sloped spans drawn at exact logical coordinates.
                    start = int(record.start_bar or 0)
                    price_a = float(record.start_price or 0.0)
                    if kind in ("LINE", "SEGMENT"):
                        end = int(record.end_bar or start)
                        price_b = float(record.end_price or price_a)
                    elif kind == "HORIZONTAL_LEVEL":
                        end = int(record.end_bar or start)
                        price_b = price_a
                    else:  # RAY
                        cap = record.extend_bars
                        horizon = total_bars if cap is None else start + cap - 1
                        end = int(record.end_bar) if record.end_bar is not None else horizon
                        end = min(end, horizon)
                        price_b = (
                            float(record.end_price) if record.end_price is not None else price_a
                        )
                    if end < first or start >= last:
                        continue
                    start_c = max(start, first)
                    end_c = min(end, last - 1)
                    if start_c > end_c:
                        continue
                    y1 = _price_y(price_a, low, high, rect)
                    y2 = _price_y(price_b, low, high, rect)
                    x1 = _bar_x(start_c, first, last, rect)
                    x2 = _bar_x(end_c, first, last, rect)
                    painter.save()
                    try:
                        pen = QPen(color, _PLOT_WIDTH, _PLOT_STYLE)
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        painter.setPen(pen)
                        if start_c == end_c and y1 == y2:
                            x2b = (
                                _bar_x(start_c + 1, first, last, rect)
                                if start_c + 1 < last
                                else x1 + 4
                            )
                            painter.drawLine(int(x1), int(y1), int(x2b), int(y1))
                        else:
                            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
                    finally:
                        painter.restore()
            except Exception:
                continue
        painter.restore()
