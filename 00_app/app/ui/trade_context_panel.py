"""TradeContextPanel — compact institutional trade inspector for chart workspace.

Shows the single selected trade's exact context without covering the chart.
Follows VAYREN dark terminal palette; chart remains dominant.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t

_BUY = "#00C7B7"
_SELL = "#F05A67"


def _inr(value: float) -> str:
    neg = value < 0
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
    return ("-" if neg else "") + text


class TradeContextPanel(QWidget):
    """Compact strip + detail card for the focused trade.

    Layout (single row + expandable detail): trade id, symbol/side, entry, exit,
    P&L, R-multiple and an OPEN IN MARKET escape hatch in one compact strip,
    plus an entry/exit detail row. Chart remains the primary focus.

    Hidden when no trade is focused; shows a brief loading state when data is being fetched.
    """

    open_in_market = Signal()
    prev_trade = Signal()
    next_trade = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TradeContextPanel")
        self.setStyleSheet(
            "QWidget#TradeContextPanel "
            f"{{ background: {t.BG1}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # top strip — always visible when a trade is active
        self._strip = QWidget(self)
        self._strip.setObjectName("TradeStrip")
        self._strip.setStyleSheet(f"background: {t.BG0}; border-bottom: 1px solid {t.BORDER};")
        self._strip.setFixedHeight(28)
        s_lay = QHBoxLayout(self._strip)
        s_lay.setContentsMargins(10, 2, 10, 2)
        s_lay.setSpacing(8)

        self._trade_label = QLabel("", self._strip)
        self._trade_label.setStyleSheet(f"color: {t.TEXT}; font-size: 11px; font-weight: 700;")

        self._symbol_label = QLabel("", self._strip)
        self._symbol_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px; font-weight: 600;")

        self._time_label = QLabel("", self._strip)
        self._time_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")

        self._pnl_label = QLabel("", self._strip)
        self._pnl_label.setStyleSheet(f"color: {t.TEXT}; font-size: 11px; font-weight: 700;")

        self._r_label = QLabel("", self._strip)
        self._r_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")

        # subtle prev/next
        self._prev_btn = QPushButton("← PREV", self._strip)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.setStyleSheet(t.TOOL_QSS)
        self._prev_btn.setFixedHeight(22)
        self._prev_btn.clicked.connect(self.prev_trade.emit)

        self._next_btn = QPushButton("NEXT →", self._strip)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(t.TOOL_QSS)
        self._next_btn.setFixedHeight(22)
        self._next_btn.clicked.connect(self.next_trade.emit)

        self._open_btn = QPushButton("OPEN IN MARKET ↗", self._strip)
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.setStyleSheet(t.BUTTON_QSS)
        self._open_btn.setFixedHeight(22)
        self._open_btn.clicked.connect(self.open_in_market.emit)

        s_lay.addWidget(self._trade_label)
        s_lay.addWidget(self._symbol_label)
        s_lay.addWidget(self._time_label)
        s_lay.addWidget(self._pnl_label)
        s_lay.addWidget(self._r_label)
        s_lay.addStretch(1)
        s_lay.addWidget(self._prev_btn)
        s_lay.addWidget(self._next_btn)
        s_lay.addWidget(self._open_btn)
        lay.addWidget(self._strip)

        # detail row — entry/exit/pnl/r/bars/reason
        self._detail = QWidget(self)
        self._detail.setObjectName("TradeDetail")
        self._detail.setStyleSheet(
            f"QWidget#TradeDetail {{ background: {t.BG1}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        d_lay = QHBoxLayout(self._detail)
        d_lay.setContentsMargins(10, 4, 10, 4)
        d_lay.setSpacing(14)
        self._entry_label = QLabel("", self._detail)
        self._entry_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 10px;")
        self._entry_label.setTextFormat(Qt.TextFormat.RichText)
        self._exit_label = QLabel("", self._detail)
        self._exit_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 10px;")
        self._exit_label.setTextFormat(Qt.TextFormat.RichText)
        self._meta_label = QLabel("", self._detail)
        self._meta_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")
        self._meta_label.setTextFormat(Qt.TextFormat.RichText)

        sep1 = QFrame(self._detail)
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet(f"color: {t.BORDER}; background: {t.BORDER};")
        sep1.setFixedWidth(1)
        sep2 = QFrame(self._detail)
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {t.BORDER}; background: {t.BORDER};")
        sep2.setFixedWidth(1)

        d_lay.addWidget(self._entry_label)
        d_lay.addWidget(sep1)
        d_lay.addWidget(self._exit_label)
        d_lay.addWidget(sep2)
        d_lay.addWidget(self._meta_label)
        d_lay.addStretch(1)
        lay.addWidget(self._detail)

        # hidden initially — no trade context
        self._detail.setVisible(False)
        self.setVisible(False)
        self._loading = False

    def show_loading(self, message: str = "Loading historical context…") -> None:
        self._loading = True
        self.setVisible(True)
        self._detail.setVisible(False)
        self._strip.setVisible(True)
        self._trade_label.setText(message)
        self._trade_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px; font-style: italic;")
        self._symbol_label.setText("")
        self._time_label.setText("")
        self._pnl_label.setText("")
        self._r_label.setText("")
        self._open_btn.setVisible(False)
        self._prev_btn.setVisible(False)
        self._next_btn.setVisible(False)

    def show_error(self, message: str = "Historical data unavailable for this trade.") -> None:
        self._loading = False
        self.setVisible(True)
        self._detail.setVisible(True)
        self._strip.setVisible(True)
        self._trade_label.setText(message)
        self._trade_label.setStyleSheet(f"color: {t.WARN}; font-size: 10px;")
        self._symbol_label.setText("")
        self._time_label.setText("")
        self._pnl_label.setText("")
        self._r_label.setText("")
        self._entry_label.setText("")
        self._exit_label.setText("")
        self._meta_label.setText("")
        self._open_btn.setVisible(True)

    def set_trade(
        self,
        trade_index: int,
        symbol: str,
        side: str,
        timeframe: str,
        entry_time: str,
        entry_price: float,
        exit_time: str,
        exit_price: float,
        pnl: float,
        r_multiple: float | None,
        bars_held: int | None = None,
        exit_reason: str | None = None,
        has_prev: bool = True,
        has_next: bool = True,
    ) -> None:
        self._loading = False
        self.setVisible(True)
        self._detail.setVisible(True)
        self._strip.setVisible(True)
        self._open_btn.setVisible(True)
        self._prev_btn.setVisible(True)
        self._next_btn.setVisible(True)

        side_up = side.upper()
        is_long = side_up == "LONG"
        side_tag = "LONG" if is_long else "SHORT"
        side_color = _BUY if is_long else _SELL

        self._trade_label.setText(f"TRADE #{trade_index}")
        self._trade_label.setStyleSheet(f"color: {t.TEXT}; font-size: 11px; font-weight: 800;")
        self._symbol_label.setText(f"{symbol} · {side_tag}")
        self._symbol_label.setStyleSheet(f"color: {side_color}; font-size: 11px; font-weight: 700;")
        # header time = entry date
        try:
            head_time = entry_time[:16].replace("T", " ")
        except Exception:
            head_time = entry_time[:16]
        self._time_label.setText(f"{timeframe} · {head_time}")
        self._time_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")

        pnl_color = t.POS if pnl >= 0 else t.NEG
        sign = "+" if pnl >= 0 else ""
        self._pnl_label.setText(
            f"{sign}₹{_inr(abs(pnl)) if pnl < 0 else _inr(pnl)}" if pnl != 0 else f"₹{_inr(pnl)}"
        )
        # ensure sign for negative handled by _inr
        if pnl < 0:
            self._pnl_label.setText(f"₹{_inr(pnl)}")
        elif pnl > 0:
            self._pnl_label.setText(f"+₹{_inr(pnl)}")
        else:
            self._pnl_label.setText(f"₹{_inr(pnl)}")
        self._pnl_label.setStyleSheet(f"color: {pnl_color}; font-size: 11px; font-weight: 700;")

        if r_multiple is not None:
            r_color = t.POS if r_multiple >= 0 else t.NEG
            self._r_label.setText(f"{r_multiple:+.2f}R")
            self._r_label.setStyleSheet(f"color: {r_color}; font-size: 10px; font-weight: 700;")
            self._r_label.setVisible(True)
        else:
            self._r_label.setVisible(False)

        # detail row
        try:
            e_disp = entry_time[:16].replace("T", " · ")
            x_disp = exit_time[:16].replace("T", " · ")
        except Exception:
            e_disp = entry_time[:16]
            x_disp = exit_time[:16]
        self._entry_label.setText(
            f"<span style='color:{t.MUTED};'>ENTRY</span> "
            f"<span style='color:{t.TEXT}; font-weight:600;'>{e_disp}</span> "
            f"<span style='color:{side_color}; font-weight:700;'>₹{entry_price:,.2f}</span>"
        )
        exit_tag = "SELL" if is_long else "COVER"
        self._exit_label.setText(
            f"<span style='color:{t.MUTED};'>EXIT {exit_tag}</span> "
            f"<span style='color:{t.TEXT}; font-weight:600;'>{x_disp}</span> "
            f"<span style='color:{t.TEXT}; font-weight:700;'>₹{exit_price:,.2f}</span>"
        )
        meta_parts: list[str] = []
        if bars_held is not None:
            meta_parts.append(f"<span style='color:{t.TEXT2};'>{bars_held} bars</span>")
        if exit_reason:
            meta_parts.append(f"<span style='color:{t.MUTED};'>{exit_reason}</span>")
        self._meta_label.setText(" · ".join(meta_parts))
        self._meta_label.setVisible(bool(meta_parts))

        self._prev_btn.setEnabled(has_prev)
        self._next_btn.setEnabled(has_next)

    def clear(self) -> None:
        self.setVisible(False)
        self._loading = False
        self._detail.setVisible(False)

    def set_nav_enabled(self, has_prev: bool, has_next: bool) -> None:
        self._prev_btn.setEnabled(has_prev)
        self._next_btn.setEnabled(has_next)
