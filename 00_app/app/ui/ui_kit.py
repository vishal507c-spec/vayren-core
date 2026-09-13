"""UiKit — shared workspace widgets for VAYREN Qt screens.

Single home for the cross-screen building blocks so spacing, typography,
status badges, tables and empty states stay consistent: Section (titled
panel), Badge (status pill), KVBlock (wrapping key/value rows), GateRow
(readiness row with visible reason), table configuration, and EmptyState.
Colors and QSS fragments come from `app.ui.lab_theme` (the token source);
this module adds structure, never new palette entries.

Pure view helpers: no bus, no SQL, no brokers. All content is injected.
"""

from __future__ import annotations

import hashlib
import weakref
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t

_NA = "N/A"

# Layout tokens (px) — mirrors lab_theme SP_*/ROW_HEIGHT; one change
# propagates to every screen (UI_DESIGN_SYSTEM.md §3.3).
PAD_XS = t.SP_XS
PAD_SM = t.SP_SM
PAD_MD = t.SP_MD
PAD_LG = t.SP_MD
GAP_SM = t.SP_SM
GAP_MD = t.SP_MD
ROW_HEIGHT = 22


def text(value: Any, default: str = _NA) -> str:
    """Honest scalar rendering: None -> default, bool -> YES/NO, float -> 2dp."""
    if value is None:
        return default
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def money(value: Any) -> str:
    """Signed 2dp money rendering; unparseable -> N/A (never zero-filled)."""
    if value is None:
        return _NA
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return _NA


class Section(QWidget):
    """Titled panel container (lab_theme card conventions)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAD_LG, PAD_MD, PAD_LG, PAD_MD)
        layout.setSpacing(PAD_SM)
        header = QLabel(title, self)
        header.setStyleSheet(t.label())
        layout.addWidget(header)
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(GAP_SM)
        layout.addWidget(self.body)
        self.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 4px;"
        )

    def add(self, widget: QWidget) -> None:
        layout = self.body.layout()
        assert layout is not None
        layout.addWidget(widget)


class Badge(QLabel):
    """Status pill: short text + semantic tone (ok/warn/bad/muted/accent)."""

    _COLORS = {
        "ok": t.POS,
        "warn": t.WARN,
        "bad": t.NEG,
        "muted": t.MUTED,
        "accent": t.ACCENT,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("UiKitBadge")
        self.set_tone("muted")
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )

    def set_tone(self, tone: str) -> None:
        color = self._COLORS.get(tone, t.MUTED)
        self.setStyleSheet(
            "QLabel#UiKitBadge { color: "
            + color
            + f"; font-size: {t.FS_LABEL}px; font-weight: 700; }}"
        )

    def set_status(self, status_text: str, tone: str) -> None:
        self.setText(status_text)
        self.set_tone(tone)


class KVBlock(QWidget):
    """Wrapping key/value rows with a fixed label column.

    Values wrap and are mouse-selectable, so long backend strings (reasons,
    ids, timestamps) never overflow their panel. Missing keys render N/A.
    """

    def __init__(self, keys: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(GAP_SM)
        self._values: dict[str, QLabel] = {}
        for key in keys:
            row = QWidget(self)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(PAD_MD)
            name = QLabel(key.replace("_", " ").upper(), row)
            name.setStyleSheet(t.label(text_color=t.TEXT2))
            name.setMinimumWidth(110)
            value = QLabel(_NA, row)
            value.setStyleSheet(t.body(size=t.FS_LABEL, weight=500))
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setSizePolicy(
                value.sizePolicy().horizontalPolicy(),
                value.sizePolicy().verticalPolicy(),
            )
            row_lay.addWidget(name)
            row_lay.addWidget(value, 1)
            layout.addWidget(row)
            self._values[key] = value

    def set(self, key: str, display: str) -> None:
        label = self._values.get(key)
        if label is not None:
            label.setText(display)

    def set_all_na(self) -> None:
        for label in self._values.values():
            label.setText(_NA)

    def value(self, key: str) -> QLabel:
        """The value label for `key`. Unknown keys raise KeyError (fail-fast:
        row sets are fixed at construction, so a miss is a programming bug)."""
        return self._values[key]


class GateRow(QWidget):
    """One readiness row: status pill + gate name, with the reason visible
    underneath (wrapped, muted). The reason is ALSO the name tooltip so
    existing tooltip-based contracts keep working."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        top = QWidget(self)
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(PAD_MD)
        self.pill = Badge(top)
        top_lay.addWidget(self.pill)
        self.name_label = QLabel(name, top)
        self.name_label.setStyleSheet(t.body(size=t.FS_LABEL, weight=500))
        top_lay.addWidget(self.name_label, 1)
        layout.addWidget(top)
        self.reason_label = QLabel("", self)
        self.reason_label.setStyleSheet(t.label())
        self.reason_label.setWordWrap(True)
        self.reason_label.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.reason_label)

    def render_gate(self, status: str, reason: str) -> None:
        self.pill.set_status(
            status,
            {"READY": "ok", "NOT READY": "warn", "BLOCKED": "bad"}.get(status, "muted"),
        )
        self.name_label.setToolTip(reason or self.name_label.text())
        if reason:
            self.reason_label.setText(reason)
            self.reason_label.setVisible(True)
        else:
            self.reason_label.setText("")
            self.reason_label.setVisible(False)


def configure_table(
    table: QTableWidget,
    *,
    stretch_last: bool = True,
    min_section: int = 64,
) -> QTableWidget:
    """Apply the terminal table conventions: content-sized columns with a
    sane minimum, stretched last column, fixed row height, row selection,
    right-elision (never silent truncation of the underlying value)."""
    header = table.horizontalHeader()
    header.setMinimumSectionSize(min_section)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    count = table.columnCount()
    for index in range(count):
        if stretch_last and index == count - 1:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setStyleSheet(t.TABLE_QSS)
    return table


_TABLE_SIGNATURES: weakref.WeakKeyDictionary[QTableWidget, tuple[int, str]] = (
    weakref.WeakKeyDictionary()
)


def fill_table(table: QTableWidget, rows: list[list[str]]) -> bool:
    """Refill a table; returns True when content changed.

    Skips the rebuild when the content signature matches, avoiding per-tick
    flicker on 1s refresh loops. Never edits values (elision is visual only).
    """
    signature = hashlib.md5(
        ("\x00".join(str(cell) for row in rows for cell in row)).encode("utf-8")
    ).hexdigest()
    if _TABLE_SIGNATURES.get(table) == (len(rows), signature):
        return False
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if j >= table.columnCount():
                break
            item = QTableWidgetItem(str(cell))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            # Elided cells keep the full value one hover away.
            item.setToolTip(str(cell))
            if "UNKNOWN" in str(cell):
                from PySide6.QtGui import QBrush, QColor

                item.setForeground(QBrush(QColor(t.NEG)))
            table.setItem(i, j, item)
    _TABLE_SIGNATURES[table] = (len(rows), signature)
    return True


class EmptyState(QLabel):
    """Centered title + wrapped explanation for empty panels."""

    def __init__(self, title: str, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._detail = detail
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(t.body(size=t.FS_SMALL, color=t.MUTED))
        self._refresh()

    def _refresh(self) -> None:
        if self._detail:
            self.setText(f"{self._title}\n{self._detail}")
        else:
            self.setText(self._title)

    def set_detail(self, detail: str) -> None:
        self._detail = detail
        self._refresh()
