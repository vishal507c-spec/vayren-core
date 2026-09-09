"""Universal strategy-owned plot contract — the ONLY plot data contract.

Strategy owns WHAT/WHEN/WHERE/WHICH plot to emit (OBR refIndex, BUY/SELL,
NO-TRADE meaning all live in strategy logic). This module owns neither
trading decisions nor rendering: it is a generic, strategy-agnostic data
contract transported by the plot pipeline (store -> viewport -> renderer).

The renderer MUST NOT branch on strategy names or signal meanings; it only
reads ``plot_type`` / ``marker_type`` / coordinates. No OBR/ORB/VWAP/BUY/
SELL/NO-TRADE strings exist in this file by design.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum


class PlotType(Enum):
    """Generic visual primitives — any strategy may use any of these."""

    LINE = "LINE"
    RAY = "RAY"
    SEGMENT = "SEGMENT"
    MARKER = "MARKER"
    SHAPE = "SHAPE"
    LABEL = "LABEL"
    ZONE = "ZONE"
    HORIZONTAL_LEVEL = "HORIZONTAL_LEVEL"
    VERTICAL_MARK = "VERTICAL_MARK"
    AREA = "AREA"


class MarkerType(Enum):
    """Generic marker glyphs — the strategy selects, the renderer draws."""

    UP_ARROW = "UP_ARROW"
    DOWN_ARROW = "DOWN_ARROW"
    CIRCLE = "CIRCLE"
    CIRCLE_X = "CIRCLE_X"
    SQUARE = "SQUARE"
    DIAMOND = "DIAMOND"
    TRIANGLE_UP = "TRIANGLE_UP"
    TRIANGLE_DOWN = "TRIANGLE_DOWN"
    TRIANGLE_BLUE = "TRIANGLE_BLUE"
    DOT = "DOT"


class PlotLifecycle(Enum):
    """Mutable live-plot states — the renderer consumes the latest valid."""

    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    HIDDEN = "HIDDEN"
    REMOVED = "REMOVED"


class RenderLayer(Enum):
    """Generic z-order layers — no strategy semantics, lowest paints first."""

    BACKGROUND = 0
    ZONE = 10
    LEVEL = 20
    REF_LINE = 30
    SL_LINE = 40
    MARKER = 60
    LABEL = 70
    OVERLAY = 80


_DEFAULT_LAYER: dict[PlotType, RenderLayer] = {
    PlotType.ZONE: RenderLayer.ZONE,
    PlotType.AREA: RenderLayer.ZONE,
    PlotType.HORIZONTAL_LEVEL: RenderLayer.LEVEL,
    PlotType.LINE: RenderLayer.REF_LINE,
    PlotType.RAY: RenderLayer.REF_LINE,
    PlotType.SEGMENT: RenderLayer.SL_LINE,
    PlotType.MARKER: RenderLayer.MARKER,
    PlotType.SHAPE: RenderLayer.MARKER,
    PlotType.LABEL: RenderLayer.LABEL,
    PlotType.VERTICAL_MARK: RenderLayer.OVERLAY,
}

_POINT_TYPES = frozenset({PlotType.MARKER, PlotType.SHAPE, PlotType.LABEL})
_SPAN_TYPES = frozenset(
    {
        PlotType.LINE,
        PlotType.RAY,
        PlotType.SEGMENT,
        PlotType.ZONE,
        PlotType.HORIZONTAL_LEVEL,
        PlotType.VERTICAL_MARK,
        PlotType.AREA,
    }
)


class PlotValidationError(ValueError):
    """A plot instruction violates the universal contract."""


def make_event_id(
    source_strategy: str,
    symbol: str,
    timeframe: str,
    bar: int,
    plot_id: str,
) -> str:
    """Deterministic identity: strategy+symbol+timeframe+bar+plot_id.

    Stable across live updates, reconnects, replay and backtest redraws so
    re-ingestion never duplicates a visual.
    """
    raw = f"{source_strategy}|{symbol}|{timeframe}|{bar}|{plot_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source_strategy}:{bar}:{plot_id}:{digest}"


def _require_finite(value: float | None, name: str) -> None:
    if value is None:
        raise PlotValidationError(f"{name} is required for this plot type")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PlotValidationError(f"{name} must be a finite number, got {value!r}")


def _require_bar(value: int | None, name: str) -> None:
    if value is None:
        raise PlotValidationError(f"{name} is required for this plot type")
    if not isinstance(value, int) or value < 0:
        raise PlotValidationError(f"{name} must be a non-negative bar index, got {value!r}")


@dataclass(frozen=True)
class PlotEvent:
    """One generic plot instruction emitted by strategy logic.

    Coordinates are LOGICAL chart coordinates (bar index + price) preserved
    exactly end-to-end — the pipeline never shifts, replaces or infers them.
    Pixel conversion happens only in the renderer. ``text`` defaults to None:
    no label is rendered unless the strategy explicitly requests one.
    """

    event_id: str
    source_strategy: str
    plot_id: str
    plot_type: PlotType
    symbol: str = ""
    timeframe: str = ""
    bar_index: int | None = None
    start_bar: int | None = None
    end_bar: int | None = None
    price: float | None = None
    start_price: float | None = None
    end_price: float | None = None
    timestamp: str | None = None
    marker_type: MarkerType | None = None
    text: str | None = None
    layer: RenderLayer | None = None
    lifecycle: PlotLifecycle = PlotLifecycle.ACTIVE
    version: int = 1
    extend_bars: int | None = None
    dependencies: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.event_id or not isinstance(self.event_id, str):
            raise PlotValidationError("event_id must be a non-empty string")
        if not self.source_strategy or not isinstance(self.source_strategy, str):
            raise PlotValidationError("source_strategy must be a non-empty string")
        if not self.plot_id or not isinstance(self.plot_id, str):
            raise PlotValidationError("plot_id must be a non-empty string")
        if not isinstance(self.plot_type, PlotType):
            raise PlotValidationError(f"plot_type must be a PlotType, got {self.plot_type!r}")
        if self.layer is None:
            object.__setattr__(self, "layer", default_layer(self.plot_type))
        if not isinstance(self.layer, RenderLayer):
            raise PlotValidationError(f"layer must be a RenderLayer, got {self.layer!r}")
        if not isinstance(self.lifecycle, PlotLifecycle):
            raise PlotValidationError(f"lifecycle must be a PlotLifecycle, got {self.lifecycle!r}")
        if not isinstance(self.version, int) or self.version < 1:
            raise PlotValidationError(f"version must be a positive int, got {self.version!r}")
        if self.extend_bars is not None and (
            not isinstance(self.extend_bars, int) or self.extend_bars < 1
        ):
            raise PlotValidationError(
                f"extend_bars must be a positive int or None, got {self.extend_bars!r}"
            )
        if self.text is not None and not isinstance(self.text, str):
            raise PlotValidationError(f"text must be str or None, got {self.text!r}")
        if self.plot_type == PlotType.MARKER:
            if not isinstance(self.marker_type, MarkerType):
                raise PlotValidationError("MARKER plots require a marker_type")
            _require_bar(self.bar_index, "bar_index")
            _require_finite(self.price, "price")
        elif self.plot_type in (PlotType.SHAPE, PlotType.LABEL):
            _require_bar(self.bar_index, "bar_index")
            _require_finite(self.price, "price")
        elif self.plot_type in (PlotType.LINE, PlotType.SEGMENT, PlotType.RAY):
            _require_bar(self.start_bar, "start_bar")
            _require_finite(self.start_price, "start_price")
            if self.plot_type in (PlotType.LINE, PlotType.SEGMENT):
                _require_bar(self.end_bar, "end_bar")
                _require_finite(self.end_price, "end_price")
            if (
                self.start_bar is not None
                and self.end_bar is not None
                and self.end_bar < self.start_bar
            ):
                raise PlotValidationError("end_bar must be >= start_bar")
            if self.end_price is not None and not math.isfinite(float(self.end_price)):
                raise PlotValidationError(f"end_price must be finite, got {self.end_price!r}")
        elif self.plot_type == PlotType.HORIZONTAL_LEVEL:
            _require_bar(self.start_bar, "start_bar")
            _require_bar(self.end_bar, "end_bar")
            _require_finite(self.start_price, "start_price")
        elif self.plot_type == PlotType.VERTICAL_MARK:
            _require_bar(self.bar_index, "bar_index")
        elif self.plot_type in (PlotType.ZONE, PlotType.AREA):
            _require_bar(self.start_bar, "start_bar")
            _require_bar(self.end_bar, "end_bar")
            _require_finite(self.start_price, "start_price")
            _require_finite(self.end_price, "end_price")

    @property
    def anchor_bar(self) -> int:
        """Primary bar for indexing: point bar, else span start."""
        if self.bar_index is not None:
            return self.bar_index
        if self.start_bar is not None:
            return self.start_bar
        return 0

    @property
    def covered_bars(self) -> tuple[int, int]:
        """Inclusive (first, last) bar span this plot touches."""
        if self.bar_index is not None and self.start_bar is None and self.end_bar is None:
            return (self.bar_index, self.bar_index)
        first = self.start_bar if self.start_bar is not None else self.bar_index or 0
        last = self.end_bar if self.end_bar is not None else first
        if self.plot_type == PlotType.RAY and self.extend_bars is not None:
            last = max(last, first + self.extend_bars - 1)
        return (first, last)

    def with_update(self, **changes: object) -> PlotEvent:
        """Return the next version of this plot (mutable live plots)."""
        from dataclasses import replace

        next_version = self.version + 1
        merged: dict[str, object] = {"lifecycle": PlotLifecycle.ACTIVE}
        merged.update(changes)
        return replace(self, version=next_version, **merged)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        """Lightweight serializable form (save backtest -> reload -> render)."""
        return {
            "event_id": self.event_id,
            "source_strategy": self.source_strategy,
            "plot_id": self.plot_id,
            "plot_type": self.plot_type.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bar_index": self.bar_index,
            "start_bar": self.start_bar,
            "end_bar": self.end_bar,
            "price": self.price,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "timestamp": self.timestamp,
            "marker_type": self.marker_type.value if self.marker_type else None,
            "text": self.text,
            "layer": self.layer.value if self.layer is not None else RenderLayer.MARKER.value,
            "lifecycle": self.lifecycle.value,
            "version": self.version,
            "extend_bars": self.extend_bars,
            "dependencies": list(self.dependencies),
            "metadata": [list(pair) for pair in self.metadata],
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> PlotEvent:
        """Rebuild a plot event from :meth:`to_dict` output."""
        try:
            raw_meta = data.get("metadata") or []
            meta = tuple(
                (str(pair[0]), str(pair[1]))
                for pair in raw_meta  # type: ignore[union-attr]
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
            marker = data.get("marker_type")
            return PlotEvent(
                event_id=str(data["event_id"]),
                source_strategy=str(data["source_strategy"]),
                plot_id=str(data["plot_id"]),
                plot_type=PlotType(str(data["plot_type"])),
                symbol=str(data.get("symbol") or ""),
                timeframe=str(data.get("timeframe") or ""),
                bar_index=data.get("bar_index"),  # type: ignore[arg-type]
                start_bar=data.get("start_bar"),  # type: ignore[arg-type]
                end_bar=data.get("end_bar"),  # type: ignore[arg-type]
                price=data.get("price"),  # type: ignore[arg-type]
                start_price=data.get("start_price"),  # type: ignore[arg-type]
                end_price=data.get("end_price"),  # type: ignore[arg-type]
                timestamp=data.get("timestamp"),  # type: ignore[arg-type]
                marker_type=MarkerType(str(marker)) if marker else None,
                text=data.get("text"),  # type: ignore[arg-type]
                layer=RenderLayer(int(data.get("layer", RenderLayer.MARKER.value))),  # type: ignore[arg-type]
                lifecycle=PlotLifecycle(str(data.get("lifecycle", "ACTIVE"))),
                version=int(data.get("version", 1)),  # type: ignore[arg-type]
                extend_bars=data.get("extend_bars"),  # type: ignore[arg-type]
                dependencies=tuple(str(dep) for dep in (data.get("dependencies") or ())),  # type: ignore[union-attr]
                metadata=meta,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise PlotValidationError(f"invalid plot dict: {exc}") from exc


def default_layer(plot_type: PlotType) -> RenderLayer:
    """Generic z-order for a plot type (no strategy knowledge)."""
    return _DEFAULT_LAYER.get(plot_type, RenderLayer.MARKER)
