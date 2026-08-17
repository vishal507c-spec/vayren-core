"""CrosshairValue — immutable crosshair state derived from a mouse position.

Holds the snapped-to-nearest-candle bar index, the bar's price range and
timestamp. The widget computes this; renderers only consume the data.
No Qt, no SQL, no events.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CrosshairValue:
    """The value under the crosshair after snapping to the nearest candle.

    Attributes:
        bar_index: Index of the snapped bar in the chart's bar tuple.
        price: The price level under the horizontal crosshair line. This is
            the bar's close when the cursor is near the candle, otherwise the
            interpolated price along the candle's high-low range.
        timestamp: The raw ISO timestamp string of the snapped bar.
        open: Open price of the snapped bar.
        high: High price of the snapped bar.
        low: Low price of the snapped bar.
        close: Close price of the snapped bar.
    """

    bar_index: int
    price: float
    timestamp: str
    open: float
    high: float
    low: float
    close: float
