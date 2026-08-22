"""ChartViewport + ChartOverlay protocol — the chart's overlay extension point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from market.models.bar import Bar
from PySide6.QtCore import QRect


@dataclass(frozen=True)
class ChartViewport:
    """Immutable snapshot of the chart's current viewport.

    The overlay renderer reads this to map bar indices and prices → pixels
    using exactly the chart's math. Reference to the full bar tuple avoids
    extra repository queries inside the paint path.

    Attributes:
        bars: Full ascending bar window bound to the chart model.
        first: Visible window first index (inclusive).
        last: Visible window last index (exclusive, may exceed len(bars)).
        price_low: Visible price range low.
        price_high: Visible price range high.
        volume_max: Visible volume scale max.
        chart_rect: Paint area for the price panel.
        volume_rect: Paint area for the volume strip.
        axis_rect: Paint area for the time axis.
    """

    bars: tuple[Bar, ...]
    first: int
    last: int
    price_low: float
    price_high: float
    volume_max: int
    chart_rect: QRect
    volume_rect: QRect
    axis_rect: QRect


class ChartOverlay(Protocol):
    """A live overlay painter installed on :class:`CandleChartWidget`.

    Implementations must keep :meth:`paint_overlay` cheap — it is called on
    every paint (including crosshair moves). The snapshot is already
    computed by the widget; the overlay only reads it and paints.
    """

    def paint_overlay(self, painter, viewport: ChartViewport) -> None:  # type: ignore[no-untyped-def]
        """Paint on top of the chart for the current viewport."""
        ...
