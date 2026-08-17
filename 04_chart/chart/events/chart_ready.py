"""ChartReady — the chart model has been prepared for rendering."""

from dataclasses import dataclass

from core.events.event import Event

from chart.models.chart_model import ChartModel


@dataclass(frozen=True)
class ChartReady(Event):
    """The chart model is ready to be displayed."""

    model: ChartModel
