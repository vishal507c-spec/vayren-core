"""Stock Ranking — the discovery engine for the Strategy Lab.

The ranking table is not merely a table: it is the entry point to the next
stage of analysis (spec §12). Scanning it must answer, preattentively:

    WHO?           → SYMBOL           (left aligned, bold)
    HOW MUCH?      → NET P&L          (primary emphasis, semantic colour)
    WHAT RETURN?   → RETURN %         (primary emphasis, semantic colour)

…then quality/risk columns (TRADES, WIN%, PF, MAX DD, SHARPE) at deliberately
reduced emphasis, right aligned so magnitudes compare down the column.

Ranking covers ONLY the currently selected Watchlist symbols (never the full
NSE universe). All numbers come from the existing multi-symbol backtest
results via :func:`derive_symbol_result` — no new metric formulas, no
fabrication. Symbols without a valid result render as unavailable ("—")
instead of receiving an invented rank.

Selecting a row opens the **Selected Stock** workspace directly beneath the
table (spec §15-§17): key metrics, then PERFORMANCE / TRADES / EQUITY /
DRAWDOWN. Context is never destroyed — the row stays selected, the rank stays
visible, and the universe/mode/period captions stay in place.

Presentation only: no bus, no SQL, no events. Follows the lab theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.engine.directional import derive_symbol_results
from backtest.ui.analytics_views import (
    DrawdownView,
    EquityCurveView,
    decimate_envelope,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t

_COL_RANK = 0
_COL_SYMBOL = 1
_COL_NET = 2
_COL_RET = 3
_COL_TRADES = 4
_COL_WIN = 5
_COL_PF = 6
_COL_DD = 7
_COL_SHARPE = 8

_HEADERS: tuple[str, ...] = (
    "#",
    "SYMBOL",
    "NET P&L",
    "RETURN %",
    "TRADES",
    "WIN%",
    "PF",
    "MAX DD",
    "SHARPE",
)

_SORT_KEYS: dict[int, str] = {
    _COL_RANK: "net_profit",
    _COL_SYMBOL: "symbol",
    _COL_NET: "net_profit",
    _COL_RET: "return_pct",
    _COL_TRADES: "total_trades",
    _COL_WIN: "win_rate",
    _COL_PF: "profit_factor",
    _COL_DD: "max_drawdown_pct",
    _COL_SHARPE: "sharpe_ratio",
}

# Dropdown order matches the spec: the criterion the rank explains.
_SORT_OPTIONS: tuple[tuple[str, int], ...] = (
    ("Net P&L", _COL_NET),
    ("Return %", _COL_RET),
    ("Trades", _COL_TRADES),
    ("Win Rate", _COL_WIN),
    ("Profit Factor", _COL_PF),
    ("Max Drawdown", _COL_DD),
    ("Sharpe", _COL_SHARPE),
)

# Primary columns carry the scan; secondary columns carry the inspection (§13).
_PRIMARY_COLS = (_COL_SYMBOL, _COL_NET, _COL_RET)
_ROW_HEIGHT = 26
_TRADE_ROW_CAP = 400


def _format_inr(value: float) -> str:
    """Indian-grouping money text, e.g. -1234567.8 → '-12,34,567.80'."""
    negative = value < 0
    whole, frac = divmod(abs(round(value * 100) / 100), 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    text = f"{digits}.{int(round(frac * 100)):02d}"
    return ("-" if negative else "") + text


@dataclass(frozen=True)
class StockRankRow:
    """One ranking row — metrics are borrowed, never recomputed with new formulas.

    Attributes:
        symbol: Selected symbol.
        rank: 1-based position among ranked rows (None = unranked, shows "—").
        net_profit: Per-symbol net P&L from ``derive_symbol_result`` (None = n/a).
        return_pct: Per-symbol net P&L percent (existing ``net_profit_pct``).
        total_trades: Per-symbol trade count (None = n/a).
        win_rate: 0-1 fraction (None = n/a).
        profit_factor: Gross profit / gross loss (None = n/a).
        max_drawdown_pct: Stored positive, lower is better (None = n/a).
        sharpe_ratio: Per-trade Sharpe (None = n/a).
        status: One of "ranked" | "no_trades" | "unavailable" | "pending".
        note: Human reason shown as tooltip for unranked rows.
    """

    symbol: str
    rank: int | None
    net_profit: float | None
    return_pct: float | None
    total_trades: int | None
    win_rate: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    status: str
    note: str = ""


def _build_ranking(
    base: Any | None,
    symbols: tuple[str, ...] | list[str],
    errors: dict[str, str] | None = None,
    last_run: tuple[str, ...] | list[str] | None = None,
) -> tuple[tuple[StockRankRow, ...], dict[str, Any]]:
    """Build rows plus the per-symbol result views backing them.

    Views come from the same single grouped derivation pass, so the widget
    can serve details/charts without rescanning merged trades.

    Args:
        base: Mode-consistent result (BUY → LONG split, SELL → SHORT split,
            COMPARE → full). None means no backtest yet — every row is pending.
        symbols: Selected Watchlist symbols in selection order (the ONLY universe).
        errors: Per-symbol failure reasons from the batch coordinator ({} = none).
        last_run: Symbols attempted by the last completed RUN (None = never ran).
            Selected symbols absent from *last_run* render as pending ("run again")
            instead of borrowing a fake zero-trade rank from an older run.

    Returns:
        ``(rows, views)`` — rows with ranked entries first (net_profit
        descending, 1st = best), then unranked entries in selection order.
        Never invents numbers: unavailable / pending / no-trade rows carry
        None metrics and no rank.
    """
    ordered = tuple(symbols)
    if not ordered:
        return (), {}
    failed: dict[str, str] = dict(errors) if errors else {}
    attempted: tuple[str, ...] | None = tuple(last_run) if last_run is not None else None
    if base is None or attempted is None:
        return (
            tuple(
                StockRankRow(
                    symbol=symbol,
                    rank=None,
                    net_profit=None,
                    return_pct=None,
                    total_trades=None,
                    win_rate=None,
                    profit_factor=None,
                    max_drawdown_pct=None,
                    sharpe_ratio=None,
                    status="pending",
                    note="No backtest yet — run backtest",
                )
                for symbol in ordered
            ),
            {},
        )
    ranked: list[tuple[str, Any]] = []
    unranked: list[StockRankRow] = []
    # One grouped pass over the merged trades (identical math to per-symbol
    # derivation, without rescanning all trades for every symbol).
    views = derive_symbol_results(base, ordered)
    for symbol in ordered:
        if symbol not in attempted:
            unranked.append(
                StockRankRow(
                    symbol=symbol,
                    rank=None,
                    net_profit=None,
                    return_pct=None,
                    total_trades=None,
                    win_rate=None,
                    profit_factor=None,
                    max_drawdown_pct=None,
                    sharpe_ratio=None,
                    status="pending",
                    note="Not in last run — run backtest again",
                )
            )
            continue
        if symbol in failed:
            unranked.append(
                StockRankRow(
                    symbol=symbol,
                    rank=None,
                    net_profit=None,
                    return_pct=None,
                    total_trades=None,
                    win_rate=None,
                    profit_factor=None,
                    max_drawdown_pct=None,
                    sharpe_ratio=None,
                    status="unavailable",
                    note=str(failed[symbol]) or "No valid result",
                )
            )
            continue
        view = views.get(symbol)
        if view is None:
            unranked.append(
                StockRankRow(
                    symbol=symbol,
                    rank=None,
                    net_profit=None,
                    return_pct=None,
                    total_trades=None,
                    win_rate=None,
                    profit_factor=None,
                    max_drawdown_pct=None,
                    sharpe_ratio=None,
                    status="pending",
                    note="No backtest yet — run backtest",
                )
            )
            continue
        metrics = view.metrics
        if metrics.total_trades == 0:
            unranked.append(
                StockRankRow(
                    symbol=symbol,
                    rank=None,
                    net_profit=None,
                    return_pct=None,
                    total_trades=0,
                    win_rate=None,
                    profit_factor=None,
                    max_drawdown_pct=None,
                    sharpe_ratio=None,
                    status="no_trades",
                    note="No trades in range",
                )
            )
            continue
        ranked.append((symbol, metrics))
    ranked.sort(key=lambda item: (-float(item[1].net_profit), item[0]))
    out: list[StockRankRow] = []
    for index, (symbol, metrics) in enumerate(ranked, start=1):
        out.append(
            StockRankRow(
                symbol=symbol,
                rank=index,
                net_profit=float(metrics.net_profit),
                return_pct=float(metrics.net_profit_pct),
                total_trades=int(metrics.total_trades),
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                max_drawdown_pct=float(metrics.max_drawdown_pct),
                sharpe_ratio=metrics.sharpe_ratio,
                status="ranked",
            )
        )
    out.extend(unranked)
    return tuple(out), views


def build_stock_ranking(
    base: Any | None,
    symbols: tuple[str, ...] | list[str],
    errors: dict[str, str] | None = None,
    last_run: tuple[str, ...] | list[str] | None = None,
) -> tuple[StockRankRow, ...]:
    """Build ranking rows for *symbols* from an existing backtest result.

    Same contract as before (rows only); the widget uses :func:`_build_ranking`
    when it also needs the backing per-symbol views.
    """
    rows, _ = _build_ranking(base, symbols, errors, last_run)
    return rows


class _Sparkline(QWidget):
    """Compact per-stock equity curve (LOD-decimated, paint-only)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(72)
        self.setMaximumHeight(96)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        from PySide6.QtGui import QPainter, QPen

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(t.BG1))
        if len(self._values) < 2 or self.width() <= 0:
            return
        pretty = decimate_envelope(self._values, max(64, self.width() * 2))
        lo, hi = min(pretty), max(pretty)
        span = hi - lo or 1.0
        last = pretty[-1]
        color = QColor(t.POS if last >= pretty[0] else t.NEG)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.6))
        pts = []
        for i, value in enumerate(pretty):
            x = i / max(1, len(pretty) - 1) * self.width()
            y = self.height() - 5 - (value - lo) / span * (self.height() - 10)
            from PySide6.QtCore import QPointF as _QPointF

            pts.append(_QPointF(x, y))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])


