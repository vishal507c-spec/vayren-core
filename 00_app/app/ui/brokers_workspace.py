"""BrokersWorkspace — the SYSTEM → BROKERS management screen.

Single source of truth for broker configuration/authentication is the
:class:`~app.services.broker_manager.BrokerManager`; this widget is a
PURE VIEW over its snapshot. Every action goes out through Qt signals —
the widget never touches the credential store, the registry, the SDK or
the network, and secrets never enter it (the API key arrives pre-masked
from the backend; the secret fields start empty and are sent once).

Card states follow :class:`~broker.status.BrokerStatus` strings:
NOT_CONFIGURED / LOGIN_REQUIRED / AUTHENTICATING / CONNECTED /
DISCONNECTED / ERROR / *_NOT_READY / LIVE_READY.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t
from app.ui.ui_kit import Badge

_REFRESH_MS = 1000
_STATUS_TONE = {
    "CONNECTED": "ok",
    "LIVE_READY": "ok",
    "LOGIN_REQUIRED": "warn",
    "AUTHENTICATING": "warn",
    "NOT_CONFIGURED": "muted",
    "DISCONNECTED": "bad",
    "ERROR": "bad",
    "ACCOUNT_NOT_READY": "bad",
    "MARKET_DATA_NOT_READY": "bad",
    "EXECUTION_NOT_READY": "bad",
}
_CHECK_ROWS = (
    ("connection", "Connection"),
    ("account", "Account"),
    ("funds", "Funds"),
    ("positions", "Positions"),
    ("orders", "Orders"),
    ("market_data", "Market Data"),
)


class _BrokerCard(QWidget):
    """One venue's status + actions (built once per broker id)."""

    configure_requested = Signal(str, dict)
    login_requested = Signal(str)
    disconnect_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, broker_id: str, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._broker_id = broker_id
        self.setStyleSheet(
            "QWidget { background: palette(base); border: 1px solid palette(mid);"
            " border-radius: 4px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel(display_name.upper(), self)
        title.setStyleSheet("font-size: 13px; font-weight: 800; border: none;")
        head.addWidget(title)
        head.addStretch(1)
        self._status = Badge(self)
        head.addWidget(self._status)
        lay.addLayout(head)

        self._reason = QLabel("", self)
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        lay.addWidget(self._reason)

        grid_host = QWidget(self)
        grid_host.setStyleSheet("border: none;")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        self._check_labels: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(_CHECK_ROWS):
            name = QLabel(label, grid_host)
            name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
            value = QLabel("—", grid_host)
            value.setStyleSheet("font-size: 11px; border: none;")
            grid.addWidget(name, index // 2, (index % 2) * 2)
            grid.addWidget(value, index // 2, (index % 2) * 2 + 1)
            self._check_labels[key] = value
        lay.addWidget(grid_host)

        self._key_label = QLabel("", self)
        self._key_label.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        lay.addWidget(self._key_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._configure_btn = QPushButton("CONFIGURE", self)
        self._login_btn = QPushButton("LOGIN TO BROKER", self)
        self._disconnect_btn = QPushButton("DISCONNECT", self)
        self._remove_btn = QPushButton("REMOVE", self)
        for btn in (self._configure_btn, self._login_btn, self._disconnect_btn, self._remove_btn):
            btn.setStyleSheet(t.BUTTON_QSS)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        self._configure_btn.clicked.connect(self._open_configure)
        self._login_btn.clicked.connect(lambda: self.login_requested.emit(self._broker_id))
        self._disconnect_btn.clicked.connect(
            lambda: self.disconnect_requested.emit(self._broker_id)
        )
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._broker_id))
        self._lay = lay
        self._form: QWidget | None = None

    def _open_configure(self) -> None:
        """Inline config form (API key + secret). Starts collapsed."""
        if self._form is not None:
            self._form.setParent(None)
            self._form = None
            return
        from PySide6.QtWidgets import QFormLayout

        self._form = QWidget(self)
        self._form.setStyleSheet("border: none;")
        form = QFormLayout(self._form)
        form.setContentsMargins(0, 4, 0, 4)
        self._api_key_edit = QLineEdit(self._form)
        self._api_key_edit.setPlaceholderText("API key")
        self._api_secret_edit = QLineEdit(self._form)
        self._api_secret_edit.setPlaceholderText("API secret (stored securely, never shown)")
        self._api_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key", self._api_key_edit)
        form.addRow("API Secret", self._api_secret_edit)
        save = QPushButton("SAVE", self._form)
        save.setStyleSheet(t.BUTTON_QSS)
        save.clicked.connect(self._save)
        form.addRow("", save)
        self._lay.addWidget(self._form)

    def _save(self) -> None:
        values = {
            "api_key": self._api_key_edit.text() if hasattr(self, "_api_key_edit") else "",
            "api_secret": self._api_secret_edit.text() if hasattr(self, "_api_secret_edit") else "",
        }
        self.configure_requested.emit(self._broker_id, values)
        if self._form is not None:
            self._form.setParent(None)
        self._form = None

    def render_card(self, card: dict[str, Any]) -> None:
        status = str(card.get("status", "NOT_CONFIGURED"))
        self._status.set_status(
            f"● {status}" if status in ("CONNECTED", "LIVE_READY") else f"○ {status}",
            _STATUS_TONE.get(status, "muted"),
        )
        reason = str(card.get("reason", "") or "")
        self._reason.setText(reason)
        self._reason.setVisible(bool(reason))
        checks = card.get("checks") or {}
        for key, label_widget in self._check_labels.items():
            value = str(checks.get(key, ""))
            label_widget.setText(value or "—")
            tone = "ok" if value == "READY" else ("bad" if value else "muted")
            color = {"ok": t.POS, "bad": t.NEG, "muted": t.MUTED}.get(tone, t.MUTED)
            label_widget.setStyleSheet(f"color: {color}; font-size: 11px; border: none;")
        masked = str(card.get("api_key_masked", "") or "")
        self._key_label.setText(f"API key: {masked}" if masked else "API key: not set")
        configured = bool(card.get("configured"))
        self._configure_btn.setText("CONFIGURE" if configured else "ADD API KEY")
        venue_name = str(card.get("name", "") or "BROKER").upper()
        self._login_btn.setText(
            "RE-AUTHENTICATE" if status in ("CONNECTED", "LIVE_READY") else f"LOGIN TO {venue_name}"
        )
        self._login_btn.setVisible(configured)
        self._login_btn.setEnabled(bool(card.get("can_login")))
        self._disconnect_btn.setEnabled(bool(card.get("can_disconnect")))


class BrokersWorkspace(QWidget):
    """SYSTEM → BROKERS. Pure view; manager owns all state."""

    configure_requested = Signal(str, dict)
    login_requested = Signal(str)
    disconnect_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = state_provider
        self._cards: dict[str, _BrokerCard] = {}
        self._empty_label: QLabel | None = None
        self._add_host: QWidget | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("SYSTEM  ·  BROKERS", self)
        title.setStyleSheet("font-size: 14px; font-weight: 800; letter-spacing: 1px;")
        root.addWidget(title)
        self._note = QLabel("", self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        root.addWidget(self._note)

        self._cards_host = QWidget(self)
        self._cards_lay = QVBoxLayout(self._cards_host)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(8)
        root.addWidget(self._cards_host, 1)

        bottom = QHBoxLayout()
        self._add_combo = QComboBox(self)
        self._add_combo.setStyleSheet(t.INPUT_QSS)
        self._add_btn = QPushButton("+ ADD BROKER", self)
        self._add_btn.setStyleSheet(t.BUTTON_QSS)
        self._add_btn.clicked.connect(self._on_add)
        bottom.addWidget(self._add_combo)
        bottom.addWidget(self._add_btn)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()
        self._timer.start()

    # ── state input ──────────────────────────────────────────────

    def set_state_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        self._provider = provider
        self.refresh()

    def refresh(self) -> None:
        with contextlib.suppress(Exception):
            if self._provider is not None:
                state = self._provider()
                if isinstance(state, dict):
                    self._render(state)

    def _render(self, state: dict[str, Any]) -> None:
        cards = state.get("brokers") or []
        known = set(self._cards)
        seen: set[str] = set()
        for card in cards:
            if not isinstance(card, dict):
                continue
            broker_id = str(card.get("id", ""))
            seen.add(broker_id)
            widget = self._cards.get(broker_id)
            if widget is None:
                widget = _BrokerCard(broker_id, str(card.get("name", broker_id)), self._cards_host)
                widget.configure_requested.connect(self.configure_requested.emit)
                widget.login_requested.connect(self.login_requested.emit)
                widget.disconnect_requested.connect(self.disconnect_requested.emit)
                widget.remove_requested.connect(self.remove_requested.emit)
                self._cards_lay.addWidget(widget)
                self._cards[broker_id] = widget
            widget.render_card(card)
        for stale in known - seen:
            widget = self._cards.pop(stale, None)
            if widget is not None:
                widget.setParent(None)
        if not cards:
            if self._empty_label is None:
                self._empty_label = QLabel("No broker configured", self._cards_host)
                self._empty_label.setStyleSheet(f"color: {t.MUTED}; font-size: 12px;")
                self._cards_lay.addWidget(self._empty_label)
        elif self._empty_label is not None:
            self._empty_label.setParent(None)
            self._empty_label = None
        choices = [str(card.get("id", "")) for card in cards if isinstance(card, dict)]
        current = self._add_combo.currentText()
        self._add_combo.blockSignals(True)
        try:
            self._add_combo.clear()
            self._add_combo.addItems(choices or ["zerodha"])
            if current in choices:
                self._add_combo.setCurrentText(current)
        finally:
            self._add_combo.blockSignals(False)
        url = str(state.get("callback_url", "") or "")
        self._note.setText(
            "Official Zerodha login opens in your browser; VAYREN captures the"
            " redirect automatically. Register this redirect URL in your Kite"
            f" Connect app: {url}"
            if url
            else ""
        )

    def _on_add(self) -> None:
        broker_id = self._add_combo.currentText()
        if broker_id:
            card = self._cards.get(broker_id)
            if card is not None:
                card._open_configure()


__all__ = ["BrokersWorkspace"]
