"""BrokersWorkspace — the SYSTEM → BROKERS infrastructure control center.

Single source of truth for broker configuration/authentication is the
:class:`~app.services.broker_manager.BrokerManager`; this widget is a
PURE VIEW over its snapshot. Every action goes out through Qt signals —
the widget never touches the credential store, the registry, the SDK or
the network, and secrets never enter it (the API key arrives pre-masked
from the backend; the secret fields start empty and are sent once).

Information hierarchy (top → bottom): connection status hero → broker +
account → capability grid → metrics → actions → connection settings →
integration details. Technical details (callback URL) stay collapsed in
per-card Integration Details; they never dominate trading status.

Card states follow :class:`~broker.status.BrokerStatus` strings:
NOT_CONFIGURED / CONFIGURING / LOGIN_REQUIRED / AUTHENTICATING /
CONNECTED / DISCONNECTED / ERROR / *_NOT_READY / LIVE_READY. The
transient SYNCING indicator is local UI feedback for a refresh round
trip, not a backend state.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t
from app.ui.ui_kit import Badge

_REFRESH_MS = 1000
_REFRESH_BUSY_MS = 4000
_MAX_CONTENT_WIDTH = 1280

# status → (tone, glyph, label, explanation). Icon + text, never color alone.
_STATUS_META: dict[str, tuple[str, str, str, str]] = {
    "NOT_CONFIGURED": (
        "muted",
        "○",
        "NOT CONFIGURED",
        "Connect a broker to enable trading and market data.",
    ),
    "CONFIGURING": ("warn", "◐", "CONFIGURING", "Saving configuration…"),
    "LOGIN_REQUIRED": (
        "warn",
        "○",
        "LOGIN REQUIRED",
        "Session expired — log in to reconnect.",
    ),
    "AUTHENTICATING": (
        "warn",
        "◐",
        "CONNECTING",
        "Waiting for the broker login in your browser…",
    ),
    "CONNECTED": ("ok", "●", "CONNECTED", "Broker connection is healthy."),
    "LIVE_READY": ("ok", "●", "CONNECTED", "Broker connection is healthy."),
    "DISCONNECTED": (
        "bad",
        "○",
        "DISCONNECTED",
        "Connection lost — reconnect to resume trading.",
    ),
    "ERROR": ("bad", "⚠", "ERROR", "Connection failed — check details and retry."),
    "ACCOUNT_NOT_READY": (
        "warn",
        "◐",
        "DEGRADED",
        "Connected with limited capabilities — see health below.",
    ),
    "MARKET_DATA_NOT_READY": (
        "warn",
        "◐",
        "DEGRADED",
        "Connected with limited capabilities — see health below.",
    ),
    "EXECUTION_NOT_READY": (
        "warn",
        "◐",
        "DEGRADED",
        "Connected with limited capabilities — see health below.",
    ),
}

_CHECK_ROWS = (
    ("connection", "Connection"),
    ("account", "Account"),
    ("funds", "Funds"),
    ("positions", "Positions"),
    ("orders", "Orders"),
    ("market_data", "Market Data"),
)

_CONNECTED_STATUSES = ("CONNECTED", "LIVE_READY")
_WORKING_STATUSES = ("AUTHENTICATING", "CONFIGURING")
_DEGRADED_STATUSES = ("ACCOUNT_NOT_READY", "MARKET_DATA_NOT_READY", "EXECUTION_NOT_READY")


def _format_inr(value: Any) -> str:
    """Compact rupee rendering; unparseable/None → em dash (never zero)."""
    if value is None:
        return "—"
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _check_display(value: str) -> tuple[str, str]:
    """Normalize a raw check value → (display text, tone)."""
    if not value:
        return "— Unavailable", "muted"
    if value == "READY":
        return "✓ Ready", "ok"
    if value.startswith("FAILED"):
        return "⚠ Check failed", "bad"
    return value, "muted"


def _venue_subtitle(display_name: str, venue_subtitle: str = "") -> str:
    """Venue subtitle assembled from snapshot data — never a broker literal.

    The venue's own product label arrives through the snapshot
    (``BrokerSpec.extra`` → manager), so this surface neither branches on a
    broker name nor pins an alias (design §3.2 rule 6 — the registry, via the
    snapshot, is the mechanism).
    """
    if venue_subtitle:
        return f"{display_name} {venue_subtitle}"
    return f"{display_name} connection"


def _divider(parent: QWidget) -> QWidget:
    line = QWidget(parent)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {t.BORDER}; border: none;")
    return line


class _BrokerCard(QWidget):
    """One venue's control surface (built once per broker id)."""

    configure_requested = Signal(str, dict)
    login_requested = Signal(str)
    refresh_requested = Signal(str)
    disconnect_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, broker_id: str, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._broker_id = broker_id
        self._display_name = display_name
        self._callback_url = ""
        self._busy_refresh = False
        self._last_seen_sync = ""
        self.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 4px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # ── hero: broker identity + connection status ──
        head = QHBoxLayout()
        head.setSpacing(8)
        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        self._name_label = QLabel(display_name.upper(), self)
        self._name_label.setStyleSheet("font-size: 18px; font-weight: 800; border: none;")
        name_box.addWidget(self._name_label)
        self._sub_label = QLabel(_venue_subtitle(display_name), self)
        self._sub_label.setStyleSheet(f"color: {t.MUTED}; font-size: 12px; border: none;")
        name_box.addWidget(self._sub_label)
        head.addLayout(name_box)
        head.addStretch(1)
        self._status = Badge(self)
        head.addWidget(self._status, alignment=Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        self._reason = QLabel("", self)
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {t.TEXT2}; font-size: 13px; border: none;")
        lay.addWidget(self._reason)

        # ── hero stats: account / connection / last sync ──
        hero = QGridLayout()
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setHorizontalSpacing(16)
        hero.setVerticalSpacing(2)
        self._hero_account_value = self._hero_cell(hero, 0, "ACCOUNT")
        self._hero_conn_value = self._hero_cell(hero, 1, "CONNECTION")
        self._hero_sync_value = self._hero_cell(hero, 2, "LAST SYNC")
        lay.addLayout(hero)
        lay.addWidget(_divider(self))

        # ── broker health: capability grid ──
        health_head = QHBoxLayout()
        health_head.setSpacing(8)
        health_title = QLabel("BROKER HEALTH", self)
        health_title.setStyleSheet(
            f"color: {t.MUTED}; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; border: none;"
        )
        health_head.addWidget(health_title)
        health_head.addStretch(1)
        self._health_summary = Badge(self)
        health_head.addWidget(self._health_summary)
        lay.addLayout(health_head)

        grid_host = QWidget(self)
        grid_host.setStyleSheet("border: none;")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self._check_labels: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(_CHECK_ROWS):
            cell = QVBoxLayout()
            cell.setSpacing(1)
            name = QLabel(label.upper(), grid_host)
            name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
            cell.addWidget(name)
            value = QLabel("— Unavailable", grid_host)
            value.setStyleSheet("font-size: 13px; font-weight: 600; border: none;")
            cell.addWidget(value)
            grid.addLayout(cell, index // 3, index % 3)
            self._check_labels[key] = value
        lay.addWidget(grid_host)
        lay.addWidget(_divider(self))

        # ── metrics: account / funds / positions / orders ──
        metrics = QGridLayout()
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setHorizontalSpacing(16)
        metrics.setVerticalSpacing(6)
        self._metric_account = self._metric_cell(metrics, 0, "ACCOUNT")
        self._metric_funds, self._metric_funds_sub = self._metric_cell_with_sub(metrics, 1, "FUNDS")
        self._metric_positions, self._metric_positions_sub = self._metric_cell_with_sub(
            metrics, 2, "POSITIONS"
        )
        self._metric_orders, self._metric_orders_sub = self._metric_cell_with_sub(
            metrics, 3, "ORDERS"
        )
        lay.addLayout(metrics)
        lay.addWidget(_divider(self))

        # ── action bar ──
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._refresh_btn = QPushButton("REFRESH", self)
        self._login_btn = QPushButton("CONNECT", self)
        self._configure_btn = QPushButton("CONFIGURE", self)
        self._disconnect_btn = QPushButton("DISCONNECT", self)
        self._remove_btn = QPushButton("REMOVE", self)
        self._remove_btn.setVisible(False)
        for btn in (self._refresh_btn, self._login_btn, self._configure_btn, self._disconnect_btn):
            btn.setStyleSheet(t.BUTTON_QSS)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        self._more_btn = QToolButton(self)
        self._more_btn.setText("⋯")
        self._more_btn.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        remove_action = self._more_btn.menu() or None
        if remove_action is None:
            from PySide6.QtWidgets import QMenu

            menu = QMenu(self._more_btn)
            menu.setStyleSheet(t.MENU_QSS)
            action = menu.addAction("Remove broker…")
            action.triggered.connect(lambda: self._remove_btn.click())
            self._more_btn.setMenu(menu)
        buttons.addWidget(self._more_btn)
        lay.addLayout(buttons)

        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._login_btn.clicked.connect(self._on_login_clicked)
        self._configure_btn.clicked.connect(self._open_configure)
        self._disconnect_btn.clicked.connect(
            lambda: self.disconnect_requested.emit(self._broker_id)
        )
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._broker_id))

        # ── connection settings (collapsible, secrets never shown) ──
        self._settings_toggle = QToolButton(self)
        self._settings_toggle.setText("▸ CONNECTION SETTINGS")
        self._settings_toggle.setCheckable(True)
        self._settings_toggle.setChecked(False)
        self._settings_toggle.setStyleSheet(
            f"QToolButton {{ color: {t.MUTED}; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; border: none; text-align: left; padding: 2px 0; }}"
        )
        self._settings_toggle.setSizePolicy(
            self._settings_toggle.sizePolicy().horizontalPolicy(),
            self._settings_toggle.sizePolicy().verticalPolicy(),
        )
        lay.addWidget(self._settings_toggle)
        self._settings_host = QWidget(self)
        self._settings_host.setStyleSheet("border: none;")
        settings_lay = QVBoxLayout(self._settings_host)
        settings_lay.setContentsMargins(0, 0, 0, 0)
        settings_lay.setSpacing(6)
        self._key_label = QLabel("", self._settings_host)
        self._key_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 12px; border: none;")
        settings_lay.addWidget(self._key_label)
        lay.addWidget(self._settings_host)
        self._settings_host.setVisible(False)
        self._settings_toggle.toggled.connect(self._on_settings_toggled)

        # ── integration details (collapsible, technical — never primary) ──
        self._integration_toggle = QToolButton(self)
        self._integration_toggle.setText("▸ INTEGRATION DETAILS")
        self._integration_toggle.setCheckable(True)
        self._integration_toggle.setChecked(False)
        self._integration_toggle.setStyleSheet(
            f"QToolButton {{ color: {t.MUTED}; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; border: none; text-align: left; padding: 2px 0; }}"
        )
        lay.addWidget(self._integration_toggle)
        self._integration_host = QWidget(self)
        self._integration_host.setStyleSheet("border: none;")
        integration_lay = QHBoxLayout(self._integration_host)
        integration_lay.setContentsMargins(0, 0, 0, 0)
        integration_lay.setSpacing(8)
        callback_name = QLabel("CALLBACK URL", self._integration_host)
        callback_name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        integration_lay.addWidget(callback_name)
        self._callback_label = QLabel("", self._integration_host)
        self._callback_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._callback_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 12px; border: none;")
        integration_lay.addWidget(self._callback_label, 1)
        self._copy_btn = QPushButton("COPY", self._integration_host)
        self._copy_btn.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._copy_btn.clicked.connect(self._on_copy_callback)
        integration_lay.addWidget(self._copy_btn)
        lay.addWidget(self._integration_host)
        self._integration_host.setVisible(False)
        self._integration_toggle.toggled.connect(self._on_integration_toggled)

        self._lay = lay
        self._form: QWidget | None = None
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.timeout.connect(self._clear_refresh_busy)

    # ── small builders ──────────────────────────────────────

    def _hero_cell(self, grid: QGridLayout, column: int, caption: str) -> QLabel:
        box = QVBoxLayout()
        box.setSpacing(1)
        name = QLabel(caption, self)
        name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        box.addWidget(name)
        value = QLabel("—", self)
        value.setStyleSheet("font-size: 15px; font-weight: 700; border: none;")
        box.addWidget(value)
        grid.addLayout(box, 0, column)
        return value

    def _metric_cell(self, grid: QGridLayout, column: int, caption: str) -> QLabel:
        box = QVBoxLayout()
        box.setSpacing(1)
        name = QLabel(caption, self)
        name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        box.addWidget(name)
        value = QLabel("—", self)
        value.setStyleSheet(f"font-size: {t.FS_METRIC}px; font-weight: 700; border: none;")
        box.addWidget(value)
        grid.addLayout(box, 0, column)
        return value

    def _metric_cell_with_sub(
        self, grid: QGridLayout, column: int, caption: str
    ) -> tuple[QLabel, QLabel]:
        box = QVBoxLayout()
        box.setSpacing(1)
        name = QLabel(caption, self)
        name.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        box.addWidget(name)
        value = QLabel("—", self)
        value.setStyleSheet(f"font-size: {t.FS_METRIC}px; font-weight: 700; border: none;")
        box.addWidget(value)
        sub = QLabel("", self)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: 11px; border: none;")
        box.addWidget(sub)
        grid.addLayout(box, 0, column)
        return value, sub

    # ── collapsibles / actions ──────────────────────────────

    def _on_settings_toggled(self, checked: bool) -> None:
        self._settings_toggle.setText(
            "▾ CONNECTION SETTINGS" if checked else "▸ CONNECTION SETTINGS"
        )
        self._settings_host.setVisible(checked)

    def _on_integration_toggled(self, checked: bool) -> None:
        text = "▾ INTEGRATION DETAILS" if checked else "▸ INTEGRATION DETAILS"
        self._integration_toggle.setText(text)
        self._integration_host.setVisible(checked)

    def _on_copy_callback(self) -> None:
        with contextlib.suppress(Exception):
            clipboard = QApplication.clipboard()
            if clipboard is not None and self._callback_url:
                clipboard.setText(self._callback_url)
            self._copy_btn.setText("COPIED")
            QTimer.singleShot(1500, lambda: self._copy_btn.setText("COPY"))

    def _on_login_clicked(self) -> None:
        status = str(getattr(self, "_last_status", "NOT_CONFIGURED"))
        if status == "NOT_CONFIGURED":
            self._open_configure()
            return
        self.login_requested.emit(self._broker_id)

    def _on_refresh_clicked(self) -> None:
        self._busy_refresh = True
        self._busy_timer.start(_REFRESH_BUSY_MS)
        self._refresh_btn.setText("SYNCING…")
        self._refresh_btn.setEnabled(False)
        self.refresh_requested.emit(self._broker_id)

    def _clear_refresh_busy(self) -> None:
        self._busy_refresh = False
        self.refresh()

    def refresh(self) -> None:
        """Re-render from the last snapshot if the parent supplied one."""
        render = getattr(self, "_last_card", None)
        if isinstance(render, dict):
            self.render_card(render)

    def set_callback_url(self, url: str) -> None:
        self._callback_url = url or ""
        display = self._callback_url.replace("http://", "").replace("https://", "")
        self._callback_label.setText(display or "—")
        self._copy_btn.setVisible(bool(self._callback_url))

    def _open_configure(self) -> None:
        """Inline credentials form (API key + secret). Starts collapsed."""
        if self._form is not None:
            self._form.setParent(None)
            self._form = None
            return
        if not self._settings_toggle.isChecked():
            self._settings_toggle.setChecked(True)
        self._form = QWidget(self._settings_host)
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
        row = QHBoxLayout()
        row.setSpacing(6)
        save = QPushButton("SAVE", self._form)
        save.setStyleSheet(t.BUTTON_QSS)
        save.clicked.connect(self._save)
        row.addWidget(save)
        cancel = QPushButton("CANCEL", self._form)
        cancel.setStyleSheet(t.QUIET_BUTTON_QSS)
        cancel.clicked.connect(self._open_configure)
        row.addWidget(cancel)
        row.addStretch(1)
        form.addRow("", row)
        layout = self._settings_host.layout()
        assert layout is not None
        layout.addWidget(self._form)

    def _save(self) -> None:
        values = {
            "api_key": self._api_key_edit.text() if hasattr(self, "_api_key_edit") else "",
            "api_secret": self._api_secret_edit.text() if hasattr(self, "_api_secret_edit") else "",
        }
        self.configure_requested.emit(self._broker_id, values)
        if self._form is not None:
            self._form.setParent(None)
        self._form = None

    # ── rendering (pure view of one snapshot card) ──────────

    def render_card(self, card: dict[str, Any]) -> None:
        self._last_card = dict(card)
        self._sub_label.setText(
            _venue_subtitle(
                str(card.get("name", "") or self._display_name),
                str(card.get("venue_subtitle", "") or ""),
            )
        )
        status = str(card.get("status", "NOT_CONFIGURED"))
        self._last_status = status
        tone, glyph, label, explanation = _STATUS_META.get(
            status, ("muted", "○", status.replace("_", " "), "Status unavailable.")
        )
        reason = str(card.get("reason", "") or "")
        if self._busy_refresh:
            seen = str(card.get("last_sync", "") or "")
            if seen and seen != self._last_seen_sync:
                self._busy_refresh = False
                self._busy_timer.stop()
        if self._busy_refresh:
            self._status.set_status("◐ SYNCING", "warn")
        else:
            self._status.set_status(f"{glyph} {label}", tone)
        duplicate = bool(reason) and reason.strip().lower() in explanation.lower()
        if status == "NOT_CONFIGURED" or not reason or duplicate:
            self._reason.setText(explanation)
        else:
            self._reason.setText(f"{explanation} {reason}")
        self._reason.setVisible(True)

        account_id = str(card.get("account_id", "") or "")
        self._hero_account_value.setText(account_id or "—")
        conn_text, conn_color = self._connection_display(status, card.get("checks") or {})
        self._hero_conn_value.setText(conn_text)
        self._hero_conn_value.setStyleSheet(
            f"color: {conn_color}; font-size: 15px; font-weight: 700; border: none;"
        )
        last_sync = str(card.get("last_sync", "") or "")
        if last_sync:
            self._last_seen_sync = last_sync
        self._hero_sync_value.setText(last_sync or "—")

        checks = card.get("checks") or {}
        for key, label_widget in self._check_labels.items():
            raw = str(checks.get(key, "") or "")
            display, check_tone = _check_display(raw)
            label_widget.setText(display)
            color = {"ok": t.POS, "bad": t.NEG, "muted": t.MUTED}.get(check_tone, t.MUTED)
            label_widget.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: 600; border: none;"
            )
            label_widget.setToolTip(raw if raw and raw != "READY" else display)
        self._render_health_summary(status, checks)

        funds = card.get("funds") if isinstance(card.get("funds"), dict) else {}
        self._metric_account.setText(account_id or "—")
        available = funds.get("available") if isinstance(funds, dict) else None
        self._metric_funds.setText(_format_inr(available))
        used = funds.get("used") if isinstance(funds, dict) else None
        total = funds.get("total") if isinstance(funds, dict) else None
        if available is None and used is None and total is None:
            self._metric_funds_sub.setText("Not available")
        else:
            parts = []
            if used is not None:
                parts.append(f"Used {_format_inr(used)}")
            if total is not None:
                parts.append(f"Total {_format_inr(total)}")
            self._metric_funds_sub.setText(" · ".join(parts))
        positions_open = card.get("positions_open")
        if positions_open is None:
            self._metric_positions.setText("—")
            self._metric_positions_sub.setText("Not available")
        elif int(positions_open) == 0:
            self._metric_positions.setText("0")
            self._metric_positions_sub.setText("No open positions")
        else:
            self._metric_positions.setText(str(positions_open))
            self._metric_positions_sub.setText(f"{positions_open} open")
        orders_open = card.get("orders_open")
        if orders_open is None:
            self._metric_orders.setText("—")
            self._metric_orders_sub.setText("No data")
        elif int(orders_open) == 0:
            self._metric_orders.setText("0")
            self._metric_orders_sub.setText("No open orders")
        else:
            self._metric_orders.setText(str(orders_open))
            self._metric_orders_sub.setText(f"{orders_open} open")

        masked = str(card.get("api_key_masked", "") or "")
        configured = bool(card.get("configured"))
        if configured and masked:
            self._key_label.setText(f"Credentials  ● Securely configured · {masked}")
        elif configured:
            self._key_label.setText("Credentials  ● Configured")
        else:
            self._key_label.setText("Credentials  ○ Not configured")

        venue_name = str(card.get("name", "") or "BROKER").upper()
        can_login = bool(card.get("can_login"))
        can_disconnect = bool(card.get("can_disconnect"))
        can_refresh = bool(card.get("can_refresh", configured))
        working = status in _WORKING_STATUSES
        connected = status in _CONNECTED_STATUSES or status in _DEGRADED_STATUSES

        self._refresh_btn.setVisible(configured)
        if self._busy_refresh:
            self._refresh_btn.setText("SYNCING…")
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setStyleSheet(t.BUTTON_QSS)
        else:
            self._refresh_btn.setText("REFRESH")
            self._refresh_btn.setEnabled(can_refresh and not working)
            self._refresh_btn.setStyleSheet(t.PRIMARY_QSS if connected else t.BUTTON_QSS)

        if status == "NOT_CONFIGURED":
            self._login_btn.setText(f"CONNECT {venue_name}")
            self._login_btn.setEnabled(True)
            self._login_btn.setStyleSheet(t.PRIMARY_QSS)
        elif working:
            self._login_btn.setText("CONNECTING…")
            self._login_btn.setEnabled(False)
            self._login_btn.setStyleSheet(t.BUTTON_QSS)
        elif status in _CONNECTED_STATUSES:
            self._login_btn.setText("RE-AUTHENTICATE")
            self._login_btn.setEnabled(can_login)
            self._login_btn.setStyleSheet(t.BUTTON_QSS)
        elif status in _DEGRADED_STATUSES:
            self._login_btn.setText("RECONNECT")
            self._login_btn.setEnabled(can_login)
            self._login_btn.setStyleSheet(t.BUTTON_QSS)
        else:
            self._login_btn.setText(f"CONNECT {venue_name}")
            self._login_btn.setEnabled(can_login)
            self._login_btn.setStyleSheet(t.PRIMARY_QSS)
        self._login_btn.setVisible(True)

        self._configure_btn.setText("SETTINGS" if configured else "CONFIGURE")
        self._configure_btn.setStyleSheet(t.BUTTON_QSS)
        self._disconnect_btn.setVisible(configured)
        self._disconnect_btn.setEnabled(can_disconnect)
        self._more_btn.setVisible(configured)

    def _render_health_summary(self, status: str, checks: dict[str, Any]) -> None:
        if status == "NOT_CONFIGURED":
            self._health_summary.set_status("— NOT CONNECTED", "muted")
            return
        values = [str(checks.get(key, "") or "") for key, _ in _CHECK_ROWS]
        if not any(values):
            self._health_summary.set_status("— NO DATA", "muted")
            return
        failed = [v for v in values if v and v != "READY"]
        if not failed:
            self._health_summary.set_status("✓ HEALTHY", "ok")
        else:
            self._health_summary.set_status("⚠ PARTIAL", "warn")

    @staticmethod
    def _connection_display(status: str, checks: dict[str, Any]) -> tuple[str, str]:
        if status in _CONNECTED_STATUSES:
            failed = [k for k, v in checks.items() if v and v != "READY"]
            if failed:
                return "Degraded", t.WARN
            return "Healthy", t.POS
        if status in _DEGRADED_STATUSES:
            return "Degraded", t.WARN
        if status in _WORKING_STATUSES:
            return "Working…", t.WARN
        if status in ("DISCONNECTED", "ERROR"):
            return "Down", t.NEG
        return "Not connected", t.MUTED


