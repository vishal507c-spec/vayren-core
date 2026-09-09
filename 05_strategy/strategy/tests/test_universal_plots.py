"""Universal strategy-owned plotting — contract, store, renderer, acceptance.

Covers the production-grade plotting architecture:

- Strategy owns WHAT/WHEN/WHERE/WHICH plot (OBR refIndex, BUY/SELL/NO-TRADE
  meaning all live in strategy logic); the plot contract transports, the
  store indexes, the viewport selects, the renderer only draws.
- The renderer never branches on strategy names or signal meanings: one
  dummy strategy's CIRCLE_X can mean anything without renderer changes.
- Exact coordinate preservation, same-bar multiplicity, visual-only
  extendBars, trading/visual API separation, lifecycle, determinism,
  viewport-bounded rendering at 100k-bar scale.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chart.models.chart_viewport import ChartViewport
from market.models.bar import Bar
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from strategy import (
    MarkerType,
    PlotEvent,
    PlotLifecycle,
    PlotType,
    PlotValidationError,
    RenderLayer,
    default_layer,
    make_event_id,
)
from strategy.models.parameters import StrategyParameters
from strategy.runtime import BarView
from strategy.strategies.base import PythonStrategy

# ── universal contract ───────────────────────────────────────────────


def test_contract_all_plot_types_validate() -> None:
    base: dict[str, Any] = {
        "event_id": "e1",
        "source_strategy": "ALPHA",
        "plot_id": "p",
        "symbol": "X",
        "timeframe": "15m",
    }
    assert (
        PlotEvent(
            plot_type=PlotType.MARKER,
            bar_index=5,
            price=10.0,
            marker_type=MarkerType.UP_ARROW,
            **base,
        ).text
        is None
    )
    assert (
        PlotEvent(
            plot_type=PlotType.LINE, start_bar=1, end_bar=3, start_price=1.0, end_price=2.0, **base
        ).anchor_bar
        == 1
    )
    assert PlotEvent(
        plot_type=PlotType.RAY, start_bar=1, start_price=1.0, extend_bars=5, **base
    ).covered_bars == (1, 5)
    assert (
        PlotEvent(
            plot_type=PlotType.SEGMENT,
            start_bar=1,
            end_bar=2,
            start_price=1.0,
            end_price=2.0,
            **base,
        ).plot_type
        == PlotType.SEGMENT
    )
    assert PlotEvent(
        plot_type=PlotType.HORIZONTAL_LEVEL, start_bar=2, end_bar=6, start_price=3.0, **base
    ).covered_bars == (2, 6)
    assert PlotEvent(plot_type=PlotType.VERTICAL_MARK, bar_index=4, **base).anchor_bar == 4
    assert (
        PlotEvent(
            plot_type=PlotType.ZONE, start_bar=1, end_bar=2, start_price=1.0, end_price=2.0, **base
        ).layer
        == RenderLayer.ZONE
    )
    assert (
        PlotEvent(
            plot_type=PlotType.AREA, start_bar=1, end_bar=2, start_price=1.0, end_price=2.0, **base
        ).plot_type
        == PlotType.AREA
    )
    assert (
        PlotEvent(plot_type=PlotType.SHAPE, bar_index=1, price=1.0, **base).plot_type
        == PlotType.SHAPE
    )
    assert (
        PlotEvent(plot_type=PlotType.LABEL, bar_index=1, price=1.0, text="hi", **base).text == "hi"
    )


def test_contract_rejects_invalid() -> None:
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="e",
            source_strategy="A",
            plot_id="p",
            plot_type=PlotType.MARKER,
            bar_index=1,
            price=1.0,
            marker_type=None,
        )  # type: ignore[arg-type]
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="e",
            source_strategy="A",
            plot_id="p",
            plot_type=PlotType.MARKER,
            bar_index=-1,
            price=1.0,
            marker_type=MarkerType.DOT,
        )
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="e",
            source_strategy="A",
            plot_id="p",
            plot_type=PlotType.MARKER,
            bar_index=1,
            price=float("nan"),
            marker_type=MarkerType.DOT,
        )
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="",
            source_strategy="A",
            plot_id="p",
            plot_type=PlotType.LABEL,
            bar_index=1,
            price=1.0,
            text="t",
        )
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="e",
            source_strategy="A",
            plot_id="p",
            plot_type=PlotType.LINE,
            start_bar=5,
            end_bar=2,
            start_price=1.0,
            end_price=2.0,
        )


def test_contract_text_defaults_to_none() -> None:
    event = PlotEvent(
        event_id="e",
        source_strategy="A",
        plot_id="nt",
        plot_type=PlotType.MARKER,
        bar_index=152,
        price=247.35,
        marker_type=MarkerType.CIRCLE_X,
    )
    assert event.text is None


def test_event_ids_deterministic() -> None:
    first = make_event_id("OBR", "X", "15m", 152, "ref_high")
    second = make_event_id("OBR", "X", "15m", 152, "ref_high")
    other_bar = make_event_id("OBR", "X", "15m", 153, "ref_high")
    other_plot = make_event_id("OBR", "X", "15m", 152, "ref_low")
    assert first == second
    assert first != other_bar
    assert first != other_plot


def test_event_versioning_preserves_coordinates() -> None:
    event = PlotEvent(
        event_id="e",
        source_strategy="A",
        plot_id="lvl",
        plot_type=PlotType.HORIZONTAL_LEVEL,
        start_bar=3,
        end_bar=7,
        start_price=100.0,
    )
    updated = event.with_update(end_bar=9)
    assert updated.version == 2
    assert updated.start_bar == 3
    assert updated.start_price == 100.0
    assert updated.end_bar == 9
    assert updated.lifecycle == PlotLifecycle.ACTIVE


def test_event_serialization_round_trip() -> None:
    event = PlotEvent(
        event_id="e9",
        source_strategy="VWAP",
        plot_id="entry_152",
        plot_type=PlotType.MARKER,
        symbol="TCS",
        timeframe="15m",
        bar_index=152,
        price=247.35,
        marker_type=MarkerType.UP_ARROW,
        timestamp="2026-09-08 09:15:00",
        text=None,
        dependencies=("sl_152",),
        metadata=(("k", "v"),),
    )
    assert PlotEvent.from_dict(event.to_dict()) == event
    with pytest.raises(PlotValidationError):
        PlotEvent.from_dict({"plot_type": "NOPE"})


def test_default_layer_is_generic() -> None:
    assert default_layer(PlotType.MARKER).value < default_layer(PlotType.LABEL).value
    assert default_layer(PlotType.ZONE) == RenderLayer.ZONE


# ── strategy-owned plot API ──────────────────────────────────────────


def _logic(name: str = "OBR") -> PythonStrategy:
    class _S(PythonStrategy):
        def on_bar_logic(self, view: BarView) -> None:  # noqa: ARG002
            return None

    logic = _S(StrategyParameters({}))
    logic.set_owner_id(name)
    logic._current_bar_index = 152
    return logic


def test_strategy_plot_api_exact_coordinates() -> None:
    logic = _logic("OBR")
    marker_id = logic.plot_marker(152, 247.35, MarkerType.CIRCLE_X, plot_id="no_trade_152")
    level_id = logic.plot_level(152, 247.35, end_bar=156, plot_id="ref_high", extend_bars=5)
    events = {event.event_id: event for event in logic.get_plot_events()}
    marker = events[marker_id]
    assert (marker.bar_index, marker.price) == (152, 247.35)
    assert marker.marker_type == MarkerType.CIRCLE_X
    assert marker.text is None
    assert marker.source_strategy == "OBR"
    level = events[level_id]
    assert (level.start_bar, level.start_price, level.end_bar) == (152, 247.35, 156)
    assert level.extend_bars == 5


def test_strategy_same_bar_multiple_events_coexist() -> None:
    logic = _logic("ORB")
    logic.plot_marker(152, 100.0, MarkerType.UP_ARROW, plot_id="entry")
    logic.plot_marker(152, 99.0, MarkerType.DOWN_ARROW, plot_id="exit")
    logic.plot_level(152, 98.0, plot_id="sl")
    logic.plot_marker(152, 101.0, MarkerType.CIRCLE_X, plot_id="note")
    logic.plot_zone(152, 155, 99.0, 101.0, plot_id="zone")
    events = logic.get_plot_events()
    assert len(events) == 5
    assert len({event.event_id for event in events}) == 5


def test_strategy_plot_lifecycle() -> None:
    logic = _logic("VWAP")
    event_id = logic.plot_ray(10, 50.0, plot_id="trail", extend_bars=5)
    assert logic.update_plot(event_id, start_price=51.0) == event_id
    updated = next(event for event in logic.get_plot_events() if event.event_id == event_id)
    assert updated.version == 2
    assert updated.start_price == 51.0
    assert logic.remove_plot(event_id) is True
    removed = next(event for event in logic.get_plot_events() if event.event_id == event_id)
    assert removed.lifecycle == PlotLifecycle.REMOVED
    assert logic.update_plot("missing", price=1.0) is None
    assert logic.remove_plot("missing") is False


def test_muted_signal_bars_api() -> None:
    """Generic visual-silence hook: exact bars, validated, deterministic."""
    logic = _logic("OBR")
    assert logic.get_muted_signal_bars() == ()
    assert logic.mute_signal_bar(6) is True
    assert logic.mute_signal_bar(6) is True  # idempotent
    assert logic.mute_signal_bar(-1) is False
    assert logic.mute_signal_bar(True) is False  # type: ignore[arg-type]
    assert logic.mute_signal_bar("6") is False  # type: ignore[arg-type]
    assert logic.get_muted_signal_bars() == (6,)
    assert logic.get_plot_events() == ()  # muting creates no plot event


def test_trading_and_plot_apis_are_separate() -> None:
    logic = _logic("OBR")
    logic.buy()
    logic.stop_loss(90.0)
    assert logic.get_plot_events() == ()
    logic.plot_marker(152, 100.0, MarkerType.UP_ARROW, plot_id="m")
    assert logic._pending_kind is not None  # trading state untouched by plots below
    pending_before = logic._pending_kind
    logic.plot_label(152, 100.0, "hello", plot_id="lbl")
    assert logic._pending_kind == pending_before
    assert len(logic.get_plot_events()) == 2


def test_plot_all_primitives() -> None:
    logic = _logic("AI_STRATEGY")
    logic.plot_marker(1, 10.0, MarkerType.DIAMOND, plot_id="a")
    logic.plot_line(1, 10.0, 3, 12.0, plot_id="b")
    logic.plot_ray(2, 11.0, plot_id="c", extend_bars=5)
    logic.plot_segment(2, 11.0, 4, 13.0, plot_id="d")
    logic.plot_zone(1, 4, 9.0, 13.0, plot_id="e")
    logic.plot_label(3, 12.0, "note", plot_id="f")
    logic.plot_level(1, 10.5, end_bar=6, plot_id="g")
    kinds = {event.plot_type for event in logic.get_plot_events()}
    assert kinds == {
        PlotType.MARKER,
        PlotType.LINE,
        PlotType.RAY,
        PlotType.SEGMENT,
        PlotType.ZONE,
        PlotType.LABEL,
        PlotType.HORIZONTAL_LEVEL,
    }


# ── plot store ───────────────────────────────────────────────────────


def _sample_dicts(count: int, strategy: str = "OBR") -> list[dict]:
    return [
        {
            "event_id": f"{strategy}:{i}:m:x",
            "source_strategy": strategy,
            "plot_id": f"m_{i}",
            "plot_type": "MARKER",
            "symbol": "X",
            "timeframe": "15m",
            "bar_index": i,
            "price": 100.0 + i,
            "marker_type": "CIRCLE_X",
            "text": None,
            "layer": 60,
            "lifecycle": "ACTIVE",
            "version": 1,
        }
        for i in range(count)
    ]


def test_store_ingest_query_filters() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    applied = store.ingest_batch(_sample_dicts(10, "OBR") + _sample_dicts(5, "VWAP"))
    assert len(applied) == 15
    assert len(store.query_visible(0, 5)) == 10  # 5 OBR (bars 0-4) + 5 VWAP (bars 0-4)
    assert len(store.query_visible(0, 100, source_strategy="VWAP")) == 5
    assert len(store.query_visible(0, 100, plot_types=("MARKER",))) == 15
    assert len(store.query_visible(0, 100, plot_types=("ZONE",))) == 0
    assert store.query_visible(0, 100, source_strategy="MISSING") == ()


def test_store_drops_malformed_without_crashing() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    assert store.ingest({"plot_type": "NOPE"}) is None
    assert store.ingest(None) is None
    assert store.ingest({"event_id": "x", "source_strategy": "A"}) is None
    assert len(store) == 0
    assert store.stats()["dropped"] >= 3


def test_store_versioning_and_lifecycle() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    event = _sample_dicts(1)[0]
    assert store.ingest(event) == event["event_id"]
    stale = dict(event, version=1)
    assert store.ingest(stale) == event["event_id"]  # idempotent re-ingest
    assert store.update(event["event_id"], price=999.0) is True
    assert store.query_visible(0, 5)[0].price == 999.0
    assert store.query_visible(0, 5)[0].version == 2
    assert store.remove(event["event_id"]) is True
    assert store.query_visible(0, 5) == ()
    assert len(store.query_visible(0, 5, include_hidden=True)) == 1
    assert store.update("missing") is False


def test_store_remove_strategy_and_dirty_region() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    store.ingest_batch(_sample_dicts(5, "OBR") + _sample_dicts(5, "VWAP"))
    dirty = store.consume_dirty()
    assert dirty == (0, 4)
    assert store.consume_dirty() is None
    assert store.remove_strategy("OBR") == 5
    assert len(store.query_visible(0, 100)) == 5


def test_store_span_visible_from_before_viewport() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    store.ingest(
        {
            "event_id": "OBR:10:ref:x",
            "source_strategy": "OBR",
            "plot_id": "ref",
            "plot_type": "HORIZONTAL_LEVEL",
            "symbol": "X",
            "timeframe": "15m",
            "start_bar": 10,
            "end_bar": 500,
            "start_price": 100.0,
            "layer": 30,
            "lifecycle": "ACTIVE",
            "version": 1,
        }
    )
    assert len(store.query_visible(200, 300)) == 1


# ── generic renderer ─────────────────────────────────────────────────


def _qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    return app


def _viewport(nbars: int, first: int, last: int) -> ChartViewport:
    bars = tuple(
        Bar(
            symbol="T",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10,
            timestamp=f"2026-01-05 09:{i:02d}:00",
        )
        for i in range(nbars)
    )
    return ChartViewport(
        bars=bars,
        first=first,
        last=last,
        price_low=90.0,
        price_high=110.0,
        volume_max=10,
        chart_rect=QRect(0, 0, 600, 300),
        volume_rect=QRect(0, 300, 600, 50),
        axis_rect=QRect(0, 350, 600, 20),
    )


def _painted_pixels(image: QImage, background: tuple[int, int, int]) -> int:
    count = 0
    for x in range(0, image.width(), 4):
        for y in range(0, image.height(), 4):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) != background:
                count += 1
    return count


def test_renderer_paints_all_plot_types() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay()
    overlay.ingest_plot_events(
        [
            {
                "event_id": f"S:1:{kind}:x",
                "source_strategy": "ALPHA",
                "plot_id": kind,
                "plot_type": kind,
                "symbol": "T",
                "timeframe": "15m",
                "bar_index": 5 if kind in ("MARKER", "SHAPE", "LABEL", "VERTICAL_MARK") else None,
                "start_bar": None if kind in ("MARKER", "SHAPE", "LABEL", "VERTICAL_MARK") else 2,
                "end_bar": None
                if kind in ("MARKER", "SHAPE", "LABEL", "VERTICAL_MARK", "RAY")
                else 8,
                "price": 100.0 if kind in ("MARKER", "SHAPE", "LABEL") else None,
                "start_price": 100.0
                if kind not in ("MARKER", "SHAPE", "LABEL", "VERTICAL_MARK")
                else None,
                "end_price": 101.0 if kind in ("LINE", "SEGMENT", "ZONE", "AREA") else None,
                "marker_type": "UP_ARROW" if kind == "MARKER" else None,
                "text": "lbl" if kind == "LABEL" else None,
                "layer": 60,
                "lifecycle": "ACTIVE",
                "version": 1,
            }
            for kind in (
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
            )
        ]
    )
    viewport = _viewport(30, 0, 30)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    assert _painted_pixels(image, (0x10, 0x14, 0x18)) > 10


def _paint_marker_image(marker: str, text: str | None) -> QImage:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay()
    overlay.ingest_plot_events(
        [
            {
                "event_id": "S:12:e:x",
                "source_strategy": "S",
                "plot_id": "e",
                "plot_type": "MARKER",
                "symbol": "T",
                "timeframe": "15m",
                "bar_index": 12,
                "price": 105.0,
                "marker_type": marker,
                "text": text,
                "layer": 60,
                "lifecycle": "ACTIVE",
                "version": 1,
            }
        ]
    )
    viewport = _viewport(30, 0, 30)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    return image


def test_up_triangle_tip_touches_anchor() -> None:
    """UP glyph: tip at the exact price, body below — candle-attached."""
    from chart.renderer.plot_renderer import _bar_x, _price_y
    from PySide6.QtCore import QRectF

    image = _paint_marker_image("UP_ARROW", None)
    viewport = _viewport(30, 0, 30)
    rect = QRectF(viewport.chart_rect)
    ax = _bar_x(12, 0, 30, rect)
    ay = _price_y(105.0, 90.0, 110.0, rect)
    rows = []
    for dx in range(-6, 7):
        for dy in range(-12, 14):
            color = image.pixelColor(int(ax) + dx, int(ay) + dy)
            if (color.red(), color.green(), color.blue()) == (0x26, 0xA6, 0x9A):
                rows.append(dy)
    assert rows, "triangle must paint"
    assert min(rows) >= -2, "no glyph above the anchor (tip touches it)"
    assert max(rows) <= 9, "compact body below the anchor"


def test_down_triangle_tip_touches_anchor() -> None:
    """DOWN glyph mirrors UP: tip at the exact price, body above."""
    from chart.renderer.plot_renderer import _bar_x, _price_y
    from PySide6.QtCore import QRectF

    image = _paint_marker_image("DOWN_ARROW", None)
    viewport = _viewport(30, 0, 30)
    rect = QRectF(viewport.chart_rect)
    ax = _bar_x(12, 0, 30, rect)
    ay = _price_y(105.0, 90.0, 110.0, rect)
    rows = []
    for dx in range(-6, 7):
        for dy in range(-14, 12):
            color = image.pixelColor(int(ax) + dx, int(ay) + dy)
            if (color.red(), color.green(), color.blue()) == (0xEF, 0x53, 0x50):
                rows.append(dy)
    assert rows, "triangle must paint"
    assert max(rows) <= 2, "no glyph below the anchor (tip touches it)"
    assert min(rows) >= -9, "compact body above the anchor"


def test_compact_pill_matches_tight_padding() -> None:
    """Owner-text pill: tight padding, small radius, hugging the glyph."""
    from chart.renderer.plot_renderer import _price_y
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFont, QFontMetrics

    image = _paint_marker_image("UP_ARROW", "BUY 104.00")
    viewport = _viewport(30, 0, 30)
    rect = QRectF(viewport.chart_rect)
    ay = _price_y(105.0, 90.0, 110.0, rect)
    xs, ys = [], []
    # Scan below the glyph body so the bbox measures the pill alone.
    for x in range(0, 600, 1):
        for y in range(int(ay) + 8, 300, 1):
            color = image.pixelColor(x, y)
            # Solid-fill pill shares the marker green; white text sits inside.
            if (color.red(), color.green(), color.blue()) == (0x26, 0xA6, 0x9A):
                xs.append(x)
                ys.append(y)
    assert xs and ys, "pill must paint"
    font = QFont("Segoe UI", 7)
    font.setBold(True)
    metrics = QFontMetrics(font)
    expected_w = metrics.horizontalAdvance("BUY 104.00") + 6
    expected_h = metrics.height() + 2
    assert max(xs) - min(xs) <= expected_w + 4
    assert max(ys) - min(ys) <= expected_h + 4
    # Pill hugs the glyph below the anchor (gap + glyph body, no drifting).
    assert min(ys) - ay <= 16


def test_pill_text_never_clipped_inside_own_pill() -> None:
    """Measure font == draw font: long labels (e.g. EOD) render in full."""
    from PySide6.QtGui import QFont, QFontMetrics

    image = _paint_marker_image("TRIANGLE_BLUE", "EOD 103.00 +1.7")
    xs = []
    for x in range(0, 600, 1):
        for y in range(0, 300, 1):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) == (0xFF, 0xFF, 0xFF):
                xs.append(x)
    assert xs, "label text must paint"
    font = QFont("Segoe UI", 7)
    font.setBold(True)
    expected = QFontMetrics(font).horizontalAdvance("EOD 103.00 +1.7")
    # White span covers essentially the full advance: no leading/trailing clip.
    assert max(xs) - min(xs) + 1 >= expected - 6


def test_edge_pill_clamped_inside_viewport() -> None:
    """Pills at the chart edge stay fully visible (marked displaced)."""
    from chart.renderer.plot_renderer import _layout_marker_pills, _MarkerJob
    from PySide6.QtCore import QRectF

    def _job(x: float) -> _MarkerJob:
        return _MarkerJob(
            x=x,
            y=150.0,
            width=120.0,
            height=18.0,
            below=True,
            priority=0,
            order=0,
            kind="marker_up",
            text="BUY 104.00",
        )

    bounds = QRectF(0, 0, 600, 300)
    (plain, _, _, plain_moved) = _layout_marker_pills([_job(590.0)])[0]
    assert plain_moved is False
    placed, px, _, moved = _layout_marker_pills([_job(590.0)], bounds)[0]
    assert placed.text == "BUY 104.00"
    assert px + placed.width <= bounds.right()
    assert moved is True


def test_renderer_paints_all_marker_types() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    kinds = (
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
    )
    for marker in kinds:
        overlay = PlotOverlay()
        overlay.ingest_plot_events(
            [
                {
                    "event_id": f"S:5:{marker}:x",
                    "source_strategy": "BETA",
                    "plot_id": marker,
                    "plot_type": "MARKER",
                    "symbol": "T",
                    "timeframe": "15m",
                    "bar_index": 5,
                    "price": 100.0,
                    "marker_type": marker,
                    "text": None,
                    "layer": 60,
                    "lifecycle": "ACTIVE",
                    "version": 1,
                }
            ]
        )
        viewport = _viewport(30, 0, 30)
        image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#101418"))
        painter = QPainter(image)
        overlay.paint_overlay(painter, viewport)
        painter.end()
        assert _painted_pixels(image, (0x10, 0x14, 0x18)) > 0, marker


def test_triangle_blue_paints_theme_blue() -> None:
    """TRIANGLE_BLUE renders the generic blue triangle (EOD-style)."""
    image = _paint_marker_image("TRIANGLE_BLUE", None)
    blue = 0
    for x in range(0, 600, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) == (0x42, 0xA5, 0xF5):
                blue += 1
    assert blue > 0, "blue triangle must paint theme-blue pixels"


def test_renderer_circle_x_glyph_only_has_no_text() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay()
    overlay.ingest_plot_events(
        [
            {
                "event_id": "OBR:152:nt:x",
                "source_strategy": "OBR",
                "plot_id": "nt",
                "plot_type": "MARKER",
                "symbol": "T",
                "timeframe": "15m",
                "bar_index": 5,
                "price": 100.0,
                "marker_type": "CIRCLE_X",
                "text": None,
                "layer": 60,
                "lifecycle": "ACTIVE",
                "version": 1,
            }
        ]
    )
    viewport = _viewport(30, 0, 30)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    amber = 0
    for x in range(0, 600, 2):
        for y in range(0, 300, 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) == (0xFF, 0xB7, 0x4D):
                amber += 1
    assert amber > 0


def test_renderer_has_no_strategy_branches() -> None:
    import inspect

    import chart.renderer.plot_renderer as module

    targets = [
        ("_coerce_plot_record", module._coerce_plot_record),
        ("PlotStore", module.PlotStore),
        ("_StoredPlot", module._StoredPlot),
        ("PlotOverlay._paint_store", module.PlotOverlay._paint_store),
    ]
    for name, target in targets:
        source = inspect.getsource(target)
        for forbidden in ('"OBR"', '"ORB"', '"VWAP"', "NO_TRADE", "SIGNAL_", "entryEvent"):
            assert forbidden not in source, f"{name} contains {forbidden}"


def test_renderer_exact_coordinates() -> None:
    from chart.renderer.plot_renderer import PlotOverlay, _bar_x, _price_y
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay()
    overlay.ingest_plot_events(
        [
            {
                "event_id": "S:12:e:x",
                "source_strategy": "GAMMA",
                "plot_id": "e",
                "plot_type": "MARKER",
                "symbol": "T",
                "timeframe": "15m",
                "bar_index": 12,
                "price": 105.0,
                "marker_type": "DOT",
                "text": None,
                "layer": 60,
                "lifecycle": "ACTIVE",
                "version": 1,
            }
        ]
    )
    viewport = _viewport(30, 0, 30)
    rect = QRectF(viewport.chart_rect)
    expected_x = _bar_x(12, 0, 30, rect)
    expected_y = _price_y(105.0, 90.0, 110.0, rect)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    found = False
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            color = image.pixelColor(int(expected_x) + dx, int(expected_y) + dy)
            if (color.red(), color.green(), color.blue()) != (0x10, 0x14, 0x18):
                found = True
    assert found, "marker must paint at its exact logical coordinate"


def test_hidden_strategy_filters_without_recalc() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay(is_visible=lambda name: name != "OBR")
    overlay.ingest_plot_events(_sample_dicts(3, "OBR") + _sample_dicts(3, "VWAP"))
    assert overlay.total_plot_count() == 6
    viewport = _viewport(30, 0, 30)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    overlay.paint_overlay(painter, viewport)
    painter.end()
    assert overlay.visible_plot_count(0, 30) == 6  # store query is unfiltered
    assert overlay.total_plot_count() == 6  # nothing recalculated or removed


def test_overlay_telemetry_is_measured() -> None:
    from chart.renderer.plot_renderer import PlotOverlay

    overlay = PlotOverlay()
    overlay.ingest_plot_events(_sample_dicts(50))
    stats = overlay.plot_stats()
    assert stats["total"] == 50
    assert stats["strategies"] == 1
    assert stats["ingested"] == 50
    assert stats["dropped"] == 0
    assert stats["last_ingest_ms"] >= 0.0


# ── OBR acceptance (strategy owns meaning; pipeline preserves it) ────


def test_obr_ref_extend_visual_only_and_trading_stable() -> None:
    record_path = __import__("pathlib").Path(r"D:\VAYREN_STRATEGIES\OBR.py")
    if not record_path.exists():
        pytest.skip("canonical OBR record missing")
    import json

    from strategy.language import compile_strategy
    from strategy.models.parameters import StrategyParameters

    code = json.loads(record_path.read_text(encoding="utf-8"))["code"]
    logic5: Any = compile_strategy(code).create_logic(StrategyParameters({}))
    logic9: Any = compile_strategy(code).create_logic(StrategyParameters({}))
    assert logic5.extendBars == 5
    logic9.extendBars = 9
    logic9.engine.cfg.extendBars = 9
    assert logic5.engine.cfg.refIndex == logic9.engine.cfg.refIndex == 3
    # extendBars never touches trading config beyond its own visual field
    assert logic5.engine.cfg.buySlippagePct == logic9.engine.cfg.buySlippagePct


def test_obr_plots_ingest_with_exact_coordinates() -> None:
    from chart.renderer.plot_renderer import PlotOverlay

    logic = _logic("OBR")
    logic._current_bar_index = 3
    logic.plot_level(3, 103.0, end_bar=7, plot_id="ref_high", extend_bars=5)
    logic._current_bar_index = 5
    logic.plot_marker(5, 104.0, MarkerType.UP_ARROW, plot_id="entry_5", text="BUY 104.00")
    overlay = PlotOverlay()
    applied = overlay.ingest_plot_events(logic.get_plot_events())
    assert len(applied) == 2
    visible = overlay._store.query_visible(0, 30)
    by_plot = {record.plot_id: record for record in visible}
    assert (by_plot["ref_high"].start_bar, by_plot["ref_high"].start_price) == (3, 103.0)
    assert by_plot["ref_high"].extend_bars == 5
    assert (by_plot["entry_5"].bar_index, by_plot["entry_5"].price) == (5, 104.0)


# ── performance ──────────────────────────────────────────────────────


def test_performance_100k_scale() -> None:
    from chart.renderer.plot_renderer import PlotStore

    store = PlotStore()
    events = _sample_dicts(100000)
    start = time.perf_counter()
    store.ingest_batch(events)
    ingest_ms = (time.perf_counter() - start) * 1000.0
    start = time.perf_counter()
    visible = store.query_visible(48200, 48850)
    query_ms = (time.perf_counter() - start) * 1000.0
    assert len(visible) == 650
    assert query_ms < 200.0, f"viewport query too slow: {query_ms:.1f}ms"
    assert ingest_ms < 10000.0, f"ingest too slow: {ingest_ms:.1f}ms"
    stats = store.stats()
    assert stats["total"] == 100000


def test_performance_paint_only_visible() -> None:
    from chart.renderer.plot_renderer import PlotOverlay
    from PySide6.QtGui import QColor, QImage, QPainter

    _qapp()
    overlay = PlotOverlay()
    overlay.ingest_plot_events(_sample_dicts(20000))
    viewport = _viewport(20000, 10000, 10650)
    image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101418"))
    painter = QPainter(image)
    start = time.perf_counter()
    overlay.paint_overlay(painter, viewport)
    painter.end()
    paint_ms = (time.perf_counter() - start) * 1000.0
    assert overlay.visible_plot_count(10000, 10650) == 650
    assert paint_ms < 5000.0, f"paint too slow: {paint_ms:.1f}ms"
