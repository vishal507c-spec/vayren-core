"""StrategyListPanel — checkbox strategy list with allocation."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from strategy.models.definition import StrategyDefinition

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 11px; font-weight: 600;"
_EMPTY_STYLE = "color: palette(placeholder-text); font-size: 11px; font-style: italic;"
_SECONDARY_STYLE = """
QPushButton {
    color: palette(text);
    background: transparent;
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 3px 5px;
    font-size: 10px;
}
QPushButton:hover {
    border-color: palette(highlight);
    color: palette(highlight);
}
"""


class _StrategyRow(QWidget):
    """One row: checkbox + label + allocation spin + context menu."""

    toggled = Signal(str, bool)
    allocation_changed = Signal(str, float)
    selected = Signal(str)
    configure_requested = Signal(str)
    duplicate_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, definition: StrategyDefinition, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._definition = definition

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        self._check = QCheckBox(self)
        self._check.setChecked(definition.enabled)
        self._check.toggled.connect(lambda checked: self.toggled.emit(definition.id, checked))

        label = QLabel(f"{definition.label}", self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet("font-size: 11px; font-weight: 500;")
        label.mousePressEvent = lambda _e: self.selected.emit(definition.id)  # type: ignore[assignment]

        self._allocation = QDoubleSpinBox(self)
        self._allocation.setRange(0.0, 100.0)
        self._allocation.setDecimals(0)
        self._allocation.setSuffix("%")
        self._allocation.setValue(definition.allocation_pct)
        self._allocation.setFixedWidth(58)
        self._allocation.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._allocation.valueChanged.connect(
            lambda v: self.allocation_changed.emit(definition.id, float(v))
        )

        layout.addWidget(self._check, 0)
        layout.addWidget(label, 1)
        layout.addWidget(self._allocation, 0)

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        menu.addAction("Configure…", lambda: self.configure_requested.emit(self._definition.id))
        menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(self._definition.id))
        menu.addAction("Remove", lambda: self.remove_requested.emit(self._definition.id))
        for action in menu.actions()[1:]:
            if "Optimize" in action.text() or "Compare" in action.text():
                action.setEnabled(False)
        menu.exec(event.globalPos())


class StrategyListPanel(QWidget):
    """Checkable strategy list below the control form."""

    strategy_toggled = Signal(str, bool)
    strategy_selected = Signal(str)
    allocation_changed = Signal(str, float)
    configure_requested = Signal(str)
    duplicate_requested = Signal(str)
    remove_requested = Signal(str)
    add_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._definitions: tuple[StrategyDefinition, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 6, 12, 2)
        header.setSpacing(6)
        caption = QLabel("STRATEGIES", self)
        caption.setStyleSheet(_LABEL_STYLE)
        self._add_button = QPushButton("+ ADD STRATEGY", self)
        self._add_button.setStyleSheet(_SECONDARY_STYLE)
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.clicked.connect(self.add_requested)
        header.addWidget(caption)
        header.addStretch(1)
        header.addWidget(self._add_button)
        layout.addLayout(header)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._inner = QWidget(self._scroll)
        self._rows_layout = QVBoxLayout(self._inner)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(2)
        self._empty_label = QLabel("No strategies registered", self._inner)
        self._empty_label.setStyleSheet(_EMPTY_STYLE)
        self._rows_layout.addWidget(self._empty_label)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._inner)
        layout.addWidget(self._scroll, 1)

    def set_strategies(self, definitions: tuple[StrategyDefinition, ...]) -> None:
        """Replace the rows from the registry snapshot."""
        self._definitions = definitions
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._empty_label.setVisible(not definitions)
        for definition in definitions:
            row = _StrategyRow(definition, self._inner)
            row.toggled.connect(self.strategy_toggled)
            row.allocation_changed.connect(self.allocation_changed)
            row.selected.connect(self.strategy_selected)
            row.configure_requested.connect(self.configure_requested)
            row.duplicate_requested.connect(self.duplicate_requested)
            row.remove_requested.connect(self.remove_requested)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    @property
    def definitions(self) -> tuple[StrategyDefinition, ...]:
        """The last snapshot received."""
        return self._definitions
