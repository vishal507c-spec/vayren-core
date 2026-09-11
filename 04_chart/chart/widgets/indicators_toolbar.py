"""IndicatorsToolbar — single INDICATORS control for chart.

TradingView-style: one button at top of chart opens a clean searchable panel
with categories ALL / TREND / MOMENTUM / VOLUME / VOLATILITY / STRATEGIES.
Strategies are loaded from the canonical strategy library (resolved from the
CLI/env/config root) via the existing storage/version-control system —
no duplicate files, no .py strategies.

Pure UI: emits strategy_selected(name) or indicator_selected(name, category);
the host (ChartWindow) turns the strategy selection into a RunBacktest
via the existing EventBus → BacktestWorker → Universal VM pipeline.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chart.theme import APP_PALETTE

# ── Indicator catalogue (static, not strategy-specific) ─────────────────

CATEGORIES: tuple[str, ...] = ("ALL", "TREND", "MOMENTUM", "VOLUME", "VOLATILITY", "STRATEGIES")

INDICATORS: dict[str, tuple[str, ...]] = {
    "TREND": ("SMA", "EMA", "VWAP", "Supertrend"),
    "MOMENTUM": ("RSI", "MACD", "Stochastic"),
    "VOLUME": ("Volume", "OBV"),
    "VOLATILITY": ("Bollinger Bands", "ATR", "ADX"),
}

# Strategies are injected from the app layer (bootstrap) via set_strategies()
# to keep chart independent of the strategy domain (no cross-module import).


class IndicatorsPanel(QFrame):
    """Popup panel: searchable, category-filtered list of indicators + strategies."""

    indicator_selected = Signal(str, str)  # name, category
    strategy_selected = Signal(str)  # name
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)  # type: ignore[call-arg]
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("IndicatorsPanel")
        self.setStyleSheet(
            """
            QFrame#IndicatorsPanel {
                background: palette(base);
                border: 1px solid palette(mid);
                border-radius: 6px;
            }
            QLineEdit {
                background: palette(alternate-base);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 6px 8px;
                font-size: 12px;
                color: palette(text);
            }
            QLineEdit:focus { border: 1px solid palette(highlight); }
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-radius: 3px;
                margin: 1px 4px;
            }
            QListWidget::item:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            QListWidget::item:hover:!selected {
                background: palette(alternate-base);
            }
            QLabel#SectionLabel {
                color: palette(placeholder-text);
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.6px;
            }
            """
        )
        self.setFixedSize(360, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Search
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search indicators & strategies…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refilter)
        lay.addWidget(self._search)

        # Category bar
        cat_row = QWidget(self)
        cat_lay = QHBoxLayout(cat_row)
        cat_lay.setContentsMargins(0, 0, 0, 0)
        cat_lay.setSpacing(4)
        self._cat_group = QButtonGroup(cat_row)
        self._cat_group.setExclusive(True)
        self._cat_buttons: dict[str, QPushButton] = {}
        for cat in CATEGORIES:
            btn = QPushButton(cat, cat_row)
            btn.setCheckable(True)
            btn.setChecked(cat == "ALL")
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                """
                QPushButton {
                    background: transparent;
                    border: 1px solid palette(mid);
                    border-radius: 3px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: 600;
                    color: palette(text);
                }
                QPushButton:checked {
                    background: palette(highlight);
                    color: palette(highlighted-text);
                    border: 1px solid palette(highlight);
                }
                QPushButton:hover:!checked {
                    background: palette(midlight);
                }
                """
            )
            btn.clicked.connect(self._on_category_clicked)
            cat_lay.addWidget(btn)
            self._cat_group.addButton(btn)
            self._cat_buttons[cat] = btn
        cat_lay.addStretch(1)
        lay.addWidget(cat_row)

        # List
        self._list = QListWidget(self)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list, 1)

        # Footer hint
        hint = QLabel("Indicators and strategies from a single control", self)
        hint.setObjectName("SectionLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        self._current_category = "ALL"
        self._strategy_names: tuple[str, ...] = ()
        self._refresh_items()

    def set_strategies(self, names: tuple[str, ...]) -> None:
        """Inject strategy names from the app layer (bootstrap)."""
        self._strategy_names = tuple(sorted(names))
        self._refresh_items()

    def _on_category_clicked(self) -> None:
        for cat, btn in self._cat_buttons.items():
            if btn.isChecked():
                self._current_category = cat
                break
        self._refilter()

    def refresh_strategies(self) -> None:
        """Refresh view (call on show) — uses last injected strategies."""
        self._refresh_items()

    def _refresh_items(self) -> None:
        text = self._search.text().strip().lower()
        cat = self._current_category
        self._list.clear()

        # Helper to add section
        def add_section(label: str, items: tuple[str, ...]) -> None:
            if cat != "ALL" and cat != label:
                return
            # Filter by search
            filtered = [n for n in items if not text or text in n.lower() or text in label.lower()]
            if not filtered and text:
                filtered = list(items) if text in label.lower() else []  # noqa: SIM108
            if not filtered:
                return
            # Section header as disabled item
            header = QListWidgetItem(f"— {label} —")
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            f = QFont("Segoe UI", 9)
            f.setBold(True)
            header.setFont(f)
            header.setForeground(APP_PALETTE.color(APP_PALETTE.ColorRole.PlaceholderText))  # type: ignore[attr-defined]
            self._list.addItem(header)
            for name in filtered:
                item = QListWidgetItem(f"  {name}")
                item.setData(Qt.ItemDataRole.UserRole, (name, label))
                self._list.addItem(item)

        for c in ("TREND", "MOMENTUM", "VOLUME", "VOLATILITY"):
            add_section(c, INDICATORS[c])

        # Strategies: injected from app layer
        add_section("STRATEGIES", self._strategy_names)

        # If no items after filter, show placeholder
        if self._list.count() == 0:
            placeholder = QListWidgetItem("  No matches")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._list.addItem(placeholder)

    def _refilter(self) -> None:
        self._refresh_items()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        name, category = data
        if category == "STRATEGIES":
            self.strategy_selected.emit(name)
        else:
            self.indicator_selected.emit(name, category)
        self.hide()

    def show_near(self, anchor: QWidget) -> None:
        """Show popup just below the anchor button, left-aligned."""
        self.refresh_strategies()
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        # Keep inside screen: clamp to anchor width
        self.move(pos)
        self.show()
        self._search.setFocus()
        self._search.selectAll()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.closed.emit()


class IndicatorsToolbar(QWidget):
    """Top-of-chart bar with a single INDICATORS control.

    Minimal, institutional, fast — one button, one panel.
    Reuses existing strategy storage and execution pipeline.
    """

    strategy_selected = Signal(str)  # strategy name
    indicator_selected = Signal(str, str)  # name, category

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("IndicatorsToolbar")
        # Single-line, no extra border when embedded in MarketTopBar (parent handles border)
        self.setStyleSheet(
            """
            QWidget#IndicatorsToolbar {
                background: transparent;
                border: none;
            }
            QPushButton#IndicatorsBtn {
                background: transparent;
                border: 1px solid palette(mid);
                border-radius: 3px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.4px;
                color: palette(text);
            }
            QPushButton#IndicatorsBtn:hover {
                background: palette(midlight);
                border: 1px solid palette(midlight);
            }
            QPushButton#IndicatorsBtn:pressed {
                background: palette(mid);
            }
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._btn = QPushButton("INDICATORS", self)
        self._btn.setObjectName("IndicatorsBtn")
        self._btn.setFixedHeight(24)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._toggle_panel)
        lay.addWidget(self._btn)

        # Optional active indicator chip (shows last selected) — compact, no stretch
        self._active_label = QLabel("", self)
        self._active_label.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        self._active_label.setVisible(False)
        lay.addWidget(self._active_label)

        self._panel = IndicatorsPanel(self)
        self._panel.strategy_selected.connect(self._on_strategy)
        self._panel.indicator_selected.connect(self._on_indicator)
        self._panel.closed.connect(self._on_panel_closed)

        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _toggle_panel(self) -> None:
        if self._panel.isVisible():
            self._panel.hide()
            self._btn.setText("INDICATORS")
        else:
            self._panel.show_near(self._btn)
            self._btn.setText("INDICATORS")

    def _on_panel_closed(self) -> None:
        self._btn.setText("INDICATORS")

    def _on_strategy(self, name: str) -> None:
        self._active_label.setText(f"Active: {name}")
        self._active_label.setVisible(True)
        self.strategy_selected.emit(name)

    def _on_indicator(self, name: str, category: str) -> None:
        # For non-strategy indicators, just show active label; future phases can plot
        self._active_label.setText(f"Indicator: {name}")
        self._active_label.setVisible(True)
        self.indicator_selected.emit(name, category)

    def clear_active(self) -> None:
        self._active_label.clear()
        self._active_label.setVisible(False)

    @property
    def panel(self) -> IndicatorsPanel:
        return self._panel

    @property
    def button(self) -> QPushButton:
        return self._btn

    def set_strategies(self, names: tuple[str, ...]) -> None:
        """Inject strategy names (from bootstrap) into the popup."""
        self._panel.set_strategies(names)

    def refresh_strategies(self) -> None:
        """Force reload of strategy list (e.g., after lab save)."""
        self._panel.refresh_strategies()