class _AddBrokerDialog(QDialog):
    """Focused broker selector — extensible to future venues."""

    broker_chosen = Signal(str)

    def __init__(
        self,
        brokers: list[dict[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add broker")
        self.setModal(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = QLabel("ADD BROKER", self)
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        lay.addWidget(title)
        sub = QLabel("Choose your broker", self)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: 13px;")
        lay.addWidget(sub)
        for broker in brokers:
            broker_id = str(broker.get("id", ""))
            name = str(broker.get("name", broker_id))
            row = QHBoxLayout()
            row.setSpacing(8)
            name_label = QLabel(name.upper(), self)
            name_label.setStyleSheet("font-size: 14px; font-weight: 700;")
            row.addWidget(name_label)
            row.addStretch(1)
            pick = QPushButton(f"SELECT {name.upper()}", self)
            pick.setStyleSheet(t.BUTTON_QSS)
            pick.clicked.connect(lambda _checked=False, bid=broker_id: self._choose(bid))
            row.addWidget(pick)
            lay.addLayout(row)
        future = QLabel("More brokers — coming soon", self)
        future.setStyleSheet(f"color: {t.MUTED}; font-size: 12px;")
        future.setEnabled(False)
        lay.addWidget(future)
        note = QLabel(
            "Additional venues plug into this same dashboard — no layout change needed.",
            self,
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {t.MUTED}; font-size: 12px;")
        lay.addWidget(note)
        close = QPushButton("CLOSE", self)
        close.setStyleSheet(t.QUIET_BUTTON_QSS)
        close.clicked.connect(self.reject)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _choose(self, broker_id: str) -> None:
        self.broker_chosen.emit(broker_id)
        self.accept()


class _ConnectDialog(QDialog):
    """Step-based Zerodha connection progress (info only — actions live on the card)."""

    def __init__(self, broker_id: str, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._broker_id = broker_id
        self.setWindowTitle(f"Connect {display_name}")
        self.setModal(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = QLabel(f"CONNECT {display_name.upper()}", self)
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        lay.addWidget(title)
        sub = QLabel("You can close this — the login continues in the background.", self)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: 12px;")
        lay.addWidget(sub)
        self._step_rows: list[tuple[QLabel, QLabel]] = []
        for number, step_title, desc in (
            ("1", "Authenticate", "Your broker login opens in your browser."),
            ("2", "Verify connection", "Checking account, funds, positions, orders, market data."),
            ("3", "Connected", "Return to the broker dashboard."),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel(number, self)
            dot.setFixedWidth(22)
            dot.setStyleSheet(f"color: {t.MUTED}; font-size: 13px; font-weight: 800;")
            row.addWidget(dot)
            body = QVBoxLayout()
            body.setSpacing(1)
            head = QLabel(step_title, self)
            head.setStyleSheet("font-size: 13px; font-weight: 700;")
            body.addWidget(head)
            detail = QLabel(desc, self)
            detail.setWordWrap(True)
            detail.setStyleSheet(f"color: {t.MUTED}; font-size: 12px;")
            body.addWidget(detail)
            row.addLayout(body, 1)
            lay.addLayout(row)
            self._step_rows.append((dot, head))
        self._status_line = QLabel("", self)
        self._status_line.setWordWrap(True)
        self._status_line.setStyleSheet(f"color: {t.TEXT2}; font-size: 12px;")
        lay.addWidget(self._status_line)
        close = QPushButton("CLOSE", self)
        close.setStyleSheet(t.BUTTON_QSS)
        close.clicked.connect(self.reject)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def update_card(self, card: dict[str, Any]) -> None:
        """Reflect the latest snapshot card (call on every workspace refresh)."""
        status = str(card.get("status", ""))
        checks = card.get("checks") or {}
        if status in _CONNECTED_STATUSES:
            self._mark(0, done=True)
            self._mark(1, done=True)
            self._mark(2, done=True, current=True)
            self._status_line.setText("✓ Zerodha connected successfully.")
        elif status in ("ACCOUNT_NOT_READY", "MARKET_DATA_NOT_READY", "EXECUTION_NOT_READY"):
            self._mark(0, done=True)
            self._mark(1, done=False, current=True)
            self._mark(2, done=False)
            failed = [k for k, v in checks.items() if v and v != "READY"]
            self._status_line.setText(f"Partial connection — re-check: {', '.join(failed)}.")
        elif status in _WORKING_STATUSES:
            self._mark(0, done=False, current=True)
            self._mark(1, done=False)
            self._mark(2, done=False)
            self._status_line.setText("Waiting for the broker login in your browser…")
        else:
            self._mark(0, done=False, current=True)
            self._mark(1, done=False)
            self._mark(2, done=False)
            reason = str(card.get("reason", "") or "")
            self._status_line.setText(reason or "Start the login from the broker card.")

    def _mark(self, index: int, *, done: bool, current: bool = False) -> None:
        dot, head = self._step_rows[index]
        if done:
            dot.setText("✓")
            dot.setStyleSheet(f"color: {t.POS}; font-size: 13px; font-weight: 800;")
            head.setStyleSheet(f"color: {t.POS}; font-size: 13px; font-weight: 700;")
        elif current:
            dot.setText("◐")
            dot.setStyleSheet(f"color: {t.WARN}; font-size: 13px; font-weight: 800;")
            head.setStyleSheet(f"color: {t.TEXT}; font-size: 13px; font-weight: 700;")
        else:
            dot.setText(str(index + 1))
            dot.setStyleSheet(f"color: {t.MUTED}; font-size: 13px; font-weight: 800;")
            head.setStyleSheet(f"color: {t.TEXT2}; font-size: 13px; font-weight: 700;")


class BrokersWorkspace(QWidget):
    """SYSTEM → BROKERS. Pure view; manager owns all state."""

    configure_requested = Signal(str, dict)
    login_requested = Signal(str)
    refresh_requested = Signal(str)
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
        self._last_brokers: list[dict[str, Any]] = []
        self._callback_url = ""
        self._connect_dialogs: dict[str, _ConnectDialog] = {}
        self._empty_label: QLabel | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("SYSTEM / BROKERS", self)
        title.setStyleSheet("font-size: 20px; font-weight: 800; letter-spacing: 0.5px;")
        title_box.addWidget(title)
        subtitle = QLabel("Broker connections and trading infrastructure", self)
        subtitle.setStyleSheet(f"color: {t.MUTED}; font-size: 13px;")
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self._add_btn = QPushButton("+ ADD BROKER", self)
        self._add_btn.setStyleSheet(t.PRIMARY_QSS)
        self._add_btn.clicked.connect(self._open_add_dialog)
        header.addWidget(self._add_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        self._content = QWidget(self._scroll)
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(12, 4, 12, 4)
        self._content_lay.setSpacing(12)
        self._scroll.setWidget(self._content)

        self._cards_host = QWidget(self._content)
        self._cards_lay = QVBoxLayout(self._cards_host)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(12)
        self._content_lay.addWidget(self._cards_host)

        self._empty_host = QWidget(self._content)
        empty_lay = QVBoxLayout(self._empty_host)
        empty_lay.setContentsMargins(24, 32, 24, 32)
        empty_lay.setSpacing(8)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_label = QLabel("No brokers connected", self._empty_host)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("font-size: 16px; font-weight: 800;")
        empty_lay.addWidget(self._empty_label)
        empty_detail = QLabel(
            "Connect a broker to enable trading, orders, positions, funds and market data.",
            self._empty_host,
        )
        empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_detail.setWordWrap(True)
        empty_detail.setStyleSheet(f"color: {t.MUTED}; font-size: 13px;")
        empty_lay.addWidget(empty_detail)
        empty_caps = QLabel("Trading · Orders · Positions · Funds · Market Data", self._empty_host)
        empty_caps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_caps.setStyleSheet(f"color: {t.TEXT2}; font-size: 12px;")
        empty_lay.addWidget(empty_caps)
        self._empty_add_btn = QPushButton("+ ADD BROKER", self._empty_host)
        self._empty_add_btn.setStyleSheet(t.PRIMARY_QSS)
        self._empty_add_btn.clicked.connect(self._open_add_dialog)
        empty_lay.addWidget(self._empty_add_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        self._content_lay.addWidget(self._empty_host)
        self._empty_host.setVisible(False)

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
        self._last_brokers = [card for card in cards if isinstance(card, dict)]
        self._callback_url = str(state.get("callback_url", "") or "")
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
                widget.login_requested.connect(self._handle_login)
                widget.refresh_requested.connect(self.refresh_requested.emit)
                widget.disconnect_requested.connect(self.disconnect_requested.emit)
                widget.remove_requested.connect(self.remove_requested.emit)
                self._cards_lay.addWidget(widget)
                self._cards[broker_id] = widget
            widget.set_callback_url(self._callback_url)
            widget.render_card(card)
        for stale in known - seen:
            widget = self._cards.pop(stale, None)
            if widget is not None:
                widget.setParent(None)
            dialog = self._connect_dialogs.pop(stale, None)
            if dialog is not None:
                with contextlib.suppress(Exception):
                    dialog.close()
        has_cards = bool(seen)
        self._cards_host.setVisible(has_cards)
        self._empty_host.setVisible(not has_cards)
        by_id = {str(c.get("id", "")): c for c in cards if isinstance(c, dict)}
        for broker_id, dialog in list(self._connect_dialogs.items()):
            card = by_id.get(broker_id)
            if isinstance(card, dict):
                with contextlib.suppress(Exception):
                    dialog.update_card(card)

    # ── actions ──────────────────────────────────────────────────

    def _handle_login(self, broker_id: str) -> None:
        self.login_requested.emit(broker_id)
        self._open_connect_dialog(broker_id)

    def _open_add_dialog(self) -> None:
        """Open the broker selector with snapshot (registry-backed) choices.

        The widget never touches the registry — the snapshot is the channel,
        and its entries already carry the registry display names. No broker
        literal is ever fabricated (design §3.2 rule 6); if the snapshot is
        empty the selector honestly offers nothing.
        """
        brokers = [
            {"id": str(card.get("id", "")), "name": str(card.get("name", ""))}
            for card in self._last_brokers
            if card.get("id")
        ]
        if not brokers:
            brokers = [{"id": bid, "name": card._display_name} for bid, card in self._cards.items()]
        dialog = _AddBrokerDialog(brokers, self)
        dialog.broker_chosen.connect(self._on_add_chosen)
        dialog.show()

    def _on_add_chosen(self, broker_id: str) -> None:
        card = self._cards.get(broker_id)
        if card is None:
            return
        state = getattr(card, "_last_status", "")
        if state == "NOT_CONFIGURED":
            with contextlib.suppress(Exception):
                card._open_configure()
        with contextlib.suppress(Exception):
            self._scroll.ensureWidgetVisible(card)

    def _open_connect_dialog(self, broker_id: str) -> None:
        existing = self._connect_dialogs.get(broker_id)
        if existing is not None:
            with contextlib.suppress(Exception):
                existing.show()
                existing.raise_()
            return
        card = self._cards.get(broker_id)
        display = card._display_name if card is not None else broker_id
        dialog = _ConnectDialog(broker_id, display, self)
        if card is not None and isinstance(getattr(card, "_last_card", None), dict):
            with contextlib.suppress(Exception):
                dialog.update_card(card._last_card)
        dialog.finished.connect(lambda _code, bid=broker_id: self._connect_dialogs.pop(bid, None))
        self._connect_dialogs[broker_id] = dialog
        dialog.show()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        side = max(12, (self.width() - _MAX_CONTENT_WIDTH) // 2)
        self._content_lay.setContentsMargins(side, 4, side, 4)


__all__ = ["BrokersWorkspace"]
