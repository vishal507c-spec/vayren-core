"""ChartWindow — hosts the candle chart widget and reports rendering."""

from logging import getLogger

from core.event_bus.event_bus import EventBus
from PySide6.QtWidgets import QMainWindow

from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.widgets.candle_chart_widget import CandleChartWidget

logger = getLogger(__name__)


class ChartWindow(QMainWindow):
    """Displays a ChartModel via CandleChartWidget and publishes WindowRendered.

    Subscriptions are wired by bootstrap; this class only handles the
    incoming event and publishes the terminal result.
    """

    def __init__(self, widget: CandleChartWidget, bus: EventBus) -> None:
        super().__init__()
        self._widget = widget
        self._bus = bus
        self.setCentralWidget(widget)
        self.resize(1280, 760)

    def on_chart_ready(self, event: ChartReady) -> None:
        """Display the prepared chart model."""
        model = event.model
        self._widget.set_model(model)
        self.setWindowTitle(f"Vayren — {model.symbol} ({len(model.bars)} bars)")
        self.show()
        logger.info("Chart window shown for %s", model.symbol)
        self._bus.publish(WindowRendered())
