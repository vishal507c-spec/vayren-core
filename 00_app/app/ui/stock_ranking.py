"""Stock Ranking — compact institutional ranking for selected backtest stocks.

Ranking covers ONLY the currently selected Watchlist symbols (never the full
NSE universe). All numbers come from the existing multi-symbol backtest
results via :func:`derive_symbol_result` — no new metric formulas, no
fabrication. Symbols without a valid result render as unavailable ("—")
instead of receiving an invented rank.

Presentation only: no bus, no SQL, no events. Follows the lab theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.engine.directional import derive_symbol_results
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t

_COL_RANK = 0
_COL_SYMBOL = 1
_COL_NET = 2
_COL_TRADES = 3
_COL_WIN = 4
_COL_PF = 5
_COL_DD = 6
_COL_SHARPE = 7

_HEADERS: tuple[str, ...] = ("#", "SYMBOL", "NET P&L", "TRADES", "WIN%", "PF", "MAX DD", "SHARPE")

_SORT_KEYS: dict[int, str] = {
    _COL_RANK: "net_profit",
    _COL_SYMBOL: "symbol",
    _COL_NET: "net_profit",
    _COL_TRADES: "total_trades",
    _COL_WIN: "win_rate",
    _COL_PF: "profit_factor",
    _COL_DD: "max_drawdown_pct",
    _COL_SHARPE: "sharpe_ratio",
}


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
    total_trades: int | None
    win_rate: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    status: str
    note: str = ""


def build_stock_ranking(
    base: Any | None,
    symbols: tuple[str, ...] | list[str],
    errors: dict[str, str] | None = None,
    last_run: tuple[str, ...] | list[str] | None = None,
) -> tuple[StockRankRow, ...]:
    """Build ranking rows for *symbols* from an existing backtest result.

    Args:
        base: Mode-consistent result (BUY → LONG split, SELL → SHORT split,
            COMPARE → full). None means no backtest yet — every row is pending.
        symbols: Selected Watchlist symbols in selection order (the ONLY universe).
        errors: Per-symbol failure reasons from the batch coordinator ({} = none).
        last_run: Symbols attempted by the last completed RUN (None = never ran).
            Selected symbols absent from *last_run* render as pending ("run again")
            instead of borrowing a fake zero-trade rank from an older run.

    Returns:
        Rows with ranked entries first (net_profit descending, 1st = best),
        then unranked entries in selection order. Never invents numbers:
        unavailable / pending / no-trade rows carry None metrics and no rank.
    """
    ordered = tuple(symbols)
    if not ordered:
        return ()
    failed: dict[str, str] = dict(errors) if errors else {}
    attempted: tuple[str, ...] | None = tuple(last_run) if last_run is not None else None
    if base is None or attempted is None:
        return tuple(
            StockRankRow(
                symbol=symbol,
                rank=None,
                net_profit=None,
                total_trades=None,
                win_rate=None,
                profit_factor=None,
                max_drawdown_pct=None,
                sharpe_ratio=None,
                status="pending",
                note="No backtest yet — run backtest",
            )
            for symbol in ordered
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
                total_trades=int(metrics.total_trades),
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                max_drawdown_pct=float(metrics.max_drawdown_pct),
                sharpe_ratio=metrics.sharpe_ratio,
                status="ranked",
            )
        )
    out.extend(unranked)
    return tuple(out)


class StockRankingWidget(QWidget):
    """Compact sortable ranking table for the selected backtest stocks.

    Signals:
        symbol_focused: emitted with the symbol when a ranked row is clicked,
            so the trade blotter filter can agree with the ranking.
    """

    symbol_focused = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._universe: tuple[str, ...] = ()
        self._base: Any | None = None
        self._errors: dict[str, str] = {}
        self._last_run: tuple[str, ...] | None = None
        self._mode_label = ""
        self._rows: tuple[StockRankRow, ...] = ()
        self._sort_col = _COL_NET
        self._sort_desc = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(6)
        self._title = QLabel("STOCK RANKING", self)
        self._title.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        head_row.addWidget(self._title)
        head_row.addStretch(1)
        self._mode = QLabel("", self)
        self._mode.setStyleSheet(f"color: {t.MUTED}; font-size: 10px; font-weight: 600;")
        head_row.addWidget(self._mode)
        lay.addLayout(head_row)

        self._empty = QLabel("No symbols selected — add from your Market Watchlist.", self)
        self._empty.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        self._empty.setWordWrap(True)
        lay.addWidget(self._empty)

        self._table = QTableWidget(self)
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        self._table.setStyleSheet(t.TABLE_QSS + f" {t.SCROLLBAR_QSS}")
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMaximumHeight(220)
        self._table.setMinimumHeight(88)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        lay.addWidget(self._table)

        self._foot = QLabel("", self)
        self._foot.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")
        self._foot.setWordWrap(True)
        lay.addWidget(self._foot)
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

    # ── internals ───────────────────────────────────────────────

    def _display_rows(self) -> tuple[StockRankRow, ...]:
        built = build_stock_ranking(self._base, self._universe, self._errors, self._last_run)
        ranked = [row for row in built if row.status == "ranked"]
        unranked = [row for row in built if row.status != "ranked"]
        key = _SORT_KEYS.get(self._sort_col, "net_profit")
        if key == "symbol":
            ranked.sort(key=lambda row: row.symbol, reverse=self._sort_desc)
        elif key == "net_profit":
            ranked.sort(
                key=lambda row: (float(row.net_profit or 0.0), row.symbol),
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
        has_universe = bool(self._universe)
        self._empty.setVisible(not has_universe)
        self._table.setVisible(has_universe)
        self._foot.setVisible(has_universe)
        if self._mode_label:
            count = len(self._universe)
            noun = "stock" if count == 1 else "stocks"
            self._mode.setText(f"{self._mode_label} · {count} {noun}")
        else:
            count = len(self._universe)
            noun = "stock" if count == 1 else "stocks"
            self._mode.setText(f"{count} {noun}" if has_universe else "")
        headers = list(_HEADERS)
        arrow = " ▼" if self._sort_desc else " ▲"
        if 0 <= self._sort_col < len(headers):
            headers[self._sort_col] = headers[self._sort_col] + arrow
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(0)
        if not has_universe:
            self._foot.setText("")
            return
        self._table.setRowCount(len(self._rows))
        for row_idx, row in enumerate(self._rows):
            ranked = row.status == "ranked"
            rank_text = str(row.rank) if row.rank is not None else "—"
            if row.net_profit is None:
                net_text = "—"
                net_color = t.MUTED
            else:
                net_text = f"₹{_format_inr(row.net_profit)}"
                net_color = t.POS if row.net_profit >= 0 else t.NEG
            trades_text = str(row.total_trades) if row.total_trades is not None else "—"
            win_text = f"{row.win_rate * 100:.1f}%" if row.win_rate is not None else "—"
            pf_text = f"{row.profit_factor:.2f}" if row.profit_factor is not None else "—"
            dd_text = "—" if row.max_drawdown_pct is None else f"-{row.max_drawdown_pct:.2f}%"
            sh_text = f"{row.sharpe_ratio:.2f}" if row.sharpe_ratio is not None else "—"
            texts = (
                rank_text,
                row.symbol,
                net_text,
                trades_text,
                win_text,
                pf_text,
                dd_text,
                sh_text,
            )
            for col_idx, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col_idx == _COL_SYMBOL:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                from PySide6.QtGui import QColor, QFont

                if col_idx == _COL_RANK:
                    item.setForeground(QColor(t.TEXT if ranked else t.MUTED))
                    font = QFont()
                    font.setBold(ranked)
                    item.setFont(font)
                elif col_idx == _COL_SYMBOL:
                    item.setForeground(QColor(t.TEXT))
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                elif col_idx == _COL_NET:
                    item.setForeground(QColor(net_color))
                    font = QFont()
                    font.setBold(ranked)
                    item.setFont(font)
                else:
                    item.setForeground(QColor(t.TEXT if ranked else t.MUTED))
                if row.note:
                    item.setToolTip(f"{row.symbol} — {row.note}")
                elif ranked and row.rank in (1, 2, 3):
                    item.setToolTip(f"Rank {row.rank} by net P&L")
                self._table.setItem(row_idx, col_idx, item)
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()
        for row_idx in range(self._table.rowCount()):
            self._table.setRowHeight(row_idx, 22)
        ranked_count = sum(1 for row in self._rows if row.status == "ranked")
        pending = sum(1 for row in self._rows if row.status == "pending")
        missing = sum(1 for row in self._rows if row.status in ("unavailable", "no_trades"))
        if self._base is None or self._last_run is None:
            self._foot.setText("Run backtest to rank — 1st = best net P&L.")
        elif ranked_count == 0 and (pending or missing):
            self._foot.setText("— = no valid result · Run backtest to rank.")
        else:
            self._foot.setText(
                "— = no valid result · Green profit / Red loss · Click header to sort."
            )

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

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._rows):
            symbol = self._rows[row].symbol
            if symbol:
                self.symbol_focused.emit(symbol)


__all__ = ["StockRankingWidget", "StockRankRow", "build_stock_ranking"]
