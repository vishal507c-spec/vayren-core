"""PortfolioWorkspace — institutional portfolio workstation (real state only).

Pure view over the shared workspace-state dict (the same schema the LIVE
workspace consumes, so no state is duplicated): account/funds, positions
with allocation, exposure, P&L, orders, fills, performance, risk and
reconciliation. Allocation percentages are derived display math
(position exposure / equity) computed transparently from state values;
anything absent renders as an explicit unavailable state — never zero,
never fabricated.

Visual flow: ACCOUNT → PORTFOLIO VALUE → P&L → POSITIONS → ORDERS → RISK.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
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
    configure_table,
    fill_table,
)
from app.ui.ui_kit import money as _money
from app.ui.ui_kit import text as _text

_REFRESH_MS = 1000
_NA = "N/A"


def _equity_of(state: dict[str, Any]) -> float | None:
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


def _fmt_equity(value: Any) -> str:
    if value is None:
        return _NA
    try:
        v = float(value)
        if v >= 1_00_000:
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_pct(value: Any) -> str:
    if value is None:
        return _NA
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return str(value)


class _KpiCard(QWidget):
    def __init__(
        self,
        label_text: str,
        *,
        primary: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_primary = primary
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SP_LG, t.SP_MD, t.SP_LG, t.SP_MD)
        layout.setSpacing(t.SP_XS)
        self._label = QLabel(label_text, self)
        self._label.setStyleSheet(t.label(size=t.FS_LABEL))
        layout.addWidget(self._label)
        self._value = QLabel(_NA, self)
        sz = t.FS_HERO if primary else t.FS_METRIC
        wt = 800 if primary else 700
        self._value.setStyleSheet(t.metric(size=sz, weight=wt))
        layout.addWidget(self._value)
        self.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(text)
        if color is not None:
            self._value.setStyleSheet(
                t.metric(
                    size=t.FS_HERO if self._is_primary else t.FS_METRIC,
                    color=color,
                    weight=800 if self._is_primary else 700,
                )
            )


class _StatusIndicator(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.SP_MD)
        self._dot = QLabel("●", self)
        self._dot.setStyleSheet(f"font-size: {t.FS_METRIC}px;")
        layout.addWidget(self._dot)
        self._text = QLabel("", self)
        self._text.setStyleSheet(t.body(weight=700))
        layout.addWidget(self._text, 1)

    def set_state(self, label: str, tone: str) -> None:
        self._text.setText(label)
        colors = {
            "ok": t.POS,
            "warn": t.WARN,
            "bad": t.NEG,
            "muted": t.MUTED,
        }
        color = colors.get(tone, t.MUTED)
        self._dot.setStyleSheet(f"color: {color}; font-size: {t.FS_METRIC}px;")
        self._text.setStyleSheet(t.body(color=color, weight=700))


class PortfolioWorkspace(QWidget):
    """Institutional portfolio workstation. Backend state is the truth."""

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

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SP_XL, t.SP_XL, t.SP_XL, t.SP_XL)
        root.setSpacing(t.SP_LG)
        self._build_header(root)
        self._build_account_status(root)
        self._build_kpi_strip(root)
        self._build_middle(root)
        self._build_positions(root)
        self._build_alloc_detail(root)
        self._build_orders(root)
        self._build_risk(root)

    def _build_header(self, root: QVBoxLayout) -> None:
        header = QWidget(self)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        title_area = QWidget(header)
        title_lay = QVBoxLayout(title_area)
        title_lay.setContentsMargins(0, 0, 0, 0)
        title_lay.setSpacing(t.SP_XS)
        title = QLabel("PORTFOLIO", title_area)
        title.setStyleSheet(
            f"color: {t.TEXT}; font-size: {t.FS_DISPLAY}px; font-weight: 800;"
            " letter-spacing: 1.5px;"
        )
        title_lay.addWidget(title)
        subtitle = QLabel("Account, positions, performance and risk", title_area)
        subtitle.setStyleSheet(t.label(size=t.FS_SMALL, text_color=t.TEXT2))
        title_lay.addWidget(subtitle)
        header_lay.addWidget(title_area, 1)
        self._updated_label = QLabel("", header)
        self._updated_label.setStyleSheet(t.label(size=t.FS_SMALL))
        header_lay.addWidget(self._updated_label)
        self._refresh_btn = QPushButton("Refresh", header)
        self._refresh_btn.setStyleSheet(t.BUTTON_QSS)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        header_lay.addWidget(self._refresh_btn)
        root.addWidget(header)

    def _build_account_status(self, root: QVBoxLayout) -> None:
        panel = QFrame(self)
        panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(t.SP_XL, t.SP_LG, t.SP_XL, t.SP_LG)
        layout.setSpacing(t.SP_XL)
        self._status_indicator = _StatusIndicator(panel)
        layout.addWidget(self._status_indicator)
        sep = QFrame(panel)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {t.BORDER};")
        layout.addWidget(sep)
        info_area = QWidget(panel)
        info_lay = QVBoxLayout(info_area)
        info_lay.setContentsMargins(0, 0, 0, 0)
        info_lay.setSpacing(t.SP_XS)
        self._account_info_label = QLabel("", info_area)
        self._account_info_label.setStyleSheet(t.body(size=t.FS_BODY, color=t.TEXT2))
        self._account_info_label.setWordWrap(True)
        info_lay.addWidget(self._account_info_label)
        self._account_detail_label = QLabel("", info_area)
        self._account_detail_label.setStyleSheet(t.label(size=t.FS_SMALL))
        self._account_detail_label.setWordWrap(True)
        info_lay.addWidget(self._account_detail_label)
        layout.addWidget(info_area, 1)
        root.addWidget(panel)

    def _build_kpi_strip(self, root: QVBoxLayout) -> None:
        strip = QWidget(self)
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(0, 0, 0, 0)
        strip_lay.setSpacing(t.SP_MD)
        self._kpi_equity = _KpiCard("TOTAL EQUITY", primary=True, parent=strip)
        self._kpi_available = _KpiCard("AVAILABLE", parent=strip)
        self._kpi_invested = _KpiCard("INVESTED", parent=strip)
        self._kpi_total_pnl = _KpiCard("TOTAL P&L", parent=strip)
        strip_lay.addWidget(self._kpi_equity, 2)
        strip_lay.addWidget(self._kpi_available, 1)
        strip_lay.addWidget(self._kpi_invested, 1)
        strip_lay.addWidget(self._kpi_total_pnl, 1)
        root.addWidget(strip)
        strip2 = QWidget(self)
        strip2_lay = QHBoxLayout(strip2)
        strip2_lay.setContentsMargins(0, 0, 0, 0)
        strip2_lay.setSpacing(t.SP_MD)
        self._kpi_today_pnl = _KpiCard("TODAY P&L", parent=strip2)
        self._kpi_unrealized = _KpiCard("UNREALIZED", parent=strip2)
        self._kpi_realized = _KpiCard("REALIZED", parent=strip2)
        self._kpi_return = _KpiCard("RETURN", parent=strip2)
        strip2_lay.addWidget(self._kpi_today_pnl, 1)
        strip2_lay.addWidget(self._kpi_unrealized, 1)
        strip2_lay.addWidget(self._kpi_realized, 1)
        strip2_lay.addWidget(self._kpi_return, 1)
        root.addWidget(strip2)

    def _build_middle(self, root: QVBoxLayout) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setStyleSheet(t.SPLITTER_QSS)
        splitter.setHandleWidth(1)
        perf_panel = QFrame(splitter)
        perf_panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        perf_lay = QVBoxLayout(perf_panel)
        perf_lay.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        perf_lay.setSpacing(t.SP_MD)
        perf_title = QLabel("PORTFOLIO PERFORMANCE", perf_panel)
        perf_title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        perf_lay.addWidget(perf_title)
        self._perf_empty = EmptyState(
            "NO PERFORMANCE DATA",
            "Connect an account or load portfolio data to view performance.",
            perf_panel,
        )
        perf_lay.addWidget(self._perf_empty, 1)
        self._perf_summary = QWidget(perf_panel)
        perf_sum_lay = QVBoxLayout(self._perf_summary)
        perf_sum_lay.setContentsMargins(0, 0, 0, 0)
        perf_sum_lay.setSpacing(t.SP_SM)
        self._perf_rows: list[tuple[QLabel, QLabel]] = []
        for label_text in ("Trades", "Wins", "Losses", "Win Rate"):
            row = QWidget(self._perf_summary)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label_text, row)
            lbl.setStyleSheet(t.label(size=t.FS_SMALL))
            val = QLabel(_NA, row)
            val.setStyleSheet(t.body(size=t.FS_BODY))
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_lay.addWidget(lbl, 1)
            row_lay.addWidget(val)
            perf_sum_lay.addWidget(row)
            self._perf_rows.append((lbl, val))
        perf_lay.addWidget(self._perf_summary)
        self._perf_summary.setVisible(False)
        splitter.addWidget(perf_panel)
        status_panel = QFrame(splitter)
        status_panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        status_lay = QVBoxLayout(status_panel)
        status_lay.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        status_lay.setSpacing(t.SP_MD)
        status_title = QLabel("PORTFOLIO STATUS", status_panel)
        status_title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        status_lay.addWidget(status_title)
        self._gate_trading = GateRow("Trading", status_panel)
        status_lay.addWidget(self._gate_trading)
        self._gate_market_data = GateRow("Market Data", status_panel)
        status_lay.addWidget(self._gate_market_data)
        self._gate_sync = GateRow("Account Sync", status_panel)
        status_lay.addWidget(self._gate_sync)
        self._gate_risk = GateRow("Risk Engine", status_panel)
        status_lay.addWidget(self._gate_risk)
        status_lay.addStretch(1)
        splitter.addWidget(status_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

    def _build_positions(self, root: QVBoxLayout) -> None:
        panel = QFrame(self)
        panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        layout.setSpacing(t.SP_MD)
        header = QWidget(panel)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("POSITIONS", header)
        title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        header_lay.addWidget(title)
        self._pos_count = QLabel("", header)
        self._pos_count.setStyleSheet(t.label(size=t.FS_SMALL))
        header_lay.addWidget(self._pos_count)
        header_lay.addStretch(1)
        layout.addWidget(header)
        self._positions_table = QTableWidget(0, 9, panel)
        self._positions_table.setHorizontalHeaderLabels(
            [
                "SYMBOL",
                "SIDE",
                "QTY",
                "AVG PRICE",
                "LTP",
                "INVESTED",
                "VALUE",
                "P&L",
                "ALLOC %",
            ]
        )
        configure_table(self._positions_table)
        hdr = self._positions_table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setDefaultSectionSize(max(hdr.defaultSectionSize(), 80))
        self._pos_rows_data: list[dict[str, Any]] = []
        self._positions_table.itemSelectionChanged.connect(self._on_position_selected)
        layout.addWidget(self._positions_table, 1)
        self._positions_empty = EmptyState(
            "NO OPEN POSITIONS",
            "Your portfolio is currently flat.",
            panel,
        )
        layout.addWidget(self._positions_empty)
        self._positions_not_configured = EmptyState(
            "ACCOUNT NOT CONFIGURED",
            "Connect/configure an account to view positions.",
            panel,
        )
        layout.addWidget(self._positions_not_configured)
        self._positions_not_configured.setVisible(False)
        root.addWidget(panel, 1)

    def _build_orders(self, root: QVBoxLayout) -> None:
        panel = QFrame(self)
        panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        layout.setSpacing(t.SP_MD)
        tab_bar = QWidget(panel)
        tab_bar.setObjectName("WorkspaceTabBar")
        tab_lay = QHBoxLayout(tab_bar)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.setSpacing(0)
        self._tab_orders = QPushButton("ORDERS", tab_bar)
        self._tab_orders.setCheckable(True)
        self._tab_orders.setChecked(True)
        self._tab_orders.setStyleSheet(t.TAB_QSS)
        self._tab_orders.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_orders.clicked.connect(lambda: self._switch_order_tab("orders"))
        tab_lay.addWidget(self._tab_orders)
        self._tab_fills = QPushButton("FILLS", tab_bar)
        self._tab_fills.setCheckable(True)
        self._tab_fills.setStyleSheet(t.TAB_QSS)
        self._tab_fills.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_fills.clicked.connect(lambda: self._switch_order_tab("fills"))
        tab_lay.addWidget(self._tab_fills)
        tab_lay.addStretch(1)
        layout.addWidget(tab_bar)
        self._orders_table = QTableWidget(0, 6, panel)
        self._orders_table.setHorizontalHeaderLabels(
            [
                "ORDER ID",
                "TIME",
                "SYMBOL",
                "SIDE",
                "QTY",
                "STATUS",
            ]
        )
        configure_table(self._orders_table)
        layout.addWidget(self._orders_table, 1)
        self._orders_empty = EmptyState(
            "NO ORDERS",
            "No orders are available for the selected period.",
            panel,
        )
        layout.addWidget(self._orders_empty)
        self._orders_not_configured = EmptyState(
            "ORDERS UNAVAILABLE",
            "Account connection is required.",
            panel,
        )
        layout.addWidget(self._orders_not_configured)
        self._orders_not_configured.setVisible(False)
        self._fills_table = QTableWidget(0, 5, panel)
        self._fills_table.setHorizontalHeaderLabels(
            [
                "TIME",
                "SYMBOL",
                "SIDE",
                "QTY",
                "PRICE",
            ]
        )
        configure_table(self._fills_table)
        self._fills_table.setVisible(False)
        layout.addWidget(self._fills_table, 1)
        self._fills_empty = EmptyState(
            "NO FILLS",
            "No execution history available.",
            panel,
        )
        self._fills_empty.setVisible(False)
        layout.addWidget(self._fills_empty)
        root.addWidget(panel, 1)

    def _build_alloc_detail(self, root: QVBoxLayout) -> None:
        row = QWidget(self)
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(t.SP_LG)
        alloc_panel = QFrame(row)
        alloc_panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        alloc_lay = QVBoxLayout(alloc_panel)
        alloc_lay.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        alloc_lay.setSpacing(t.SP_MD)
        alloc_title = QLabel("PORTFOLIO ALLOCATION", alloc_panel)
        alloc_title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        alloc_lay.addWidget(alloc_title)
        self._alloc_rows = QWidget(alloc_panel)
        self._alloc_rows_lay = QVBoxLayout(self._alloc_rows)
        self._alloc_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._alloc_rows_lay.setSpacing(t.SP_SM)
        alloc_lay.addWidget(self._alloc_rows)
        self._alloc_empty = EmptyState(
            "ALLOCATION UNAVAILABLE",
            "Position exposure or equity basis is missing.",
            alloc_panel,
        )
        alloc_lay.addWidget(self._alloc_empty)
        row_lay.addWidget(alloc_panel, 1)
        detail_panel = QFrame(row)
        detail_panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        detail_lay = QVBoxLayout(detail_panel)
        detail_lay.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        detail_lay.setSpacing(t.SP_MD)
        detail_title = QLabel("POSITION DETAIL", detail_panel)
        detail_title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        detail_lay.addWidget(detail_title)
        self._detail_symbol = QLabel("No position selected", detail_panel)
        self._detail_symbol.setStyleSheet(t.body(size=t.FS_TITLE, weight=800))
        detail_lay.addWidget(self._detail_symbol)
        self._detail_side = Badge(detail_panel)
        detail_lay.addWidget(self._detail_side)
        self._detail_grid = QWidget(detail_panel)
        grid_lay = QVBoxLayout(self._detail_grid)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(t.SP_XS)
        self._detail_fields: dict[str, QLabel] = {}
        for field in ("Qty", "Average Price", "Current Value", "P&L", "Allocation"):
            field_row = QWidget(self._detail_grid)
            field_lay = QHBoxLayout(field_row)
            field_lay.setContentsMargins(0, 0, 0, 0)
            name = QLabel(field.upper(), field_row)
            name.setStyleSheet(t.label(size=t.FS_SMALL))
            name.setMinimumWidth(130)
            value = QLabel(_NA, field_row)
            value.setStyleSheet(t.body(size=t.FS_BODY))
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            field_lay.addWidget(name, 1)
            field_lay.addWidget(value)
            grid_lay.addWidget(field_row)
            self._detail_fields[field] = value
        detail_lay.addWidget(self._detail_grid)
        detail_lay.addStretch(1)
        row_lay.addWidget(detail_panel, 1)
        root.addWidget(row)

    def _build_risk(self, root: QVBoxLayout) -> None:
        panel = QFrame(self)
        panel.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS}px;"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(t.SP_LG, t.SP_LG, t.SP_LG, t.SP_LG)
        layout.setSpacing(t.SP_MD)
        header = QWidget(panel)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("PORTFOLIO RISK", header)
        title.setStyleSheet(t.section_title(size=t.FS_SMALL))
        header_lay.addWidget(title)
        header_lay.addStretch(1)
        self._health_badge = Badge(header)
        header_lay.addWidget(self._health_badge)
        layout.addWidget(header)
        self._health_note = QLabel("", panel)
        self._health_note.setStyleSheet(t.label(size=t.FS_SMALL))
        self._health_note.setWordWrap(True)
        layout.addWidget(self._health_note)
        self._risk_grid = QWidget(panel)
        risk_lay = QVBoxLayout(self._risk_grid)
        risk_lay.setContentsMargins(0, 0, 0, 0)
        risk_lay.setSpacing(t.SP_XS)
        self._risk_fields: dict[str, QLabel] = {}
        for field in ("Exposure", "Largest Position", "Concentration", "Drawdown", "Volatility"):
            field_row = QWidget(self._risk_grid)
            field_lay = QHBoxLayout(field_row)
            field_lay.setContentsMargins(0, 0, 0, 0)
            name = QLabel(field.upper(), field_row)
            name.setStyleSheet(t.label(size=t.FS_SMALL))
            name.setMinimumWidth(150)
            value = QLabel(_NA, field_row)
            value.setStyleSheet(t.body(size=t.FS_BODY))
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            field_lay.addWidget(name, 1)
            field_lay.addWidget(value)
            risk_lay.addWidget(field_row)
            self._risk_fields[field] = value
        layout.addWidget(self._risk_grid)
        self._risk_empty = EmptyState(
            "RISK UNAVAILABLE",
            "Risk metrics are not provided by the current backend.",
            panel,
        )
        layout.addWidget(self._risk_empty)
        root.addWidget(panel)

    def _switch_order_tab(self, tab: str) -> None:
        is_orders = tab == "orders"
        self._tab_orders.setChecked(is_orders)
        self._tab_fills.setChecked(not is_orders)
        self._orders_table.setVisible(is_orders)
        self._orders_empty.setVisible(is_orders and self._orders_table.rowCount() == 0)
        self._orders_not_configured.setVisible(False)
        self._fills_table.setVisible(not is_orders)
        self._fills_empty.setVisible(not is_orders and self._fills_table.rowCount() == 0)

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
            self._updated_label.setText(f"Updated {QTime.currentTime().toString('HH:mm:ss')}")

    def _on_refresh_clicked(self) -> None:
        self._refresh_btn.setText("Syncing...")
        self._refresh_btn.setEnabled(False)
        self.refresh_requested.emit()
        self.refresh()
        self._refresh_btn.setText("Refresh")
        self._refresh_btn.setEnabled(True)

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _render_all(self) -> None:
        state = self._state
        self._render_account_status(state)
        self._render_kpis(state)
        self._render_performance(state)
        self._render_status_gates(state)
        self._render_positions(state)
        self._render_allocation(state)
        self._render_orders_fills(state)
        self._render_risk(state)

    def _allocation_entries(self, state: dict[str, Any]) -> list[tuple[str, float]]:
        equity = _equity_of(state)
        if not equity:
            return []
        entries: list[tuple[str, float]] = []
        for entry in self._pos_rows_data:
            try:
                exposure = float(entry.get("exposure", 0) or entry.get("value", 0) or 0)
            except (TypeError, ValueError):
                continue
            try:
                pct = 100.0 * exposure / equity
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            symbol = str(entry.get("symbol", ""))
            if symbol:
                entries.append((symbol, pct))
        entries.sort(key=lambda item: item[1], reverse=True)
        return entries

    def _on_position_selected(self) -> None:
        selected = self._positions_table.selectionModel()
        rows = sorted({index.row() for index in selected.selectedRows()}) if selected else []
        if not rows or rows[0] >= len(self._pos_rows_data):
            self._detail_symbol.setText("No position selected")
            self._detail_side.set_status("", "muted")
            for value in self._detail_fields.values():
                value.setText(_NA)
            return
        entry = self._pos_rows_data[rows[0]]
        symbol = str(entry.get("symbol", ""))
        self._detail_symbol.setText(symbol or "No position selected")
        side = str(entry.get("side", ""))
        self._detail_side.set_status(side, "accent" if side else "muted")
        equity = _equity_of(self._state)
        alloc = _NA
        if equity:
            try:
                exposure = float(entry.get("exposure", 0) or entry.get("value", 0) or 0)
                alloc = f"{100.0 * exposure / equity:.1f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                alloc = _NA
        pnl_val = entry.get("pnl", entry.get("unrealized"))
        values = {
            "Qty": _text(entry.get("quantity"), ""),
            "Average Price": _text(entry.get("avg_price"), ""),
            "Current Value": _text(entry.get("value", entry.get("exposure")), ""),
            "P&L": _money(pnl_val) if pnl_val is not None else _NA,
            "Allocation": alloc,
        }
        for field, display in values.items():
            label = self._detail_fields.get(field)
            if label is not None:
                label.setText(display)

    def _is_configured(self, state: dict[str, Any]) -> bool:
        broker = state.get("broker") or {}
        if isinstance(broker, dict):
            name = str(broker.get("name", "")).upper()
            if name in ("NOT CONFIGURED", "", "NONE"):
                return False
            connected = broker.get("connected")
            if connected is True or broker.get("status") in ("CONNECTED", "LIVE_READY"):
                return True
        funds = state.get("funds")
        return bool(isinstance(funds, dict) and funds)

    def _render_account_status(self, state: dict[str, Any]) -> None:
        configured = self._is_configured(state)
        broker = state.get("broker") or {}
        if not isinstance(broker, dict):
            broker = {}
        if configured:
            self._status_indicator.set_state("ACCOUNT CONNECTED", "ok")
            name = _text(broker.get("name"), "")
            masked = name[-4:] if len(name) > 4 else name
            env = _text(broker.get("environment"), "")
            parts = []
            if masked:
                parts.append(f"Account  XXXX{masked}")
            if env:
                parts.append(f"Environment  {env}")
            self._account_info_label.setText("     ".join(parts))
            lifecycle = _text(state.get("lifecycle"), "")
            mode = _text(state.get("mode"), "")
            detail_parts = []
            if mode:
                detail_parts.append(f"Mode {mode}")
            if lifecycle:
                detail_parts.append(f"Lifecycle {lifecycle}")
            self._account_detail_label.setText("     ".join(detail_parts))
        else:
            self._status_indicator.set_state("ACCOUNT NOT CONFIGURED", "warn")
            self._account_info_label.setText(
                "Connect/configure a broker account to view live portfolio information."
            )
            self._account_detail_label.setText("")

    def _render_kpis(self, state: dict[str, Any]) -> None:
        funds = state.get("funds")
        pnl = state.get("pnl") or {}
        if not isinstance(pnl, dict):
            pnl = {}
        configured = self._is_configured(state)
        if not configured:
            for kpi in (
                self._kpi_equity,
                self._kpi_available,
                self._kpi_invested,
                self._kpi_total_pnl,
                self._kpi_today_pnl,
                self._kpi_unrealized,
                self._kpi_realized,
                self._kpi_return,
            ):
                kpi.set_value("Unavailable")
            return
        if isinstance(funds, dict):
            equity = funds.get("equity")
            available = funds.get("available")
            used = funds.get("used")
            self._kpi_equity.set_value(_fmt_equity(equity))
            self._kpi_available.set_value(_fmt_equity(available))
            self._kpi_invested.set_value(_fmt_equity(used))
        else:
            self._kpi_equity.set_value(_NA)
            self._kpi_available.set_value(_NA)
            self._kpi_invested.set_value(_NA)
        total_pnl = pnl.get("total")
        unrealized = pnl.get("unrealized")
        realized = pnl.get("realized")
        today_pnl = pnl.get("today")
        self._kpi_total_pnl.set_value(
            _money(total_pnl),
            t.semantic(total_pnl) if total_pnl is not None else None,
        )
        self._kpi_unrealized.set_value(
            _money(unrealized),
            t.semantic(unrealized) if unrealized is not None else None,
        )
        self._kpi_realized.set_value(
            _money(realized),
            t.semantic(realized) if realized is not None else None,
        )
        self._kpi_today_pnl.set_value(
            _money(today_pnl),
            t.semantic(today_pnl) if today_pnl is not None else None,
        )
        equity_val = _equity_of(state)
        if equity_val and total_pnl is not None:
            try:
                ret_pct = 100.0 * float(total_pnl) / float(equity_val)
                self._kpi_return.set_value(_fmt_pct(ret_pct), t.semantic(ret_pct))
            except (TypeError, ValueError, ZeroDivisionError):
                self._kpi_return.set_value(_NA)
        else:
            self._kpi_return.set_value(_NA)

    def _render_performance(self, state: dict[str, Any]) -> None:
        pnl = state.get("pnl") or {}
        if not isinstance(pnl, dict):
            self._perf_empty.setVisible(True)
            self._perf_summary.setVisible(False)
            return
        wins = pnl.get("wins")
        losses = pnl.get("losses")
        trades = None
        try:
            if wins is not None and losses is not None:
                trades = int(wins) + int(losses)
        except (TypeError, ValueError):
            trades = None
        win_rate = _NA
        try:
            if wins is not None and trades:
                win_rate = f"{100.0 * float(wins) / float(trades):.1f}%"
        except (TypeError, ValueError, ZeroDivisionError):
            win_rate = _NA
        has_data = trades is not None or pnl.get("total") is not None
        self._perf_empty.setVisible(not has_data)
        self._perf_summary.setVisible(has_data)
        if has_data and self._perf_rows:
            values = [_text(trades), _text(wins), _text(losses), win_rate]
            for (_, val_label), val in zip(self._perf_rows, values, strict=True):
                val_label.setText(val)

    def _render_status_gates(self, state: dict[str, Any]) -> None:
        configured = self._is_configured(state)
        risk = state.get("risk") or {}
        rstatus = str(risk.get("status", _NA)) if isinstance(risk, dict) else _NA
        recon = state.get("reconciliation") or {}
        rstate = str(recon.get("status", _NA)) if isinstance(recon, dict) else _NA
        kill = state.get("kill") or {}
        halted = bool(kill.get("halted")) if isinstance(kill, dict) else False
        if not configured:
            for gate in (
                self._gate_trading,
                self._gate_market_data,
                self._gate_sync,
                self._gate_risk,
            ):
                gate.render_gate("NOT READY", "Account not configured")
            return
        trading_ok = not halted and rstatus not in ("BLOCKED", "HALTED")
        self._gate_trading.render_gate(
            "READY" if trading_ok else "BLOCKED",
            "Halted" if halted else "Active" if trading_ok else rstatus,
        )
        broker = state.get("broker") or {}
        b_connected = False
        if isinstance(broker, dict):
            b_connected = broker.get("connected") is True or broker.get("status") in (
                "CONNECTED",
                "LIVE_READY",
            )
        self._gate_market_data.render_gate(
            "READY" if b_connected else "NOT READY",
            "Connected" if b_connected else "Unavailable",
        )
        sync_ok = rstate == "CLEAN"
        self._gate_sync.render_gate(
            "READY" if sync_ok else "NOT READY",
            "Healthy" if sync_ok else rstate,
        )
        risk_ok = rstatus == "READY"
        self._gate_risk.render_gate(
            "READY" if risk_ok else "NOT READY",
            "Active" if risk_ok else rstatus,
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
                    "avg_price": position.get("avg_price"),
                    "current_price": position.get("current_price"),
                    "invested": position.get("exposure", 0),
                    "value": position.get("exposure", 0),
                    "exposure": position.get("exposure", 0),
                    "pnl": position.get("unrealized"),
                }
            ]
        positions = state.get("positions")
        if isinstance(positions, list):
            return [p for p in positions if isinstance(p, dict)]
        return []

    def _render_positions(self, state: dict[str, Any]) -> None:
        configured = self._is_configured(state)
        rows_data = self._positions_from_state(state)
        equity = _equity_of(state)
        rows: list[list[str]] = []
        self._pos_rows_data = list(rows_data)
        for entry in rows_data:
            try:
                exposure = float(entry.get("exposure", 0) or entry.get("value", 0) or 0)
            except (TypeError, ValueError):
                exposure = 0.0
            alloc = _NA
            if equity:
                try:
                    alloc = f"{100.0 * exposure / equity:.1f}%"
                except (TypeError, ValueError, ZeroDivisionError):
                    alloc = _NA
            pnl_val = entry.get("pnl", entry.get("unrealized"))
            rows.append(
                [
                    _text(entry.get("symbol"), ""),
                    _text(entry.get("side"), ""),
                    _text(entry.get("quantity"), ""),
                    _text(entry.get("avg_price"), ""),
                    _text(entry.get("current_price", entry.get("ltp")), ""),
                    _text(entry.get("invested", entry.get("exposure")), ""),
                    _text(entry.get("value", entry.get("exposure")), ""),
                    _money(pnl_val) if pnl_val is not None else _NA,
                    alloc,
                ]
            )
        fill_table(self._positions_table, rows)
        has_positions = bool(rows)
        self._positions_table.setVisible(has_positions)
        self._positions_empty.setVisible(not has_positions and configured)
        self._positions_not_configured.setVisible(not has_positions and not configured)
        count_text = (
            f"{len(rows)} open position{'s' if len(rows) != 1 else ''}" if has_positions else ""
        )
        self._pos_count.setText(count_text)

    def _render_allocation(self, state: dict[str, Any]) -> None:
        entries = self._allocation_entries(state)
        for index in reversed(range(self._alloc_rows_lay.count())):
            item = self._alloc_rows_lay.takeAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._alloc_empty.setVisible(not entries)
        self._alloc_rows.setVisible(bool(entries))
        for symbol, pct in entries[:8]:
            row = QWidget(self._alloc_rows)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(t.SP_MD)
            name = QLabel(symbol, row)
            name.setStyleSheet(t.body(size=t.FS_TABLE, weight=700))
            name.setMinimumWidth(110)
            row_lay.addWidget(name)
            bar = QProgressBar(row)
            bar.setRange(0, 1000)
            bar.setValue(max(0, min(1000, int(pct * 10))))
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            bar.setStyleSheet(
                f"QProgressBar {{ background: {t.PANEL2}; border: none; border-radius: 5px; }}"
                f"QProgressBar::chunk {{ background: {t.ACCENT_DIM}; border-radius: 5px; }}"
            )
            row_lay.addWidget(bar, 1)
            pct_label = QLabel(f"{pct:.1f}%", row)
            pct_label.setStyleSheet(t.body(size=t.FS_TABLE))
            pct_label.setMinimumWidth(56)
            pct_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_lay.addWidget(pct_label)
            self._alloc_rows_lay.addWidget(row)

    def _render_risk(self, state: dict[str, Any]) -> None:
        configured = self._is_configured(state)
        risk = state.get("risk") if isinstance(state.get("risk"), dict) else {}
        entries = self._allocation_entries(state)
        largest_pct = entries[0][1] if entries else None
        if not configured:
            self._health_badge.set_status("NOT CONFIGURED", "muted")
            self._health_note.setText("Connect an account to assess portfolio health.")
        elif (risk.get("status") if isinstance(risk, dict) else None) in ("BLOCKED", "HALTED"):
            self._health_badge.set_status("BLOCKED", "bad")
            self._health_note.setText("Risk engine is blocking activity.")
        elif largest_pct is not None and largest_pct >= 50.0:
            self._health_badge.set_status("CONCENTRATED", "warn")
            self._health_note.setText(
                f"Largest position is {largest_pct:.1f}% of equity (≥50% rule)."
            )
        else:
            self._health_badge.set_status("HEALTHY", "ok")
            self._health_note.setText("No blocking risk conditions detected.")
        raw_metrics = state.get("risk_metrics")
        risk_payload: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
        shown = 0
        exposure_total = sum(
            _safe_float(entry.get("exposure", entry.get("value", 0)))
            for entry in self._pos_rows_data
        )
        if self._pos_rows_data:
            self._risk_fields["Exposure"].setText(_fmt_equity(exposure_total))
            shown += 1
        else:
            self._risk_fields["Exposure"].setText(_NA)
        if entries:
            self._risk_fields["Largest Position"].setText(f"{entries[0][0]}  {entries[0][1]:.1f}%")
            self._risk_fields["Concentration"].setText(f"{entries[0][1]:.1f}%")
            shown += 2
        else:
            self._risk_fields["Largest Position"].setText(_NA)
            self._risk_fields["Concentration"].setText(_NA)
        for field, key in (("Drawdown", "drawdown"), ("Volatility", "volatility")):
            raw = risk_payload.get(key)
            if raw is None:
                self._risk_fields[field].setText(_NA)
            else:
                try:
                    self._risk_fields[field].setText(f"{float(raw):.2f}%")
                    shown += 1
                except (TypeError, ValueError):
                    self._risk_fields[field].setText(str(raw))
                    shown += 1
        self._risk_grid.setVisible(shown > 0)
        self._risk_empty.setVisible(shown == 0)

    def _render_orders_fills(self, state: dict[str, Any]) -> None:
        configured = self._is_configured(state)
        orders = state.get("orders") or []
        order_rows: list[list[str]] = []
        if isinstance(orders, list):
            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_rows.append(
                    [
                        _text(order.get("order_id"), ""),
                        _text(order.get("time", order.get("timestamp")), ""),
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
                        _text(fill.get("side"), ""),
                        _text(fill.get("quantity"), ""),
                        _text(fill.get("price"), ""),
                    ]
                )
        fill_table(self._fills_table, fill_rows)
        has_orders = bool(order_rows)
        has_fills = bool(fill_rows)
        is_orders_tab = self._tab_orders.isChecked()
        self._orders_empty.setVisible(is_orders_tab and not has_orders and configured)
        self._orders_not_configured.setVisible(is_orders_tab and not has_orders and not configured)
        self._fills_empty.setVisible(not is_orders_tab and not has_fills)