class _MetricBlock(QWidget):
    """Label + value pair. Value is always materially larger than the label."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._caption = QLabel(caption, self)
        self._caption.setStyleSheet(t.label(t.MUTED, t.FS_LABEL, 700, 0.7))
        lay.addWidget(self._caption)
        self._value = QLabel("—", self)
        self._value.setStyleSheet(t.metric(t.FS_METRIC_SM, t.TEXT, 700))
        lay.addWidget(self._value)

    def set(self, text: str, color: str = t.TEXT) -> None:
        self._value.setText(text)
        self._value.setStyleSheet(t.metric(t.FS_METRIC_SM, color, 700))


class _SelectedStockPanel(QWidget):
    """Drill-down workspace for one ranked stock (spec §15-§17).

    Never navigates away: the table stays above, the row stays selected and
    the rank stays visible, so the user always knows where they are inside
    the current backtest.
    """

    _TABS = ("PERFORMANCE", "TRADES", "EQUITY", "DRAWDOWN")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1}; border-top: 1px solid {t.BORDER};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_LG, t.SP_MD, t.SP_LG, t.SP_MD)
        lay.setSpacing(t.SP_SM)

        self.title = QLabel("SELECT A STOCK", self)
        self.title.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_TITLE}px; font-weight: 800;")
        lay.addWidget(self.title)
        self.sub = QLabel("Choose a stock from the ranking table to inspect its performance.", self)
        self.sub.setStyleSheet(t.body(t.FS_BODY, t.TEXT2))
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)

        # key metrics — label above value, values larger than labels
        self.metrics = QWidget(self)
        self.metrics.setVisible(False)
        grid = QGridLayout(self.metrics)
        grid.setContentsMargins(0, t.SP_XS, 0, t.SP_XS)
        grid.setHorizontalSpacing(t.SP_XXL)
        grid.setVerticalSpacing(t.SP_SM)
        self._blocks: dict[str, _MetricBlock] = {}
        for col, key in enumerate(
            ("NET P&L", "RETURN", "WIN RATE", "PROFIT FACTOR", "MAX DD", "SHARPE")
        ):
            block = _MetricBlock(key, self.metrics)
            self._blocks[key] = block
            grid.addWidget(block, 0, col)
        grid.setColumnStretch(6, 1)
        lay.addWidget(self.metrics)

        # analytical tabs — only the selected view consumes the content area
        self.tabs = QWidget(self)
        self.tabs.setObjectName("StockDetailTabs")
        self.tabs.setStyleSheet(
            f"QWidget#StockDetailTabs {{ background: transparent;"
            f" border-bottom: 1px solid {t.BORDER}; }}"
        )
        self.tabs.setVisible(False)
        tab_lay = QHBoxLayout(self.tabs)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.setSpacing(0)
        group = QButtonGroup(self.tabs)
        group.setExclusive(True)
        for index, text in enumerate(self._TABS):
            button = QPushButton(text, self.tabs)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(t.TAB_QSS)
            button.clicked.connect(lambda _c=False, i=index: self.stack.setCurrentIndex(i))
            group.addButton(button)
            tab_lay.addWidget(button)
        tab_lay.addStretch(1)
        lay.addWidget(self.tabs)

        self.stack = QStackedWidget(self)
        self.stack.setVisible(False)
        lay.addWidget(self.stack, 1)

        # ── PERFORMANCE ──
        perf = QWidget(self.stack)
        perf_lay = QVBoxLayout(perf)
        perf_lay.setContentsMargins(0, t.SP_SM, 0, 0)
        perf_lay.setSpacing(t.SP_SM)
        self.caption = QLabel("", perf)
        self.caption.setStyleSheet(t.label(t.MUTED, t.FS_LABEL, 700, 0.8))
        perf_lay.addWidget(self.caption)
        self.spark = _Sparkline(perf)
        perf_lay.addWidget(self.spark)
        self.trade_stats = QLabel("", perf)
        self.trade_stats.setStyleSheet(t.body(t.FS_SMALL, t.TEXT2))
        self.trade_stats.setWordWrap(True)
        perf_lay.addWidget(self.trade_stats)
        perf_lay.addStretch(1)
        self.stack.addWidget(perf)

        # ── TRADES ──
        trades = QWidget(self.stack)
        trades_lay = QVBoxLayout(trades)
        trades_lay.setContentsMargins(0, t.SP_SM, 0, 0)
        trades_lay.setSpacing(0)
        self.trade_table = QTableWidget(trades)
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels(
            ["#", "DIRECTION", "ENTRY", "EXIT", "ENTRY PX", "EXIT PX", "P&L", "BARS"]
        )
        self.trade_table.verticalHeader().setVisible(False)
        self.trade_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trade_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trade_table.setAlternatingRowColors(True)
        self.trade_table.setWordWrap(False)
        self.trade_table.setStyleSheet(t.TABLE_QSS)
        self.trade_table.horizontalHeader().setStretchLastSection(False)
        trades_lay.addWidget(self.trade_table)
        self.trades_empty = QLabel(
            "NO TRADES\nThis stock produced no closed trades in the tested range.", trades
        )
        self.trades_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trades_empty.setStyleSheet(t.body(t.FS_BODY, t.MUTED))
        self.trades_empty.setVisible(False)
        trades_lay.addWidget(self.trades_empty)
        self.stack.addWidget(trades)

        # ── EQUITY ── (spec §18: an analytical surface, never a thin strip)
        self.equity = EquityCurveView(self.stack)
        self.equity.setMinimumHeight(300)
        self.stack.addWidget(self.equity)

        # ── DRAWDOWN ──
        self.drawdown = DrawdownView(self.stack)
        self.drawdown.setMinimumHeight(300)
        self.stack.addWidget(self.drawdown)

    # ── rendering ───────────────────────────────────────────────

    def show_empty(self) -> None:
        self.title.setText("SELECT A STOCK")
        self.sub.setText("Choose a stock from the ranking table to inspect its performance.")
        self.sub.setVisible(True)
        self.metrics.setVisible(False)
        self.tabs.setVisible(False)
        self.stack.setVisible(False)

    def show_unranked(self, symbol: str, note: str) -> None:
        self.title.setText(f"{symbol} — NO VALID RESULT")
        self.sub.setText(note or "No valid result for this stock.")
        self.sub.setVisible(True)
        self.metrics.setVisible(False)
        self.tabs.setVisible(False)
        self.stack.setVisible(False)

    def show_row(
        self,
        row: StockRankRow,
        view: Any | None,
        criterion: str,
        mode_label: str = "",
    ) -> None:
        rank_bit = f"Rank #{row.rank} by {criterion}" if row.rank else "Unranked"
        trades_bit = f"{row.total_trades} trades" if row.total_trades is not None else "no trades"
        mode_bit = f" · {mode_label}" if mode_label else ""
        self.title.setText(f"{row.symbol}  ·  {rank_bit}  ·  {trades_bit}{mode_bit}")
        self.sub.setVisible(False)

        net = f"₹{_format_inr(row.net_profit)}" if row.net_profit is not None else "—"
        ret = f"{row.return_pct:+.2f}%" if row.return_pct is not None else "—"
        win = f"{row.win_rate * 100:.1f}%" if row.win_rate is not None else "—"
        pf = f"{row.profit_factor:.2f}" if row.profit_factor is not None else "—"
        dd = f"-{row.max_drawdown_pct:.2f}%" if row.max_drawdown_pct is not None else "—"
        sh = f"{row.sharpe_ratio:.2f}" if row.sharpe_ratio is not None else "—"
        self._blocks["NET P&L"].set(net, t.semantic(row.net_profit))
        self._blocks["RETURN"].set(ret, t.semantic(row.return_pct))
        self._blocks["WIN RATE"].set(win)
        self._blocks["PROFIT FACTOR"].set(
            pf, t.POS if (row.profit_factor or 0) > 1 else t.NEG if row.profit_factor else t.TEXT
        )
        self._blocks["MAX DD"].set(dd, t.NEG if row.max_drawdown_pct else t.TEXT)
        self._blocks["SHARPE"].set(sh, t.semantic(row.sharpe_ratio, neutral=t.TEXT))
        self.metrics.setVisible(True)
        self.tabs.setVisible(True)
        self.stack.setVisible(True)
        self.stack.setCurrentIndex(0)

        curve = getattr(view, "equity_curve", None) or ()
        values = [float(p.equity) for p in curve]
        if len(values) >= 2:
            self.caption.setText(f"{row.symbol} — EQUITY CURVE")
            self.spark.set_values(values)
            self.spark.setVisible(True)
        else:
            self.caption.setText(f"{row.symbol} — NO EQUITY DATA")
            self.spark.setVisible(False)
        self.equity.set_result(view)
        self.drawdown.set_result(view)
        self._fill_trades(view)

        if row.total_trades:
            trades = list(getattr(view, "trades", ()) or ())
            wins = sum(1 for trade in trades if trade.winning)
            losses = len(trades) - wins
            self.trade_stats.setText(
                f"{wins} winning · {losses} losing · {len(trades)} closed trades in range"
            )
            self.trade_stats.setVisible(True)
        else:
            self.trade_stats.setVisible(False)

    def _fill_trades(self, view: Any | None) -> None:
        trades = list(getattr(view, "trades", ()) or ())
        table = self.trade_table
        table.setRowCount(0)
        if not trades:
            table.setVisible(False)
            self.trades_empty.setVisible(True)
            return
        shown = trades[:_TRADE_ROW_CAP]
        table.setVisible(True)
        self.trades_empty.setVisible(False)
        table.setRowCount(len(shown))
        for index, trade in enumerate(shown):
            values = (
                str(index + 1),
                trade.side,
                trade.entry_time[:16],
                trade.exit_time[:16],
                f"{trade.entry_price:.2f}",
                f"{trade.exit_price:.2f}",
                f"{trade.pnl:+,.2f}",
                str(trade.bars_held),
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    if col in (1, 2, 3)
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                font = QFont()
                font.setPixelSize(t.FS_TABLE)
                font.setBold(col == 6)
                item.setFont(font)
                if col == 1:
                    item.setForeground(QColor("#00C7B7" if trade.side == "LONG" else t.NEG))
                elif col == 6:
                    item.setForeground(QColor(t.POS if trade.winning else t.NEG))
                else:
                    item.setForeground(QColor(t.TEXT2 if col else t.MUTED))
                table.setItem(index, col, item)
        table.resizeColumnsToContents()
        for index in range(table.rowCount()):
            table.setRowHeight(index, 24)
        table.horizontalHeader().setMinimumSectionSize(56)


class StockRankingWidget(QWidget):
    """Sortable ranking table + selected-stock drill-down for one backtest.

    Signals:
        symbol_focused: emitted with the symbol when a ranked row is selected,
            so the trade blotter filter can agree with the ranking.
    """

    symbol_focused = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Own background: never depend on the host chain for the base fill.
        self.setStyleSheet(f"background: {t.BG1};")
        self._universe: tuple[str, ...] = ()
        self._base: Any | None = None
        self._errors: dict[str, str] = {}
        self._last_run: tuple[str, ...] | None = None
        self._mode_label = ""
        self._rows: tuple[StockRankRow, ...] = ()
        self._visible: tuple[StockRankRow, ...] = ()
        self._views: dict[str, Any] = {}
        self._selected: str | None = None
        self._needle = ""
        self._sort_col = _COL_NET
        self._sort_desc = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_LG, t.SP_MD, t.SP_LG, t.SP_MD)
        lay.setSpacing(t.SP_SM)
        # ── identity row: what this surface is ──
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(t.SP_MD)
        self._title = QLabel("STOCK RANKING", self)
        self._title.setStyleSheet(t.section_title(13))
        head_row.addWidget(self._title)
        head_row.addStretch(1)
        self._mode = QLabel("", self)
        self._mode.setStyleSheet(
            f"color: {t.ACCENT}; font-size: {t.FS_SMALL}px; font-weight: 700;"
            f" border: 1px solid {t.ACCENT}; border-radius: 3px; padding: 2px 9px;"
        )
        self._mode.setVisible(False)
        head_row.addWidget(self._mode)
        lay.addLayout(head_row)
        # ── controls row: scope, criterion, search ──
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(t.SP_MD)
        self._scope = QLabel("", self)
        self._scope.setStyleSheet(t.body(t.FS_SMALL, t.TEXT2))
        ctrl_row.addWidget(self._scope)
        ctrl_row.addStretch(1)
        rank_lab = QLabel("Rank by:", self)
        rank_lab.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        ctrl_row.addWidget(rank_lab)
        self._sort_box = QComboBox(self)
        self._sort_box.setStyleSheet(t.INPUT_QSS)
        for label, _col in _SORT_OPTIONS:
            self._sort_box.addItem(label)
        self._sort_box.currentIndexChanged.connect(self._on_sort_box)
        ctrl_row.addWidget(self._sort_box)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search stocks…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(t.INPUT_QSS)
        self._search.setFixedWidth(180)
        self._search.setToolTip("Filter the visible rows — ranks are never renumbered")
        self._search.textChanged.connect(self._on_search)
        ctrl_row.addWidget(self._search)
        lay.addLayout(ctrl_row)

        self._empty = QLabel(
            "NO SYMBOLS SELECTED\nManage Symbols in Backtest Configuration to choose "
            "the universe to rank.",
            self,
        )
        self._empty.setStyleSheet(t.body(t.FS_BODY, t.MUTED))
        self._empty.setWordWrap(True)
        lay.addWidget(self._empty)

        # ── table + drill-down in one adjustable vertical splitter ──
        self._split = QSplitter(Qt.Orientation.Vertical, self)
        self._split.setStyleSheet(t.SPLITTER_QSS)
        self._split.setHandleWidth(1)
        self._split.setChildrenCollapsible(False)

        table_host = QWidget(self._split)
        table_lay = QVBoxLayout(table_host)
        table_lay.setContentsMargins(0, 0, 0, 0)
        table_lay.setSpacing(t.SP_XS)
        self._table = QTableWidget(table_host)
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.setStyleSheet(t.TABLE_QSS + f" {t.SCROLLBAR_QSS}")
        # No stretch-last-section: it absorbs all free viewport width into
        # the final column and opens a dead gap mid-table. Columns pack
        # left at content width; leftover viewport stays empty at the edge.
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setHighlightSections(False)
        # Sticky header: the header widget never scrolls with the rows.
        self._table.setMinimumHeight(120)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.currentCellChanged.connect(self._on_current_cell_changed)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        table_lay.addWidget(self._table)
        self._foot = QLabel("", table_host)
        self._foot.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        self._foot.setWordWrap(True)
        table_lay.addWidget(self._foot)
        self._split.addWidget(table_host)

        self.detail = _SelectedStockPanel(self._split)
        # The drill-down lives in its own scroll region (spec §28): the
        # ranking table keeps its rows while the analytical surface below
        # can never push the table out of the viewport.
        self._detail_scroll = QScrollArea(self._split)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._detail_scroll.setStyleSheet(
            f"QScrollArea {{ background: {t.BG1}; border: none; }} {t.SCROLLBAR_QSS}"
        )
        self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail_scroll.setMinimumHeight(110)
        self._detail_scroll.setWidget(self.detail)
        self._split.addWidget(self._detail_scroll)
        self._split.setStretchFactor(0, 3)
        self._split.setStretchFactor(1, 2)
        self._split.setSizes([300, 240])
        lay.addWidget(self._split, 1)

        # Backwards-compatible aliases (the drill-down used to live inline).
        self._detail_title = self.detail.title
        self._detail_sub = self.detail.sub
        self._detail_metrics = self.detail.metrics
        self._detail_caption = self.detail.caption
        self._spark = self.detail.spark

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._refresh()

    # ── public API ──────────────────────────────────────────────

    @property
    def universe(self) -> tuple[str, ...]:
        """The current selected-symbol universe backing the ranking."""
        return self._universe

    def set_universe(self, symbols: tuple[str, ...] | list[str]) -> None:
        """Replace the ranking universe (selection order, watchlist-only)."""
        wanted = tuple(symbols)
        if wanted == self._universe:
            return
        self._universe = wanted
        self._refresh()

    def set_results(
        self,
        base: Any | None,
        errors: dict[str, str] | None = None,
        last_run: tuple[str, ...] | list[str] | None = None,
        mode_label: str = "",
    ) -> None:
        """Push a mode-consistent result plus batch errors and last RUN symbols."""
        self._base = base
        self._errors = dict(errors) if errors else {}
        self._last_run = tuple(last_run) if last_run is not None else None
        self._mode_label = mode_label or ""
        self._refresh()

    def clear_results(self) -> None:
        """Forget results but keep the universe (Lab reset semantics)."""
        self._base = None
        self._errors = {}
        self._last_run = None
        self._mode_label = ""
        self._selected = None
        self._views = {}
        self._refresh()

    def rank_for(self, symbol: str) -> int | None:
        """The displayed rank for *symbol* (None = unranked)."""
        for row in self._rows:
            if row.symbol == symbol:
                return row.rank
        return None

    def row_status(self, symbol: str) -> str | None:
        """The status for *symbol* (None when symbol not in universe)."""
        for row in self._rows:
            if row.symbol == symbol:
                return row.status
        return None

    def ordered_symbols(self) -> tuple[str, ...]:
        """Symbols in current display order (ranked first, then unranked)."""
        return tuple(row.symbol for row in self._rows)

    def rows(self) -> tuple[StockRankRow, ...]:
        """Current ranking rows (ranked first, NET P&L descending).

        Shared with the COMPARE board so both surfaces read the same
        single derivation pass — never a second scan of merged trades.
        """
        return self._rows

    @property
    def selected_symbol(self) -> str | None:
        """The stock currently under investigation (None = none)."""
        return self._selected

    def select_symbol(self, symbol: str) -> bool:
        """Programmatically select a symbol (returns False when not present)."""
        for index, row in enumerate(self._visible):
            if row.symbol == symbol:
                self._select_row(index, emit=False)
                return True
        return False

    # ── internals ───────────────────────────────────────────────

    def _display_rows(self) -> tuple[StockRankRow, ...]:
        rows, views = _build_ranking(self._base, self._universe, self._errors, self._last_run)
        self._views = views
        ranked = [row for row in rows if row.status == "ranked"]
        unranked = [row for row in rows if row.status != "ranked"]
        key = _SORT_KEYS.get(self._sort_col, "net_profit")
        if key == "symbol":
            ranked.sort(key=lambda row: row.symbol, reverse=self._sort_desc)
        elif key == "net_profit":
            ranked.sort(
                key=lambda row: (float(row.net_profit or 0.0), row.symbol),
                reverse=self._sort_desc,
            )
        elif key == "return_pct":
            ranked.sort(
                key=lambda row: (float(row.return_pct or 0.0), row.symbol),
                reverse=self._sort_desc,
            )
        elif key == "total_trades":
            ranked.sort(
                key=lambda row: (int(row.total_trades or 0), row.symbol),
                reverse=self._sort_desc,
            )
        elif key == "win_rate":
            ranked.sort(
                key=lambda row: (float(row.win_rate if row.win_rate is not None else -1.0),),
                reverse=self._sort_desc,
            )
        elif key == "profit_factor":
            pf_missing = -1.0
            ranked.sort(
                key=lambda row: (
                    float(row.profit_factor if row.profit_factor is not None else pf_missing),
                ),
                reverse=self._sort_desc,
            )
        elif key == "max_drawdown_pct":
            # Lower drawdown is better — default ascending.
            ranked.sort(
                key=lambda row: (float(row.max_drawdown_pct or 0.0), row.symbol),
                reverse=self._sort_desc,
            )
        elif key == "sharpe_ratio":
            sh_missing = -1e9
            ranked.sort(
                key=lambda row: (
                    float(row.sharpe_ratio if row.sharpe_ratio is not None else sh_missing),
                ),
                reverse=self._sort_desc,
            )
        out: list[StockRankRow] = []
        for index, row in enumerate(ranked, start=1):
            if row.rank == index:
                out.append(row)
            else:
                out.append(
                    StockRankRow(
                        symbol=row.symbol,
                        rank=index,
                        net_profit=row.net_profit,
                        return_pct=row.return_pct,
                        total_trades=row.total_trades,
                        win_rate=row.win_rate,
                        profit_factor=row.profit_factor,
                        max_drawdown_pct=row.max_drawdown_pct,
                        sharpe_ratio=row.sharpe_ratio,
                        status=row.status,
                        note=row.note,
                    )
                )
        out.extend(unranked)
        return tuple(out)

    def _refresh(self) -> None:
        self._rows = self._display_rows()
        # Search applies to the display only — global ranks stay intact.
        needle = self._needle.strip().lower()
        if needle:
            self._visible = tuple(r for r in self._rows if needle in r.symbol.lower())
        else:
            self._visible = self._rows
        if self._selected is not None and self._selected not in self._universe:
            self._selected = None
        has_universe = bool(self._universe)
        count = len(self._universe)
        noun = "stock" if count == 1 else "stocks"
        # Direction chip: the active side, impossible to miss.
        direction = (self._mode_label or "").replace(" — ", " · ")
        self._mode.setText(direction)
        self._mode.setVisible(bool(direction))
        self._scope.setText(f"{count} {noun} analyzed" if has_universe else "")
        self._sync_sort_box()
        self._empty.setVisible(not has_universe)
        self._split.setVisible(has_universe)
        headers = list(_HEADERS)
        arrow = " ▼" if self._sort_desc else " ▲"
        if 0 <= self._sort_col < len(headers):
            headers[self._sort_col] = headers[self._sort_col] + arrow
        self._table.setHorizontalHeaderLabels(headers)
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        if not has_universe:
            self._table.blockSignals(False)
            self._foot.setText("")
            self.detail.show_empty()
            return
        self._table.setRowCount(len(self._visible))
        for row_idx, row in enumerate(self._visible):
            self._fill_row(row_idx, row)
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()
        for row_idx in range(self._table.rowCount()):
            self._table.setRowHeight(row_idx, _ROW_HEIGHT)
        self._table.horizontalHeader().setMinimumSectionSize(64)
        ranked_count = sum(1 for row in self._rows if row.status == "ranked")
        pending = sum(1 for row in self._rows if row.status != "ranked")
        if self._base is None or self._last_run is None:
            self._foot.setText(
                "BACKTEST NOT RUN — run the backtest to analyze the selected universe."
            )
        elif ranked_count == 0 and pending:
            self._foot.setText("NO VALID RESULTS — no selected stock produced a usable result.")
        else:
            self._foot.setText(
                f"Ranked by {self._criterion_label()} · — = no valid result · "
                "Select a row to investigate · Click a header to sort."
            )
        self._refresh_details()

    def _fill_row(self, row_idx: int, row: StockRankRow) -> None:
        ranked = row.status == "ranked"
        rank_text = str(row.rank) if row.rank is not None else "—"
        if row.net_profit is None:
            net_text, net_color = "—", t.MUTED
        else:
            net_text = f"₹{_format_inr(row.net_profit)}"
            net_color = t.semantic(row.net_profit)
        if row.return_pct is None:
            ret_text, ret_color = "—", t.MUTED
        else:
            ret_text = f"{row.return_pct:+.2f}%"
            ret_color = t.semantic(row.return_pct)
        trades_text = str(row.total_trades) if row.total_trades is not None else "—"
        win_text = f"{row.win_rate * 100:.1f}%" if row.win_rate is not None else "—"
        if row.profit_factor is None:
            pf_text, pf_color = "—", t.MUTED
        else:
            pf_text = f"{row.profit_factor:.2f}"
            pf_color = t.POS if row.profit_factor > 1 else t.NEG
        dd_text = "—" if row.max_drawdown_pct is None else f"-{row.max_drawdown_pct:.2f}%"
        sh_text = f"{row.sharpe_ratio:.2f}" if row.sharpe_ratio is not None else "—"
        texts = (
            rank_text,
            row.symbol,
            net_text,
            ret_text,
            trades_text,
            win_text,
            pf_text,
            dd_text,
            sh_text,
        )
        for col_idx, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(text)
            primary = col_idx in _PRIMARY_COLS
            if col_idx == _COL_SYMBOL:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            elif col_idx == _COL_RANK:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # ── visual priority: symbol / net / return scan first (§13) ──
            # Explicit pixel sizes (never the app default) so the hierarchy
            # survives any host stylesheet.
            font = QFont()
            font.setPixelSize(t.FS_TABLE if primary else t.FS_SMALL)
            font.setBold(primary and ranked)
            item.setFont(font)
            if col_idx == _COL_RANK:
                item.setForeground(QColor(t.TEXT2 if ranked else t.MUTED))
                item.setBackground(_rank_tint(row))
            elif col_idx == _COL_SYMBOL:
                item.setForeground(QColor(t.TEXT if ranked else t.MUTED))
            elif col_idx == _COL_NET:
                item.setForeground(QColor(net_color))
            elif col_idx == _COL_RET:
                item.setForeground(QColor(ret_color))
            elif col_idx == _COL_PF and ranked and row.profit_factor is not None:
                item.setForeground(QColor(pf_color))
            else:
                item.setForeground(QColor(t.TEXT2 if ranked else t.MUTED))
            if row.note:
                item.setToolTip(f"{row.symbol} — {row.note}")
            elif ranked and row.rank in (1, 2, 3):
                item.setToolTip(f"Rank {row.rank} by {self._criterion_label()}")
            self._table.setItem(row_idx, col_idx, item)

    def _criterion_label(self) -> str:
        for label, col in _SORT_OPTIONS:
            if col == self._sort_col:
                return label
        return "Net P&L"

    def _sync_sort_box(self) -> None:
        for index, (_label, col) in enumerate(_SORT_OPTIONS):
            if col == self._sort_col:
                self._sort_box.blockSignals(True)
                try:
                    self._sort_box.setCurrentIndex(index)
                finally:
                    self._sort_box.blockSignals(False)
                return

    def _refresh_details(self) -> None:
        """Selected-stock workspace: metrics + analytical tabs, or empty state."""
        symbol = self._selected
        view = self._views.get(symbol) if symbol else None
        row = next((r for r in self._rows if r.symbol == symbol), None) if symbol else None
        if symbol is None or row is None:
            self.detail.show_empty()
            return
        if row.status != "ranked" or view is None:
            self.detail.show_unranked(symbol, row.note)
            return
        self.detail.show_row(row, view, self._criterion_label(), self._mode_label)

    def _on_header_clicked(self, logical: int) -> None:
        key = _SORT_KEYS.get(logical)
        if key is None:
            return
        if logical == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = logical
            # Lower drawdown is better — default ascending; everything else descending.
            self._sort_desc = key != "max_drawdown_pct" and key != "symbol"
            if key == "symbol":
                self._sort_desc = False
        self._refresh()

    def _on_sort_box(self, index: int) -> None:
        """Rank-by dropdown: same single sort state as header clicks."""
        if 0 <= index < len(_SORT_OPTIONS):
            _label, col = _SORT_OPTIONS[index]
            if col != self._sort_col:
                self._sort_col = col
                key = _SORT_KEYS.get(col, "net_profit")
                self._sort_desc = key != "max_drawdown_pct" and key != "symbol"
                if key == "symbol":
                    self._sort_desc = False
                self._refresh()

    def _on_search(self, text: str) -> None:
        self._needle = text or ""
        self._refresh()

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        self._select_row(row, emit=True)

    def _on_current_cell_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        """Arrow-key navigation selects the focused row (spec §32)."""
        if row >= 0 and row != _prev_row:
            self._select_row(row, emit=True)

    def _select_row(self, row: int, *, emit: bool) -> None:
        if not (0 <= row < len(self._visible)):
            return
        symbol = self._visible[row].symbol
        if not symbol:
            return
        changed = symbol != self._selected
        self._selected = symbol
        if self._table.currentRow() != row:
            self._table.blockSignals(True)
            try:
                self._table.selectRow(row)
                self._table.setCurrentCell(row, _COL_SYMBOL)
            finally:
                self._table.blockSignals(False)
        self._refresh_details()
        if emit and changed:
            self.symbol_focused.emit(symbol)


def _rank_tint(row: StockRankRow) -> QColor:
    """Restrained ranking signal — a faint band, never a rainbow (§14).

    Positive rows carry a low-alpha positive wash, negative rows a low-alpha
    negative wash. The sign is always also present as text and colour, so
    colour is never the only signal (§33).
    """
    if row.status != "ranked" or row.net_profit is None:
        return QColor(0, 0, 0, 0)
    base = QColor(t.POS if row.net_profit >= 0 else t.NEG)
    base.setAlpha(34 if row.rank is not None and row.rank <= 3 else 20)
    return base


__all__ = ["StockRankingWidget", "StockRankRow", "build_stock_ranking"]
