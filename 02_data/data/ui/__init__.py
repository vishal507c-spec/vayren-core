"""data.ui — pure presentation widgets for the historical downloader.

Rule: no bus, no SQL, no engine access. Widgets emit Qt signals with plain
values; the window publishes requests to the bus; facts arrive back as bus
events and are rendered here.
"""

from data.ui.historical_panel import HistoricalDownloadPanel

__all__ = ["HistoricalDownloadPanel"]
