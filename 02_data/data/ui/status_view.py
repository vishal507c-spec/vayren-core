"""StatusView — the download console's monitoring cluster.

State-driven cards below the configuration area: Download Status (live
progress + performance), Download Complete, Download Failed, Coverage,
and a compact Provider Status card. Only values carried by the bus events
are shown; performance figures are computed locally (rows/sec, elapsed,
ETA, estimated size) and the Download Plan estimates live in the panel.
No bus, no events, no engine.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from data.events.download_completed import DownloadCompleted
from data.events.download_coverage import DownloadCoverage
from data.events.download_failed import DownloadFailed
from data.events.download_progress import DownloadProgress
from data.events.download_started import DownloadStarted
from data.provider.manager import ProviderCredentialsManager
from data.ui.credentials_dialog import ProviderCredentialsDialog
from data.ui.section_label import SectionLabel

_ROW_LABEL_STYLE = "color: palette(placeholder-text); font-size: 10px;"
_VALUE_STYLE = "font-size: 11px; font-weight: 600;"
_NOTE_STYLE = "color: palette(placeholder-text); font-size: 10px;"
_EST_BYTES_PER_ROW = 64  # performance "Downloaded" estimate

_PROGRESS_STYLE = """
StatusView QProgressBar {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 2px;
}
StatusView QProgressBar::chunk {
    background: palette(highlight);
    border-radius: 2px;
}
"""

_BUTTON_STYLE = """
QPushButton {
    color: palette(text);
    background: transparent;
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 2px 5px;
    font-size: 11px;
}
QPushButton:hover {
    border-color: palette(highlight);
    color: palette(highlight);
}
"""

_COMBO_STYLE = """
QComboBox {
    color: palette(text);
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 11px;
}
QComboBox:hover {
    border-color: palette(highlight);
}
QComboBox QAbstractItemView {
    background: palette(base);
    border: 1px solid palette(midlight);
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
"""

_CREDENTIALS_TEXT = (
    "VAYREN reads Zerodha credentials from the secure credential store "
    "(configure them via the Configure Credentials dialog).\n\n"
    "Fallback — environment variables (only when in-app credentials are "
    "not stored):\n"
    "  VAYREN_ZERODHA_API_KEY\n"
    "  VAYREN_ZERODHA_API_SECRET\n\n"
    "Auto-login (optional):\n"
    "  VAYREN_ZERODHA_USER_ID\n"
    "  VAYREN_ZERODHA_PASSWORD\n"
    "  VAYREN_ZERODHA_TOTP_SECRET\n\n"
    "Set these variables, then restart VAYREN."
)


class _WrapLabel(QLabel):
    """Wrapping value label that never forces the side panel wider."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _SlimButton(QPushButton):
    """QPushButton with the same shrink-tolerant minimum size hint."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


def _format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_bytes(size: int) -> str:
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f} KB"
    return f"{size} B"


class StatusView(QWidget):
    """Read-only monitoring cluster; requests for re-checks go via signals.

    Broker selection (M4): the selector is a pure view over injected
    registry facts (``set_broker_choices``) and the authoritative
    selection (``set_broker_selection``). A user choice is emitted as
    ``broker_selected`` — validation and persistence happen in the
    bootstrap composition root, never inside this widget.
    """

    coverage_requested = Signal(str, str)  # symbol, interval
    retry_requested = Signal(str, str, str, str)  # symbol, interval, from, to
    broker_selected = Signal(str)  # requested broker name (validated upstream)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._started_at: datetime | None = None
        self._last_start: DownloadStarted | None = None
        self._last_completed: DownloadCompleted | None = None
        self._last_coverage: DownloadCoverage | None = None
        self._total_rows = 0
        self._mode = "idle"
        self._credentials_manager: ProviderCredentialsManager | None = None
        self._broker_choices: dict[str, dict[str, object]] = {}

        self.setStyleSheet(_PROGRESS_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(6)

        self._run_card = self._build_run_card()
        self._complete_card = self._build_complete_card()
        self._error_card = self._build_error_card()
        self._coverage_card = self._build_coverage_card()
        self._provider_card = self._build_provider_card()

        for card in (
            self._run_card,
            self._complete_card,
            self._error_card,
            self._coverage_card,
            self._provider_card,
        ):
            layout.addWidget(card)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_performance)

        self._set_mode("idle")

    # ── construction ─────────────────────────────────────────────────────────

    def _grid(self) -> QFormLayout:
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        grid.setHorizontalSpacing(10)
        return grid

    def _grid_row(self, grid: QFormLayout, caption: str, parent: QWidget) -> QLabel:
        name = QLabel(caption, parent)
        name.setStyleSheet(_ROW_LABEL_STYLE)
        value = _WrapLabel("—", parent)
        value.setStyleSheet(_VALUE_STYLE)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addRow(name, value)
        return value

    def _card(self, parent: QWidget) -> QWidget:
        card = QWidget(parent)
        return card

    def _build_run_card(self) -> QWidget:
        card = self._card(self)
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(SectionLabel("Download Status", card))
        grid = self._grid()
        self._run_status = self._grid_row(grid, "Status", card)
        self._run_symbol = self._grid_row(grid, "Current Symbol", card)
        self._run_interval = self._grid_row(grid, "Interval", card)
        self._run_range = self._grid_row(grid, "Current Range", card)
        self._run_chunk = self._grid_row(grid, "Current Chunk", card)
        self._run_rows = self._grid_row(grid, "Rows Downloaded", card)
        self._run_coverage = self._grid_row(grid, "Coverage", card)
        column.addLayout(grid)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(6)
        self._progress = QProgressBar(card)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress_pct = _WrapLabel("0%", card)
        self._progress_pct.setStyleSheet(_VALUE_STYLE)
        self._progress_pct.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bar_row.addWidget(self._progress, 1)
        bar_row.addWidget(self._progress_pct)
        column.addLayout(bar_row)
        self._progress_note = _WrapLabel("", card)
        self._progress_note.setStyleSheet(_NOTE_STYLE)
        column.addWidget(self._progress_note)

        column.addWidget(SectionLabel("Performance", card))
        perf = self._grid()
        self._perf_rows = self._grid_row(perf, "Rows/sec", card)
        self._perf_elapsed = self._grid_row(perf, "Elapsed", card)
        self._perf_eta = self._grid_row(perf, "ETA", card)
        self._perf_size = self._grid_row(perf, "Downloaded (est.)", card)
        column.addLayout(perf)
        return card

    def _build_complete_card(self) -> QWidget:
        card = self._card(self)
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(SectionLabel("Download Complete", card))
        self._complete_status = _WrapLabel("", card)
        self._complete_status.setStyleSheet("font-size: 11px; font-weight: 700;")
        column.addWidget(self._complete_status)
        grid = self._grid()
        self._complete_rows = self._grid_row(grid, "Rows", card)
        self._complete_coverage = self._grid_row(grid, "Coverage", card)
        self._complete_duration = self._grid_row(grid, "Duration", card)
        self._complete_size = self._grid_row(grid, "Data Size (est.)", card)
        column.addLayout(grid)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self._complete_coverage_button = _SlimButton("View Coverage", card)
        self._complete_coverage_button.setStyleSheet(_BUTTON_STYLE)
        self._complete_coverage_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._complete_coverage_button.clicked.connect(self._on_view_coverage)
        actions.addWidget(self._complete_coverage_button)
        actions.addStretch(1)
        column.addLayout(actions)
        return card

    def _build_error_card(self) -> QWidget:
        card = self._card(self)
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(SectionLabel("Download Failed", card))
        self._error_line = _WrapLabel("", card)
        self._error_line.setStyleSheet("font-size: 11px; font-weight: 600;")
        self._error_line.setWordWrap(True)
        column.addWidget(self._error_line)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self._retry_button = _SlimButton("Retry", card)
        self._retry_button.setStyleSheet(_BUTTON_STYLE)
        self._retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_button.clicked.connect(self._on_retry)
        self._error_details_button = _SlimButton("View Details", card)
        self._error_details_button.setStyleSheet(_BUTTON_STYLE)
        self._error_details_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._error_details_button.clicked.connect(self._toggle_error_details)
        actions.addWidget(self._retry_button)
        actions.addWidget(self._error_details_button)
        actions.addStretch(1)
        column.addLayout(actions)
        self._error_detail = QPlainTextEdit(card)
        self._error_detail.setReadOnly(True)
        self._error_detail.setMaximumHeight(90)
        self._error_detail.hide()
        column.addWidget(self._error_detail)
        return card

    def _build_coverage_card(self) -> QWidget:
        card = self._card(self)
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(SectionLabel("Coverage", card))
        grid = self._grid()
        self._cov_symbol = self._grid_row(grid, "Symbol", card)
        self._cov_interval = self._grid_row(grid, "Interval", card)
        self._cov_range = self._grid_row(grid, "Range", card)
        self._coverage_label = self._grid_row(grid, "Coverage", card)
        self._cov_rows = self._grid_row(grid, "Rows", card)
        column.addLayout(grid)
        return card

    def _build_provider_card(self) -> QWidget:
        card = self._card(self)
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(SectionLabel("Provider Status", card))
        # ── Broker selection (M4): registry-backed choices, validated upstream ──
        selector_row = QHBoxLayout()
        selector_row.setSpacing(6)
        selector_caption = _WrapLabel("Broker", card)
        selector_caption.setStyleSheet(_ROW_LABEL_STYLE)
        self._broker_combo = QComboBox(card)
        self._broker_combo.setStyleSheet(_COMBO_STYLE)
        self._broker_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._broker_combo.activated.connect(self._on_broker_activated)
        selector_row.addWidget(selector_caption)
        selector_row.addWidget(self._broker_combo, 1)
        column.addLayout(selector_row)
        self._broker_caps = _WrapLabel("", card)
        self._broker_caps.setStyleSheet(_NOTE_STYLE)
        self._broker_caps.setWordWrap(True)
        column.addWidget(self._broker_caps)
        self._broker_error = _WrapLabel("", card)
        self._broker_error.setStyleSheet("color: palette(bright-text); font-size: 10px;")
        self._broker_error.setWordWrap(True)
        self._broker_error.hide()
        column.addWidget(self._broker_error)
        row = QHBoxLayout()
        row.setSpacing(8)
        provider_name = _WrapLabel("Zerodha", card)
        provider_name.setStyleSheet("font-size: 11px; font-weight: 600;")
        self._provider_status = _WrapLabel("● Not Configured", card)
        self._provider_status.setStyleSheet("font-size: 11px; font-weight: 600;")
        row.addWidget(provider_name, 1)
        row.addWidget(self._provider_status)
        column.addLayout(row)
        self._provider_label = _WrapLabel("", card)
        self._provider_label.setStyleSheet(_NOTE_STYLE)
        self._provider_label.setWordWrap(True)
        column.addWidget(self._provider_label)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self._configure_button = _SlimButton("Configure Credentials", card)
        self._configure_button.setStyleSheet(_BUTTON_STYLE)
        self._configure_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._configure_button.clicked.connect(self._show_credentials_dialog)
        self._advanced_button = _SlimButton("Advanced ▾", card)
        self._advanced_button.setStyleSheet(_BUTTON_STYLE)
        self._advanced_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_button.clicked.connect(self._toggle_advanced)
        actions.addWidget(self._configure_button, 1)
        actions.addWidget(self._advanced_button)
        column.addLayout(actions)
        self._provider_detail = QPlainTextEdit(card)
        self._provider_detail.setReadOnly(True)
        self._provider_detail.setMaximumHeight(70)
        self._provider_detail.hide()
        column.addWidget(self._provider_detail)
        self._env_fallback_header = QLabel("Environment Variable Fallback", card)
        self._env_fallback_header.setStyleSheet(
            "color: palette(placeholder-text); font-size: 10px; font-weight: 600;"
        )
        self._env_fallback_header.hide()
        column.addWidget(self._env_fallback_header)
        self._env_fallback = QLabel(_CREDENTIALS_TEXT, card)
        self._env_fallback.setStyleSheet(_NOTE_STYLE)
        self._env_fallback.setWordWrap(True)
        self._env_fallback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._env_fallback.hide()
        column.addWidget(self._env_fallback)
        return card

    # ── public surface ───────────────────────────────────────────────────────

    def set_credentials_manager(self, manager: ProviderCredentialsManager) -> None:
        """Inject the provider configuration service used by the dialog."""
        self._credentials_manager = manager

    def set_provider(self, ready: bool, reason: str) -> None:
        """Render the provider readiness state (raw reason stays in Advanced)."""
        if ready:
            self._provider_status.setText("● Connected")
            self._provider_label.setText("ready — provider connected")
        else:
            self._provider_status.setText("● Not Configured")
            self._provider_label.setText("not ready — API credentials required to start download.")
        self._provider_detail.setPlainText(reason)
        self._configure_button.setVisible(not ready)
        self._advanced_button.setVisible(bool(reason))

    # ── broker selection (M4) ────────────────────────────────────────────────

    def set_broker_choices(self, choices: tuple[dict[str, object], ...]) -> None:
        """Populate the selector from registry facts (name-sorted upstream).

        Each choice carries ``name``/``display_name``/``domains``; the
        combo shows the display name and stores the registry name.
        """
        self._broker_choices = {str(c.get("name", "")): c for c in choices}
        self._broker_combo.blockSignals(True)
        self._broker_combo.clear()
        for choice in choices:
            self._broker_combo.addItem(
                str(choice.get("display_name", choice.get("name"))),
                str(choice.get("name", "")),
            )
        self._broker_combo.blockSignals(False)

    def set_broker_selection(
        self, selection: object, capabilities: tuple[str, ...] | None = None
    ) -> None:
        """Show the authoritative selection (never a locally-held state)."""
        name = str(getattr(selection, "name", ""))
        reason = str(getattr(selection, "reason", ""))
        index = self._broker_combo.findData(name)
        if index < 0:
            for row, key in enumerate(self._broker_choices):
                if key == name:
                    index = row
                    break
        if index >= 0:
            self._broker_combo.blockSignals(True)
            self._broker_combo.setCurrentIndex(index)
            self._broker_combo.blockSignals(False)
        choice = self._broker_choices.get(name, {})
        domains = choice.get("domains") if isinstance(choice, dict) else None
        parts: list[str] = []
        if isinstance(domains, dict):
            for label, key in (
                ("Historical", "historical_data"),
                ("Market", "market_data"),
                ("Trading", "trading"),
            ):
                parts.append(f"{label} {'✓' if domains.get(key) else '✗'}")
        if capabilities:
            parts.append(f"{len(capabilities)} capabilities")
        if reason:
            parts.append(reason)
        self._broker_caps.setText("  ·  ".join(parts))
        self.show_broker_error("")

    def show_broker_error(self, message: str) -> None:
        """Surface a rejected selection (previous valid choice stays shown)."""
        self._broker_error.setText(message)
        self._broker_error.setVisible(bool(message))

    def _on_broker_activated(self, index: int) -> None:
        """Emit the requested broker name; validation happens in bootstrap."""
        names = list(self._broker_choices)
        if 0 <= index < len(names):
            self.broker_selected.emit(names[index])

    def on_started(self, event: DownloadStarted) -> None:
        self._last_start = event
        self._last_completed = None
        self._total_rows = 0
        self._started_at = datetime.now()
        self._set_mode("running")
        self._run_status.setText("● Downloading")
        self._run_symbol.setText(event.symbol)
        self._run_interval.setText(event.interval)
        self._run_range.setText(f"{event.from_date} → {event.to_date}")
        self._run_chunk.setText(f"0 / {event.total_chunks}")
        self._run_rows.setText("0")
        self._run_coverage.setText("—")
        self._progress.setValue(0)
        self._progress_pct.setText("0%")
        self._progress_note.setText(f"0 of {event.total_chunks} chunks completed")
        self._timer.start()
        self._tick_performance()

    def on_progress(self, event: DownloadProgress) -> None:
        self._run_status.setText("● Downloading")
        self._run_symbol.setText(event.symbol)
        self._run_interval.setText(event.interval)
        self._run_chunk.setText(f"{event.chunk} / {event.total_chunks}")
        self._total_rows += event.new_rows
        self._run_rows.setText(f"{self._total_rows:,}")
        self._progress.setValue(
            round(event.chunk / event.total_chunks * 100) if event.total_chunks else 0
        )
        self._progress_pct.setText(f"{self._progress.value()}%")
        self._progress_note.setText(f"{event.chunk} of {event.total_chunks} chunks completed")

    def on_completed(self, event: DownloadCompleted) -> None:
        self._last_completed = event
        self._timer.stop()
        self._set_mode("complete")
        elapsed = 0.0
        if self._started_at is not None:
            elapsed = (datetime.now() - self._started_at).total_seconds()
        self._complete_status.setText(f"✓ {event.symbol} processed")
        self._complete_rows.setText(f"{event.db_total:,}")
        if self._last_coverage is not None:
            self._complete_coverage.setText(f"{self._last_coverage.coverage_pct:.1f}%")
        else:
            self._complete_coverage.setText("—")
        self._complete_duration.setText(_format_clock(elapsed))
        self._complete_size.setText(_format_bytes(event.db_total * _EST_BYTES_PER_ROW))

    def on_failed(self, event: DownloadFailed) -> None:
        self._timer.stop()
        self._set_mode("failed")
        self._error_line.setText(f"{event.symbol} — {event.reason}")
        self._error_detail.setPlainText(event.reason)
        self._error_detail.hide()
        self._error_details_button.setText("View Details")

    def on_coverage(self, event: DownloadCoverage) -> None:
        self._last_coverage = event
        self._set_mode("coverage")
        self._cov_symbol.setText(event.symbol)
        self._cov_interval.setText(event.interval)
        self._cov_range.setText(f"{event.earliest or '—'} → {event.latest or '—'}")
        self._coverage_label.setText(
            f"{event.coverage_pct:.1f}% · {event.row_count} rows · "
            f"{event.trading_days} trading days · {event.state}"
        )
        gaps = []
        if event.missing_head:
            gaps.append("head gap")
        if event.missing_tail:
            gaps.append("tail gap")
        self._cov_rows.setText(
            f"{', '.join(gaps) if gaps else 'no gaps'} · "
            f"listing boundary {'verified' if event.listing_start_verified else 'not verified'}"
        )
        if self._mode == "complete":
            self._complete_coverage.setText(f"{event.coverage_pct:.1f}%")

    # ── internals ────────────────────────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._run_card.setVisible(mode == "running")
        self._complete_card.setVisible(mode == "complete")
        self._error_card.setVisible(mode == "failed")
        self._coverage_card.setVisible(mode == "coverage")

    def _tick_performance(self) -> None:
        if self._started_at is None:
            return
        elapsed = (datetime.now() - self._started_at).total_seconds()
        self._perf_elapsed.setText(_format_clock(elapsed))
        if elapsed > 0:
            self._perf_rows.setText(f"{self._total_rows / elapsed:,.0f}")
        else:
            self._perf_rows.setText("0")
        chunk = self._progress.value()
        if chunk > 0 and self._progress.maximum() > 0:
            remaining = self._progress.maximum() - chunk
            eta = elapsed * remaining / chunk
            self._perf_eta.setText(_format_clock(eta))
        else:
            self._perf_eta.setText("—")
        self._perf_size.setText(_format_bytes(self._total_rows * _EST_BYTES_PER_ROW))

    def _on_retry(self) -> None:
        if self._last_start is None:
            return
        self.retry_requested.emit(
            self._last_start.symbol,
            self._last_start.interval,
            self._last_start.from_date,
            self._last_start.to_date,
        )

    def _on_view_coverage(self) -> None:
        if self._last_completed is None:
            return
        self.coverage_requested.emit(self._last_completed.symbol, self._last_completed.interval)

    def _toggle_error_details(self) -> None:
        expanded = not self._error_detail.isVisible()
        self._error_detail.setVisible(expanded)
        self._error_details_button.setText("Hide Details" if expanded else "View Details")

    def _toggle_advanced(self) -> None:
        expanded = not self._provider_detail.isVisible()
        self._provider_detail.setVisible(expanded)
        self._env_fallback_header.setVisible(expanded)
        self._env_fallback.setVisible(expanded)
        self._advanced_button.setText("Advanced ▴" if expanded else "Advanced ▾")

    def _show_credentials_dialog(self) -> None:
        if self._credentials_manager is None:
            return
        dialog = ProviderCredentialsDialog(self._credentials_manager, self)
        dialog.exec()
        # The dialog may have applied (tested) or saved/cleared values —
        # always restore the provider to its persisted state and refresh
        # the status card.
        ready, reason = self._credentials_manager.reload()
        self.set_provider(ready, reason)
