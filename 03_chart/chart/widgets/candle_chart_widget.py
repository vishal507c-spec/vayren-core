"""CandleChartWidget — candlestick viewport: zoom, pan, resize."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QWidget

from chart.models.chart_model import ChartModel
from chart.renderer.candle_renderer import CandleRenderer


class CandleChartWidget(QWidget):
    """Renders a ChartModel and manages the viewport.

    Interactions: wheel = zoom (anchored at the cursor), left-drag = pan,
    resize = re-render. Holds no events, no SQL, no data loading.
    """

    MIN_VISIBLE_BARS = 10
    ZOOM_STEP = 1.25
    VOLUME_RATIO = 0.15
    INITIAL_BARS = 150

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: ChartModel | None = None
        self._first = 0
        self._last = 0
        self._drag_origin_x: float | None = None
        self._drag_first = 0
        self.setMinimumSize(480, 300)

    def set_model(self, model: ChartModel) -> None:
        """Replace the chart data and reset the viewport to the most recent bars."""
        self._model = model
        total = len(model.bars)
        self._last = total
        self._first = max(0, total - self.INITIAL_BARS)
        self.update()

    def _visible_range(self) -> tuple[int, int]:
        if self._model is None:
            return (0, 0)
        total = len(self._model.bars)
        first = min(max(self._first, 0), total)
        last = min(max(self._last, first + self.MIN_VISIBLE_BARS), total)
        return (first, last)

    def _chart_rects(self) -> tuple[QRect, QRect]:
        width = self.width()
        height = self.height()
        volume_height = int(height * self.VOLUME_RATIO)
        chart = QRect(0, 0, width, height - volume_height)
        volume = QRect(0, height - volume_height, width, volume_height)
        return chart, volume

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._model is None or not self._model.bars:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), CandleRenderer.BACKGROUND)
        first, last = self._visible_range()
        if last <= first:
            return
        chart_rect, volume_rect = self._chart_rects()
        visible = self._model.bars[first:last]
        price_low = min(bar.low for bar in visible)
        price_high = max(bar.high for bar in visible)
        volume_max = max(bar.volume for bar in visible)
        CandleRenderer.paint(
            painter,
            self._model.bars,
            first,
            last,
            price_low,
            price_high,
            volume_max,
            chart_rect,
            volume_rect,
        )

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._model is None or not self._model.bars:
            return
        steps = event.angleDelta().y() / 120.0
        if steps == 0.0:
            return
        chart_rect, _ = self._chart_rects()
        if chart_rect.width() <= 0:
            return
        first, last = self._visible_range()
        total = len(self._model.bars)
        count = last - first
        anchor_fraction = (event.position().x() - chart_rect.left()) / chart_rect.width()
        anchor = first + int(anchor_fraction * count)
        anchor = max(first, min(last - 1, anchor))

        new_count = count / (self.ZOOM_STEP**steps)
        new_count = max(self.MIN_VISIBLE_BARS, min(total, int(round(new_count))))
        new_first = anchor - int((anchor - first) / count * new_count)
        new_first = max(0, min(total - new_count, new_first))
        self._first = new_first
        self._last = new_first + new_count
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._model is not None:
            self._drag_origin_x = event.position().x()
            self._drag_first = self._first
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin_x is None or self._model is None:
            return
        chart_rect, _ = self._chart_rects()
        if chart_rect.width() <= 0:
            return
        first, last = self._visible_range()
        count = last - first
        delta_x = event.position().x() - self._drag_origin_x
        delta_bars = -int(round(delta_x / chart_rect.width() * count))
        new_first = min(max(self._drag_first + delta_bars, 0), len(self._model.bars) - count)
        self._first = new_first
        self._last = new_first + count
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin_x = None
            self.unsetCursor()

    def resizeEvent(self, _event: QResizeEvent) -> None:
        self.update()
