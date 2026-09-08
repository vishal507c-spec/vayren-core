"""PortfolioWorkspace — professional portfolio workstation (real state only).

Pure view over the shared workspace-state dict (the same schema the LIVE
workspace consumes, so no state is duplicated): account/funds, positions
with allocation, exposure, P&L, orders, fills, performance, risk and
reconciliation. Allocation percentages are derived display math
(position exposure / equity) computed transparently from state values;
anything absent renders as an explicit unavailable state — never zero,
never fabricated.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t
from app.ui.ui_kit import (
    Badge,
    EmptyState,
    KVBlock,
    Section,
    configure_table,
    fill_table,
)
from app.ui.ui_kit import money as _money
from app.ui.ui_kit import text as _text

_REFRESH_MS = 1000
_NA = "N/A"

_ACCOUNT_KEYS = ("account", "environment", "currency", "equity", "available", "used")
_PERF_KEYS = ("trades", "wins", "losses", "win_rate", "realized", "unrealized", "total")


def _equity_of(state: dict[str, Any]) -> float | None:
    """Equity basis for allocation math: genuine equity fields only.

    P&L totals are flows, not equity — using them as a basis fabricates
    percentages (e.g. 1100%). No basis → None → allocation shows N/A.
    """
    funds = state.get("funds")
    if isinstance(funds, dict):
        for key in ("equity", "available"):
            try:
                value = funds.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
    return None


class PortfolioWorkspace(QWidget):
    """Account + positions + performance workstation. Backend is the truth."""

    refresh_requested = Signal()

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = state_provider
        self._state: dict[str, Any] = {}
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # ── construction ──────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QWidget(self)
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(6)
        account = Section("ACCOUNT / FUNDS")
        self._account_block = KVBlock(_ACCOUNT_KEYS, account)
        account.add(self._account_block)
        self._funds_note = QLabel("", account)
        self._funds_note.setWordWrap(True)
        self._funds_note.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        account.add(self._funds_note)
        top_lay.addWidget(account)
        perf = Section("PERFORMANCE")
        self._perf_block = KVBlock(_PERF_KEYS, perf)
        perf.add(self._perf_block)
        top_lay.addWidget(perf)
        status = Section("STATUS")
        self._status_badges = Badge(status)
        status.add(self._status_badges)
        self._status_detail = QLabel("", status)
        self._status_detail.setWordWrap(True)
        self._status_detail.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        status.add(self._status_detail)
        top_lay.addWidget(status)
        root.addWidget(top)

        middle = QSplitter(Qt.Orientation.Horizontal, self)
        positions = Section("POSITIONS & ALLOCATION")
        self._positions_table = QTableWidget(0, 5)
        self._positions_table.setHorizontalHeaderLabels(
            ["Symbol", "Side", "Qty", "Exposure", "Alloc %"]
        )
        configure_table(self._positions_table)
        positions.add(self._positions_table)
        self._positions_empty = EmptyState("NO POSITIONS", "Portfolio is flat.", positions)
        positions.add(self._positions_empty)
        middle.addWidget(positions)
        orders = Section("ORDERS & FILLS")
        self._orders_table = QTableWidget(0, 5)
        self._orders_table.setHorizontalHeaderLabels(
            ["Order ID", "Symbol", "Side", "Qty", "Status"]
        )
        configure_table(self._orders_table)
        orders.add(self._orders_table)
        self._fills_table = QTableWidget(0, 4)
        self._fills_table.setHorizontalHeaderLabels(["Time", "Symbol", "Qty", "Price"])
        configure_table(self._fills_table)
        orders.add(self._fills_table)
        middle.addWidget(orders)
        middle.setStretchFactor(0, 3)
        middle.setStretchFactor(1, 2)
        scroll_host = QScrollArea(self)
        scroll_host.setWidgetResizable(True)
        scroll_host.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_host.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_host.setWidget(middle)
        scroll_host.setMinimumHeight(240)
        root.addWidget(scroll_host, 1)

    # ── state input ───────────────────────────────────────────

    def set_state(self, state: dict[str, Any]) -> None:
        self._state = dict(state) if isinstance(state, dict) else {}
        self._render_all()

    def set_state_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        self._provider = provider

    def refresh(self) -> None:
        with contextlib.suppress(Exception):
            if self._provider is not None:
                state = self._provider()
                if isinstance(state, dict):
                    self._state = state
            self._render_all()

    def showEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        super().hideEvent(event)

    # ── rendering ─────────────────────────────────────────────

    def _render_all(self) -> None:
        state = self._state
        self._render_account(state)
        self._render_performance(state)
        self._render_status(state)
        self._render_positions(state)
        self._render_orders_fills(state)

    def _render_account(self, state: dict[str, Any]) -> None:
        funds = state.get("funds")
        broker = state.get("broker") or {}
        if not isinstance(broker, dict):
            broker = {}
        if isinstance(funds, dict):
            for key in ("account", "environment", "currency", "equity", "available", "used"):
                self._account_block.set(key, _text(funds.get(key)))
            self._funds_note.setText("")
            return
        # No funds snapshot in state: show identity honestly, never zeros.
        self._account_block.set("account", _text(broker.get("name")))
        self._account_block.set("environment", _text(broker.get("environment")))
        for key in ("currency", "equity", "available", "used"):
            self._account_block.set(key, "UNAVAILABLE")
        self._funds_note.setText("Funds detail unavailable — no account snapshot in state.")

    def _render_performance(self, state: dict[str, Any]) -> None:
        pnl = state.get("pnl") or {}
        if not isinstance(pnl, dict):
            self._perf_block.set_all_na()
            return
        wins = pnl.get("wins")
        losses = pnl.get("losses")
        trades = None
        try:
            if wins is not None and losses is not None:
                trades = int(wins) + int(losses)
        except (TypeError, ValueError):
            trades = None
        win_rate = "N/A"
        try:
            if wins is not None and trades:
                win_rate = f"{100.0 * float(wins) / float(trades):.1f}%"
        except (TypeError, ValueError, ZeroDivisionError):
            win_rate = "N/A"
        self._perf_block.set("trades", _text(trades))
        self._perf_block.set("wins", _text(wins))
        self._perf_block.set("losses", _text(losses))
        self._perf_block.set("win_rate", win_rate)
        self._perf_block.set("realized", _money(pnl.get("realized")))
        self._perf_block.set("unrealized", _money(pnl.get("unrealized")))
        self._perf_block.set("total", _money(pnl.get("total")))

    def _render_status(self, state: dict[str, Any]) -> None:
        risk = state.get("risk") or {}
        recon = state.get("reconciliation") or {}
        rstatus = str(risk.get("status", _NA)) if isinstance(risk, dict) else _NA
        rstate = str(recon.get("status", _NA)) if isinstance(recon, dict) else _NA
        if rstatus in ("BLOCKED", "HALTED") or rstate in ("MISMATCH", "BLOCKED"):
            self._status_badges.set_status("BLOCKED", "bad")
        elif rstatus == "WARNING":
            self._status_badges.set_status("WARNING", "warn")
        elif rstatus == "READY" and rstate == "CLEAN":
            self._status_badges.set_status("READY", "ok")
        else:
            self._status_badges.set_status(f"{rstatus} / {rstate}", "muted")
        kill = state.get("kill") or {}
        halted = bool(kill.get("halted")) if isinstance(kill, dict) else False
        mode = str(state.get("mode", _NA))
        self._status_detail.setText(
            f"Mode {mode}  ·  Halted {'YES' if halted else 'NO'}  ·  "
            f"Lifecycle {_text(state.get('lifecycle'))}"
        )

    def _positions_from_state(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        position = state.get("position")
        if isinstance(position, dict) and not position.get("flat", False):
            symbol = position.get("symbol", position.get("instrument", ""))
            return [
                {
                    "symbol": symbol,
                    "side": position.get("side", ""),
                    "quantity": position.get("quantity", 0),
                    "exposure": position.get("exposure", 0),
                }
            ]
        positions = state.get("positions")
        if isinstance(positions, list):
            return [p for p in positions if isinstance(p, dict)]
        return []

    def _render_positions(self, state: dict[str, Any]) -> None:
        rows_data = self._positions_from_state(state)
        equity = _equity_of(state)
        rows: list[list[str]] = []
        for entry in rows_data:
            try:
                exposure = float(entry.get("exposure", 0) or 0)
            except (TypeError, ValueError):
                exposure = 0.0
            alloc = "N/A"
            if equity:
                try:
                    alloc = f"{100.0 * exposure / equity:.1f}%"
                except (TypeError, ValueError, ZeroDivisionError):
                    alloc = "N/A"
            rows.append(
                [
                    _text(entry.get("symbol"), ""),
                    _text(entry.get("side"), ""),
                    _text(entry.get("quantity"), ""),
                    _text(entry.get("exposure"), ""),
                    alloc,
                ]
            )
        fill_table(self._positions_table, rows)
        self._positions_empty.setVisible(not rows)

    def _render_orders_fills(self, state: dict[str, Any]) -> None:
        orders = state.get("orders") or []
        order_rows: list[list[str]] = []
        if isinstance(orders, list):
            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_rows.append(
                    [
                        _text(order.get("order_id"), ""),
                        _text(order.get("symbol"), ""),
                        _text(order.get("side"), ""),
                        _text(order.get("quantity"), ""),
                        _text(order.get("status"), ""),
                    ]
                )
        fill_table(self._orders_table, order_rows)
        fills = state.get("fills") or []
        fill_rows: list[list[str]] = []
        if isinstance(fills, list):
            for fill in fills:
                if not isinstance(fill, dict):
                    continue
                fill_rows.append(
                    [
                        _text(fill.get("time"), ""),
                        _text(fill.get("symbol"), ""),
                        _text(fill.get("quantity"), ""),
                        _text(fill.get("price"), ""),
                    ]
                )
        fill_table(self._fills_table, fill_rows)
