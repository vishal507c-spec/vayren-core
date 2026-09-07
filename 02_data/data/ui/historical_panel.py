"""HistoricalDownloadPanel — the historical download console as a side panel.

The panel hosts the configuration console (DownloadPanel), the monitoring
cluster (StatusView) and the collapsible engine log (LogPanel), embedded
inside the main VAYREN window (ChartWindow's side-panel slot) instead of a
separate window. Everything (header, console, status, log) lives in ONE
scroll area — the panel's single main scrollbar — so every section stays
reachable when the viewport is short; the stock checklist keeps its own
internal list scroll. Subscriptions are wired by bootstrap; this class
only handles incoming events and publishes the terminal requests. It never
touches the engine, the worker or the database directly.
"""

from __future__ import annotations

from logging import getLogger
from typing import Any

from core.event_bus.event_bus import EventBus
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from data.events.cancel_download import CancelDownload
from data.events.coverage_request import CoverageRequest
from data.events.download_completed import DownloadCompleted
from data.events.download_coverage import DownloadCoverage
from data.events.download_failed import DownloadFailed
from data.events.download_progress import DownloadProgress
from data.events.download_request import DownloadRequest
from data.events.download_started import DownloadStarted
from data.ui.download_panel import DownloadPanel
from data.ui.log_view import LogPanel, LogView
from data.ui.status_view import StatusView

logger = getLogger(__name__)

_HEADER_STYLE = """
QWidget#panelHeader {
    background: transparent;
    border-bottom: 1px solid palette(midlight);
}
QToolButton#panelClose {
    color: palette(text);
    background: transparent;
    border: none;
    border-radius: 3px;
    font-size: 14px;
    font-weight: 600;
}
QToolButton#panelClose:hover {
    background: palette(midlight);
}
QToolButton#panelClose:pressed {
    background: palette(mid);
}
"""


class _SlimLabel(QLabel):
    """QLabel that never forces the panel wider than its allotted width."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class HistoricalDownloadPanel(QWidget):
    """Hosts the download console, monitoring cluster and engine log.

    Pure event surface, same contract as before: requests in via the
    panel's signals, facts in via the ``on_*`` handlers. The close button
    emits ``close_requested`` so the host window can toggle the panel shut;
    it never opens its own window.
    """

    close_requested = Signal()
    broker_selected = Signal(str)  # forwarded from StatusView (validated upstream)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

        self._panel = DownloadPanel(self)
        self._status = StatusView(self)
        self._log_panel = LogPanel(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())
        layout.addWidget(self._panel, 3)
        layout.addWidget(self._status)
        layout.addWidget(self._log_panel)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll)

        self._panel.download_requested.connect(self._on_download_requested)
        self._panel.coverage_requested.connect(self._on_coverage_requested)
        self._panel.cancel_requested.connect(self._on_cancel_requested)
        self._status.coverage_requested.connect(self._on_coverage_requested)
        self._status.retry_requested.connect(self._on_download_requested)
        self._status.broker_selected.connect(self.broker_selected)

    def _header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("panelHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setStyleSheet(_HEADER_STYLE)
        bar = QHBoxLayout(header)
        bar.setContentsMargins(12, 5, 8, 5)
        bar.setSpacing(4)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = _SlimLabel("Historical Download", header)
        title.setStyleSheet("color: palette(text); font-size: 12px; font-weight: 600;")
        subtitle = _SlimLabel("Market Data Acquisition", header)
        subtitle.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        close = QToolButton(header)
        close.setObjectName("panelClose")
        close.setText("×")
        close.setToolTip("Close panel")
        close.setFixedSize(22, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.close_requested)
        bar.addLayout(titles)
        bar.addStretch(1)
        bar.addWidget(close)
        return header

    @property
    def panel(self) -> DownloadPanel:
        return self._panel

    @property
    def status(self) -> StatusView:
        return self._status

    @property
    def log(self) -> LogView:
        return self._log_panel.log

    @property
    def log_panel(self) -> LogPanel:
        return self._log_panel

    def set_provider(self, ready: bool, reason: str) -> None:
        self._status.set_provider(ready, reason)

    def set_credentials_manager(self, manager: Any) -> None:
        """Inject the provider configuration service (bootstrap wiring)."""
        self._status.set_credentials_manager(manager)

    def set_broker_choices(self, choices: tuple[dict[str, object], ...]) -> None:
        """Registry-backed broker list for the selector (M4)."""
        self._status.set_broker_choices(choices)

    def set_broker_selection(self, selection: Any, capabilities: Any = None) -> None:
        """Show the authoritative selection (M4)."""
        self._status.set_broker_selection(selection, capabilities)

    def show_broker_error(self, message: str) -> None:
        """Surface a rejected selection (M4)."""
        self._status.show_broker_error(message)

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        self._panel.set_symbols(symbols)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._panel.symbols

    # ── request publishing (terminal, like every other window) ──────────────

    def _on_download_requested(
        self, symbol: str, interval: str, from_date: str, to_date: str
    ) -> None:
        logger.info("Download requested: %s %s %s..%s", symbol, interval, from_date, to_date)
        self._log_panel.log.append_line(f"→ download {symbol} {interval} {from_date} … {to_date}")
        self._bus.publish(
            DownloadRequest(symbol=symbol, interval=interval, from_date=from_date, to_date=to_date)
        )

    def _on_coverage_requested(self, symbol: str, interval: str) -> None:
        logger.info("Coverage requested: %s %s", symbol, interval)
        self._log_panel.log.append_line(f"→ coverage {symbol} {interval}")
        self._bus.publish(CoverageRequest(symbol=symbol, interval=interval))

    def _on_cancel_requested(self) -> None:
        logger.info("Cancel requested")
        self._log_panel.log.append_line("→ cancel")
        self._bus.publish(CancelDownload())

    # ── facts (delivered via bootstrap subscription, Qt thread) ──────────────

    def on_symbols_listed(self, event: Any) -> None:
        """Universe fact from market; typed via Any to keep data's deps at core."""
        self.set_symbols(tuple(event.symbols))

    def on_download_started(self, event: DownloadStarted) -> None:
        self._panel.set_busy(True)
        self._status.on_started(event)
        self._log_panel.log.append_line(
            f"• started {event.symbol} {event.interval} {event.from_date} … "
            f"{event.to_date} ({event.total_chunks} chunks)"
        )

    def on_download_progress(self, event: DownloadProgress) -> None:
        self._status.on_progress(event)
        self._log_panel.log.append_line(
            f"• chunk {event.chunk}/{event.total_chunks} {event.chunk_start} … "
            f"{event.chunk_end}: +{event.new_rows} rows (db {event.db_total})"
        )

    def on_download_completed(self, event: DownloadCompleted) -> None:
        self._panel.set_busy(False)
        self._status.on_completed(event)
        self._log_panel.log.append_line(
            f"✔ finished {event.symbol} {event.interval}: +{event.new_rows} new, "
            f"{event.db_total} total, {event.trading_days} trading days"
        )

    def on_download_failed(self, event: DownloadFailed) -> None:
        self._panel.set_busy(False)
        self._status.on_failed(event)
        self._log_panel.log.append_line(f"✖ failed {event.symbol} {event.interval}: {event.reason}")

    def on_download_coverage(self, event: DownloadCoverage) -> None:
        self._status.on_coverage(event)
        self._log_panel.log.append_line(
            f"• coverage {event.symbol} {event.interval}: {event.coverage_pct:.1f}% "
            f"({event.row_count} rows, {event.trading_days} trading days, {event.state})"
        )

    def on_log_line(self, message: str) -> None:
        self._log_panel.log.append_line(message)
