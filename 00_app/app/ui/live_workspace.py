"""LiveWorkspace — real execution-control workspace for the LIVE tab.

Pure view over injected state: never touches the bus, SQL, brokers, risk
engines or sessions. All data arrives via ``set_state(state)`` or the
``state_provider`` callable; all actions (arm/halt/mode) go out through
injected callbacks. Missing data renders as N/A / NOT CONFIGURED / empty
tables — never fabricated.

Layout is scroll-safe: the right rail lives in a scroll area so readiness,
strategy, position and risk panels never clip at short window heights.
Readiness gates render as structured Gate/Status/Reason rows (reasons are
visible text, not tooltips-only). Tables share the terminal conventions
from `app.ui.ui_kit` and skip rebuilds when content is unchanged.

State dict schema (every key optional; absent means unknown):
  mode: "PAPER" | "SANDBOX" | "LIVE"
  broker: {name, environment, connected, reason, capabilities, latency_ms,
           last_heartbeat}
  strategy: {id, version, status, mode, params, instrument, timeframe,
             live_supported, warmup, state} | None
  position: {symbol, side, quantity, avg_price, current_price, unrealized,
             realized, exposure, risk_utilization} | None (None also = FLAT
             only when explicitly marked flat via "flat": True)
  orders: [{order_id, strategy, symbol, side, quantity, type, price,
            status, time, broker}]
  fills: [{time, symbol, side, quantity, price, order_id, strategy, slippage}]
  pnl: {realized, unrealized, total, exposure, orders, fills, wins, losses}
  risk: {status: READY|WARNING|BLOCKED|HALTED,
         limits: [(name, value, state)], decisions: [(name, ok, reason)]}
  reconciliation: {status: CLEAN|MISMATCH|BLOCKED|NOT CONFIGURED,
                   positions, orders, last_check, mismatches, blocks_live}
  kill: {halted: bool, level: str}
  gates: [{name, status: READY|NOT READY|BLOCKED|NOT CONFIGURED, reason: str}]
  can_arm: bool, arm_blockers: [str], can_halt: bool
  lifecycle: str, events: [{timestamp, strategy, symbol, event, status}]
  market_symbol, market_timeframe: str, market_bars: tuple[Bar, ...] | None
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from chart.widgets.candle_chart_widget import CandleChartWidget
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
    GateRow,
    KVBlock,
    Section,
    configure_table,
    fill_table,
)
from app.ui.ui_kit import money as _money
from app.ui.ui_kit import text as _text

_REFRESH_MS = 1000
_NA = "N/A"

_STRATEGY_KEYS = (
    "id",
    "version",
    "status",
    "mode",
    "instrument",
    "timeframe",
    "live_supported",
    "warmup",
    "state",
)

_POSITION_KEYS = (
    "instrument",
    "side",
    "quantity",
    "avg_price",
    "current_price",
    "unrealized",
    "realized",
    "exposure",
    "risk_utilization",
)


class LiveWorkspace(QWidget):
    """Execution-control workspace. Backend remains the single source of truth."""

    mode_requested = Signal(str)
    arm_requested = Signal()
    halt_requested = Signal()

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = state_provider
        self._on_arm: Callable[[], tuple[bool, str]] | None = None
        self._on_halt: Callable[[], tuple[bool, str]] | None = None
        self._state: dict[str, Any] = {}
        self._gate_rows: list[tuple[str, Badge, QLabel]] = []
        self._gate_widgets: dict[str, GateRow] = {}
        self._event_known: set[str] = {"ALL EVENTS"}
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

        self._halt_banner = QLabel("■ EXECUTION HALTED", self)
        self._halt_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._halt_banner.setStyleSheet(
            f"background: #3A0E14; color: {t.NEG}; font-size: 13px; font-weight: 800;"
            f" border: 1px solid {t.NEG}; border-radius: 4px; padding: 6px;"
        )
        self._halt_banner.setVisible(False)
        root.addWidget(self._halt_banner)

        root.addWidget(self._build_status_bar())

        middle = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget(middle)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        left_lay.addWidget(self._build_chart_panel(), 3)
        tables = QSplitter(Qt.Orientation.Horizontal)
        tables.addWidget(self._build_orders_panel())
        tables.addWidget(self._build_fills_panel())
        tables.setStretchFactor(0, 1)
        tables.setStretchFactor(1, 1)
        left_lay.addWidget(tables, 2)
        middle.addWidget(left)

        rail_content = QWidget(middle)
        rail_lay = QVBoxLayout(rail_content)
        rail_lay.setContentsMargins(0, 0, 0, 0)
        rail_lay.setSpacing(6)
        rail_lay.addWidget(self._build_readiness_panel())
        rail_lay.addWidget(self._build_strategy_panel())
        rail_lay.addWidget(self._build_position_panel())
        rail_lay.addWidget(self._build_risk_panel())
        rail_lay.addStretch(1)
        self._rail_scroll = QScrollArea(middle)
        self._rail_scroll.setWidgetResizable(True)
        self._rail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._rail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rail_scroll.setWidget(rail_content)
        self._rail_scroll.setMinimumWidth(300)
        middle.addWidget(self._rail_scroll)
        # Proportional split (chart area gets ~3/4); no hardcoded pixels so
        # the layout survives 1280px through 2560px widths.
        middle.setStretchFactor(0, 3)
        middle.setStretchFactor(1, 1)
        root.addWidget(middle, 1)

        root.addWidget(self._build_bottom_strip())
        root.addWidget(self._build_event_stream(), 1)

    def _build_status_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("LiveStatusBar")
        bar.setStyleSheet(
            f"#LiveStatusBar {{ background: {t.PANEL}; border: 1px solid {t.BORDER};"
            f" border-radius: 4px; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(14)
        self._mode_pill = Badge(bar)
        lay.addWidget(self._mode_pill)
        self._broker_pill = Badge(bar)
        lay.addWidget(self._broker_pill)
        self._conn_pill = Badge(bar)
        lay.addWidget(self._conn_pill)
        self._strategy_pill = Badge(bar)
        lay.addWidget(self._strategy_pill)
        self._risk_pill = Badge(bar)
        lay.addWidget(self._risk_pill)
        self._recon_pill = Badge(bar)
        lay.addWidget(self._recon_pill)
        self._kill_pill = Badge(bar)
        lay.addWidget(self._kill_pill)
        lay.addStretch(1)
        mode_label = QLabel("Mode:", bar)
        mode_label.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(mode_label)
        self._mode_selector = QComboBox(bar)
        self._mode_selector.addItems(["PAPER", "SANDBOX", "LIVE"])
        self._mode_selector.setStyleSheet(t.INPUT_QSS)
        self._mode_selector.currentTextChanged.connect(self._on_mode_selected)
        lay.addWidget(self._mode_selector)
        self._halt_button = QPushButton("HALT EXECUTION", bar)
        self._halt_button.setStyleSheet(t.BUTTON_QSS)
        self._halt_button.clicked.connect(self._on_halt_clicked)
        self._halt_button.setEnabled(False)
        lay.addWidget(self._halt_button)
        return bar

    def _build_chart_panel(self) -> QWidget:
        section = Section("CHART")
        self._chart = CandleChartWidget(section)
        self._chart_empty = EmptyState("NO DATA", parent=section)
        self._chart_empty.setVisible(False)
        section.add(self._chart)
        section.add(self._chart_empty)
        self._price_label = QLabel("", section)
        self._price_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._price_label)
        return section

    def _build_readiness_panel(self) -> QWidget:
        section = Section("LIVE READINESS")
        self._gate_box = QWidget(section)
        self._gate_lay = QVBoxLayout(self._gate_box)
        self._gate_lay.setContentsMargins(0, 0, 0, 0)
        self._gate_lay.setSpacing(4)
        section.add(self._gate_box)
        self._arm_button = QPushButton("ARM LIVE", section)
        self._arm_button.setStyleSheet(t.BUTTON_QSS)
        self._arm_button.clicked.connect(self._on_arm_clicked)
        self._arm_button.setEnabled(False)
        section.add(self._arm_button)
        self._arm_note = QLabel("", section)
        self._arm_note.setWordWrap(True)
        self._arm_note.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        section.add(self._arm_note)
        return section

    def _build_strategy_panel(self) -> QWidget:
        section = Section("ACTIVE STRATEGY")
        self._strategy_block = KVBlock(_STRATEGY_KEYS, section)
        section.add(self._strategy_block)
        self._strategy_rows = {key: self._strategy_block.value(key) for key in _STRATEGY_KEYS}
        self._strategy_params = QLabel("", section)
        self._strategy_params.setWordWrap(True)
        self._strategy_params.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._strategy_params)
        return section

    def _build_position_panel(self) -> QWidget:
        section = Section("POSITION")
        self._position_block = KVBlock(_POSITION_KEYS, section)
        section.add(self._position_block)
        self._position_rows = {key: self._position_block.value(key) for key in _POSITION_KEYS}
        return section

    def _build_risk_panel(self) -> QWidget:
        section = Section("RISK")
        self._risk_status = Badge(section)
        section.add(self._risk_status)
        self._risk_limits = QLabel("", section)
        self._risk_limits.setWordWrap(True)
        self._risk_limits.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._risk_limits)
        return section

    def _make_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        configure_table(table)
        return table

    def _build_orders_panel(self) -> QWidget:
        section = Section("OPEN ORDERS")
        self._orders_table = self._make_table(
            [
                "Order ID",
                "Strategy",
                "Symbol",
                "Side",
                "Qty",
                "Type",
                "Price",
                "Status",
                "Time",
                "Broker",
            ]
        )
        section.add(self._orders_table)
        return section

    def _build_fills_panel(self) -> QWidget:
        section = Section("RECENT FILLS")
        self._fills_table = self._make_table(
            ["Time", "Symbol", "Side", "Qty", "Price", "Order ID", "Strategy", "Slippage"]
        )
        section.add(self._fills_table)
        return section

    def _build_bottom_strip(self) -> QWidget:
        strip = QWidget(self)
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._pnl_section = Section("P&L")
        self._pnl_label = QLabel("", self._pnl_section)
        self._pnl_label.setStyleSheet(f"color: {t.TEXT}; font-size: 11px;")
        self._pnl_section.add(self._pnl_label)
        lay.addWidget(self._pnl_section)
        self._broker_section = Section("BROKER")
        self._broker_label = QLabel("", self._broker_section)
        self._broker_label.setWordWrap(True)
        self._broker_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        self._broker_section.add(self._broker_label)
        lay.addWidget(self._broker_section)
        self._recon_section = Section("RECONCILIATION")
        self._recon_label = QLabel("", self._recon_section)
        self._recon_label.setWordWrap(True)
        self._recon_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        self._recon_section.add(self._recon_label)
        lay.addWidget(self._recon_section)
        return strip

    def _build_event_stream(self) -> QWidget:
        section = Section("LIVE EVENTS")
        controls = QWidget(section)
        c_lay = QHBoxLayout(controls)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(6)
        self._event_type_filter = QComboBox(controls)
        self._event_type_filter.addItem("ALL EVENTS")
        self._event_type_filter.setStyleSheet(t.INPUT_QSS)
        self._event_type_filter.currentTextChanged.connect(lambda _t: self._render_events())
        c_lay.addWidget(self._event_type_filter)
        self._event_text_filter = QLineEdit(controls)
        self._event_text_filter.setPlaceholderText("Filter: strategy, symbol, status…")
        self._event_text_filter.setStyleSheet(t.INPUT_QSS)
        self._event_text_filter.textChanged.connect(lambda _t: self._render_events())
        c_lay.addWidget(self._event_text_filter, 1)
        section.add(controls)
        self._events_table = self._make_table(
            ["Timestamp", "Strategy", "Symbol", "Event", "Status"]
        )
        section.add(self._events_table)
        return section

    # ── state input ───────────────────────────────────────────

    def set_state(self, state: dict[str, Any]) -> None:
        """Replace the displayed snapshot. Missing keys render as N/A."""
        self._state = dict(state) if isinstance(state, dict) else {}
        self._render_all()

    def set_state_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        self._provider = provider

    def set_arm_handler(self, handler: Callable[[], tuple[bool, str]] | None) -> None:
        self._on_arm = handler

    def set_halt_handler(self, handler: Callable[[], tuple[bool, str]] | None) -> None:
        self._on_halt = handler

    def refresh(self) -> None:
        """Pull from the provider (if any) and re-render. Never raises."""
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

    def _on_mode_selected(self, mode: str) -> None:
        self.mode_requested.emit(mode)
        self.refresh()

    def _on_arm_clicked(self) -> None:
        self.arm_requested.emit()
        if self._on_arm is None:
            self._arm_note.setText("No arming backend attached.")
            return
        try:
            _, message = self._on_arm()
        except Exception as exc:
            message = str(exc)
        self.refresh()
        self._arm_note.setText(message)

    def _on_halt_clicked(self) -> None:
        self.halt_requested.emit()
        if self._on_halt is None:
            return
        with contextlib.suppress(Exception):
            self._on_halt()
        self.refresh()

    # ── rendering (pure view of self._state) ──────────────────

    def _render_all(self) -> None:
        state = self._state
        self._render_status_bar(state)
        self._render_chart(state)
        self._render_gates(state)
        self._render_strategy(state)
        self._render_position(state)
        self._render_risk(state)
        self._render_orders(state)
        self._render_fills(state)
        self._render_bottom(state)
        self._render_events()

    def _render_status_bar(self, state: dict[str, Any]) -> None:
        mode = str(state.get("mode", _NA))
        tone = {"PAPER": "accent", "SANDBOX": "warn", "LIVE": "bad"}.get(mode, "muted")
        self._mode_pill.set_status(mode, tone)
        broker = state.get("broker", {})
        if not isinstance(broker, dict):
            broker = {}
        name = broker.get("name") or "NOT CONFIGURED"
        self._broker_pill.set_status(
            f"Broker: {name}", "muted" if name in ("NOT CONFIGURED", _NA) else "accent"
        )
        connected = broker.get("connected")
        if connected is True:
            self._conn_pill.set_status("● Connected", "ok")
        elif connected is False:
            self._conn_pill.set_status("○ Disconnected", "bad")
        else:
            self._conn_pill.set_status("Connection: N/A", "muted")
        strategy = state.get("strategy") or {}
        sid = strategy.get("id") if isinstance(strategy, dict) else None
        self._strategy_pill.set_status(f"Strategy: {sid or '—'}", "accent" if sid else "muted")
        risk = state.get("risk") or {}
        rstatus = str(risk.get("status", _NA)) if isinstance(risk, dict) else _NA
        self._risk_pill.set_status(
            f"Risk: {rstatus}",
            {"READY": "ok", "WARNING": "warn", "BLOCKED": "bad", "HALTED": "bad"}.get(
                rstatus, "muted"
            ),
        )
        recon = state.get("reconciliation") or {}
        rstate = str(recon.get("status", _NA)) if isinstance(recon, dict) else _NA
        self._recon_pill.set_status(
            f"Reconciliation: {rstate}",
            {"CLEAN": "ok", "MISMATCH": "bad", "BLOCKED": "bad"}.get(rstate, "muted"),
        )
        kill = state.get("kill") or {}
        halted = bool(kill.get("halted")) if isinstance(kill, dict) else False
        self._kill_pill.set_status("■ HALTED" if halted else "● ENABLED", "bad" if halted else "ok")
        self._halt_banner.setVisible(halted)
        self._halt_button.setEnabled(bool(state.get("can_halt", False)))

    def _render_chart(self, state: dict[str, Any]) -> None:
        bars = state.get("market_bars")
        symbol = str(state.get("market_symbol", ""))
        timeframe = str(state.get("market_timeframe", ""))
        has_bars = bool(bars)
        self._chart.setVisible(has_bars)
        self._chart_empty.setVisible(not has_bars)
        if has_bars:
            try:
                from chart.models.chart_model import ChartModel

                self._chart.set_model(
                    ChartModel(symbol=symbol, bars=tuple(bars), timeframe=timeframe, exchange="NSE")
                )
            except Exception:
                self._chart.setVisible(False)
                self._chart_empty.setVisible(True)
                self._chart_empty.setText("CHART ERROR — model rejected")
                return
        else:
            detail = "  ·  ".join(
                part
                for part in (
                    f"Symbol: {symbol or '—'}",
                    f"Timeframe: {timeframe or '—'}",
                    "Source: market store (SQLite)",
                    "Next: select a symbol with downloaded history",
                )
                if part
            )
            self._chart_empty.set_detail(detail)
        price = None
        position = state.get("position") or {}
        if isinstance(position, dict):
            price = position.get("current_price")
        header = f"{symbol} {timeframe}".strip()
        if price is not None:
            header += f"  ·  {_money(price)}"
        self._price_label.setText(header)

    def _render_gates(self, state: dict[str, Any]) -> None:
        gates = state.get("gates") or []
        if not isinstance(gates, list):
            gates = []
        seen: set[str] = set()
        self._gate_rows = []
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            name = str(gate.get("name", "?"))
            status = str(gate.get("status", _NA))
            reason = str(gate.get("reason", ""))
            seen.add(name)
            row = self._gate_widgets.get(name)
            if row is None:
                row = GateRow(name, self._gate_box)
                self._gate_widgets[name] = row
                self._gate_lay.addWidget(row)
            row.render_gate(status, reason)
            self._gate_rows.append((name, row.pill, row.name_label))
        for stale in [name for name in self._gate_widgets if name not in seen]:
            widget = self._gate_widgets.pop(stale)
            self._gate_lay.removeWidget(widget)
            widget.deleteLater()
        can_arm = bool(state.get("can_arm", False))
        self._arm_button.setEnabled(can_arm)
        blockers = state.get("arm_blockers") or []
        if not isinstance(blockers, list):
            blockers = []
        if can_arm:
            self._arm_note.setText("All requirements pass — arming is available.")
        elif blockers:
            self._arm_note.setText("Blocked: " + "; ".join(str(b) for b in blockers))
        else:
            self._arm_note.setText("")

    def _render_strategy(self, state: dict[str, Any]) -> None:
        strategy = state.get("strategy")
        if not isinstance(strategy, dict):
            self._strategy_block.set_all_na()
            self._strategy_params.setText("")
            return
        mapping = {
            "id": strategy.get("id"),
            "version": strategy.get("version"),
            "status": strategy.get("status"),
            "mode": strategy.get("mode"),
            "instrument": strategy.get("instrument"),
            "timeframe": strategy.get("timeframe"),
            "live_supported": strategy.get("live_supported"),
            "warmup": strategy.get("warmup"),
            "state": strategy.get("state"),
        }
        for key, value in mapping.items():
            if key == "live_supported":
                text = "YES" if value is True else ("NO" if value is False else _NA)
            else:
                text = _text(value)
            self._strategy_block.set(key, text)
        params = strategy.get("params")
        if isinstance(params, dict) and params:
            self._strategy_params.setText(
                "Params: " + ", ".join(f"{k}={v}" for k, v in params.items())
            )
        else:
            self._strategy_params.setText("")

    def _render_position(self, state: dict[str, Any]) -> None:
        position = state.get("position")
        if not isinstance(position, dict) or position.get("flat", False):
            for key in _POSITION_KEYS:
                if key == "side":
                    self._position_block.set(key, "FLAT")
                elif key == "instrument":
                    self._position_block.set(key, "—")
                else:
                    self._position_block.set(key, "0")
            if not isinstance(position, dict):
                self._position_block.set_all_na()
            return
        mapping = {
            "instrument": position.get("symbol", position.get("instrument")),
            "side": position.get("side"),
            "quantity": position.get("quantity"),
            "avg_price": position.get("avg_price"),
            "current_price": position.get("current_price"),
            "unrealized": position.get("unrealized"),
            "realized": position.get("realized"),
            "exposure": position.get("exposure"),
            "risk_utilization": position.get("risk_utilization"),
        }
        for key, value in mapping.items():
            if key in ("unrealized", "realized"):
                self._position_block.set(key, _money(value))
            else:
                self._position_block.set(key, _text(value))

    def _render_risk(self, state: dict[str, Any]) -> None:
        risk = state.get("risk")
        if not isinstance(risk, dict):
            self._risk_status.set_status(_NA, "muted")
            self._risk_limits.setText("")
            return
        status = str(risk.get("status", _NA))
        self._risk_status.set_status(
            status,
            {"READY": "ok", "WARNING": "warn", "BLOCKED": "bad", "HALTED": "bad"}.get(
                status, "muted"
            ),
        )
        lines: list[str] = []
        limits = risk.get("limits") or []
        if isinstance(limits, list):
            for entry in limits:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    lines.append(f"{entry[0]}: {entry[1]}")
                else:
                    lines.append(str(entry))
        decisions = risk.get("decisions") or []
        if isinstance(decisions, list):
            for entry in decisions:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    mark = "ok" if entry[1] else "FAIL"
                    reason = f" — {entry[2]}" if len(entry) > 2 and entry[2] else ""
                    lines.append(f"[{mark}] {entry[0]}{reason}")
        self._risk_limits.setText("\n".join(lines))

    def _render_orders(self, state: dict[str, Any]) -> None:
        orders = state.get("orders") or []
        rows: list[list[str]] = []
        if isinstance(orders, list):
            for order in orders:
                if not isinstance(order, dict):
                    continue
                rows.append(
                    [
                        _text(order.get("order_id"), ""),
                        _text(order.get("strategy"), ""),
                        _text(order.get("symbol"), ""),
                        _text(order.get("side"), ""),
                        _text(order.get("quantity"), ""),
                        _text(order.get("type"), ""),
                        _text(order.get("price"), ""),
                        _text(order.get("status"), ""),
                        _text(order.get("time"), ""),
                        _text(order.get("broker"), ""),
                    ]
                )
        fill_table(self._orders_table, rows)

    def _render_fills(self, state: dict[str, Any]) -> None:
        fills = state.get("fills") or []
        rows: list[list[str]] = []
        if isinstance(fills, list):
            for fill in fills:
                if not isinstance(fill, dict):
                    continue
                rows.append(
                    [
                        _text(fill.get("time"), ""),
                        _text(fill.get("symbol"), ""),
                        _text(fill.get("side"), ""),
                        _text(fill.get("quantity"), ""),
                        _text(fill.get("price"), ""),
                        _text(fill.get("order_id"), ""),
                        _text(fill.get("strategy"), ""),
                        _text(fill.get("slippage"), ""),
                    ]
                )
        fill_table(self._fills_table, rows)

    def _render_bottom(self, state: dict[str, Any]) -> None:
        pnl = state.get("pnl") or {}
        if isinstance(pnl, dict):
            self._pnl_label.setText(
                f"Realized {_money(pnl.get('realized'))}  ·  "
                f"Unrealized {_money(pnl.get('unrealized'))}  ·  "
                f"Total {_money(pnl.get('total'))}\n"
                f"Exposure {_text(pnl.get('exposure'))}  ·  "
                f"Orders {_text(pnl.get('orders'))}  ·  "
                f"Fills {_text(pnl.get('fills'))}  ·  "
                f"W/L {_text(pnl.get('wins'))}/{_text(pnl.get('losses'))}"
            )
        else:
            self._pnl_label.setText(_NA)
        broker = state.get("broker") or {}
        if isinstance(broker, dict):
            caps = broker.get("capabilities")
            caps_text = ", ".join(str(c) for c in caps) if isinstance(caps, list) else _NA
            self._broker_label.setText(
                f"{_text(broker.get('name'))}  ·  {_text(broker.get('environment'))}\n"
                f"Latency {_text(broker.get('latency_ms'))}  ·  "
                f"Heartbeat {_text(broker.get('last_heartbeat'))}\n"
                f"Capabilities: {caps_text}"
            )
        else:
            self._broker_label.setText(_NA)
        recon = state.get("reconciliation") or {}
        if isinstance(recon, dict):
            self._recon_label.setText(
                f"{_text(recon.get('status'))}  ·  "
                f"Mismatches {_text(recon.get('mismatches'))}  ·  "
                f"blocks_live {_text(recon.get('blocks_live'))}\n"
                f"Positions {_text(recon.get('positions'))}  ·  "
                f"Orders {_text(recon.get('orders'))}\n"
                f"Last check {_text(recon.get('last_check'))}"
            )
        else:
            self._recon_label.setText(_NA)

    def _render_events(self) -> None:
        events = self._state.get("events") or []
        type_filter = self._event_type_filter.currentText()
        text_filter = self._event_text_filter.text().strip().lower()
        known = {"ALL EVENTS"}
        rows: list[list[str]] = []
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                etype = str(event.get("event", ""))
                known.add(etype)
                if type_filter != "ALL EVENTS" and etype != type_filter:
                    continue
                haystack = " ".join(
                    str(event.get(k, "")) for k in ("strategy", "symbol", "event", "status")
                ).lower()
                if text_filter and text_filter not in haystack:
                    continue
                rows.append(
                    [
                        _text(event.get("timestamp"), ""),
                        _text(event.get("strategy"), ""),
                        _text(event.get("symbol"), ""),
                        etype,
                        _text(event.get("status"), ""),
                    ]
                )
        if known != self._event_known:
            self._event_known = known
            current = self._event_type_filter.currentText()
            self._event_type_filter.blockSignals(True)
            self._event_type_filter.clear()
            self._event_type_filter.addItems(sorted(known))
            if current in known:
                self._event_type_filter.setCurrentText(current)
            self._event_type_filter.blockSignals(False)
        fill_table(self._events_table, rows)
