"""IndicatorVisibilityPanel — TradingView native indicator overlay bar.

Uses provided SVG assets exactly as supplied (vector, transparent background).
Single-row bar at chart top-left: OBR + 5 icons. No background/box/card.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "indicator_bar"

_PANEL_STYLE = """
QFrame#IndicatorVisibilityPanel {
    background: transparent;
    border: none;
}
QLabel#IndicatorName {
    font-family: "Inter", "Segoe UI", Arial, sans-serif;
    font-size: 14px;
    font-weight: 500;
    color: #E6EAF0;
    background: transparent;
}
QLabel#IndicatorName[hidden="true"] {
    color: #6b7280;
}
QToolButton#EyeButton, QToolButton#IconButton {
    border: none;
    border-radius: 4px;
    background: transparent;
    padding: 0px;
}
QToolButton#EyeButton:hover, QToolButton#IconButton:hover {
    background: transparent;
}
QToolButton#EyeButton:pressed, QToolButton#IconButton:pressed {
    background: transparent;
}
"""

_ICON_SIZE = 16
_ROW_HEIGHT = 28


def _icon_from_svg(name: str) -> QIcon:
    p = _ASSET_DIR / f"{name}.svg"
    if p.exists():
        return QIcon(str(p))
    # fallback to empty
    return QIcon()


def _eye_icon(visible: bool) -> QIcon:
    return _icon_from_svg("eye" if visible else "eye_off")


def _icon(kind: str) -> QIcon:
    # kind: settings, source, delete, more
    return _icon_from_svg(kind)


class _IndicatorRow(QWidget):
    toggled = Signal(str, bool)
    settings_requested = Signal(str)
    source_requested = Signal(str)
    delete_requested = Signal(str)
    more_requested = Signal(str, object)

    def __init__(self, name: str, visible: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._visible = visible
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_ROW_HEIGHT)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 8, 4)
        lay.setSpacing(6)

        self._label = QLabel(name, self)
        self._label.setObjectName("IndicatorName")
        self._label.setProperty("hidden", "false" if visible else "true")
        f = QFont("Inter, Segoe UI, Arial, sans-serif", 10)
        f.setWeight(QFont.Weight.Medium)
        self._label.setFont(f)
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._label, 1)

        self._eye = QToolButton(self)
        self._eye.setObjectName("EyeButton")
        self._eye.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._eye.setIconSize(QSize(16, 16))
        self._eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eye.setToolTip("Hide" if visible else "Show")
        self._eye.setIcon(_eye_icon(visible))
        self._eye.clicked.connect(self._on_eye_clicked)
        lay.addWidget(self._eye, 0, Qt.AlignmentFlag.AlignVCenter)

        self._settings = QToolButton(self)
        self._settings.setObjectName("IconButton")
        self._settings.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._settings.setIconSize(QSize(16, 16))
        self._settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings.setToolTip("Settings")
        self._settings.setIcon(_icon("settings"))
        self._settings.clicked.connect(lambda: self.settings_requested.emit(self._name))
        lay.addWidget(self._settings, 0, Qt.AlignmentFlag.AlignVCenter)

        self._source = QToolButton(self)
        self._source.setObjectName("IconButton")
        self._source.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._source.setIconSize(QSize(16, 16))
        self._source.setCursor(Qt.CursorShape.PointingHandCursor)
        self._source.setToolTip("Source code")
        self._source.setIcon(_icon("source"))
        self._source.clicked.connect(lambda: self.source_requested.emit(self._name))
        lay.addWidget(self._source, 0, Qt.AlignmentFlag.AlignVCenter)

        self._delete = QToolButton(self)
        self._delete.setObjectName("IconButton")
        self._delete.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._delete.setIconSize(QSize(16, 16))
        self._delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete.setToolTip("Remove")
        self._delete.setIcon(_icon("delete"))
        self._delete.clicked.connect(lambda: self.delete_requested.emit(self._name))
        lay.addWidget(self._delete, 0, Qt.AlignmentFlag.AlignVCenter)

        self._more = QToolButton(self)
        self._more.setObjectName("IconButton")
        self._more.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._more.setIconSize(QSize(16, 16))
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setToolTip("More")
        self._more.setIcon(_icon("more"))
        self._more.clicked.connect(self._on_more_clicked)
        lay.addWidget(self._more, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setStyleSheet("""
        _IndicatorRow {
            background: transparent;
            border: none;
        }
        _IndicatorRow:hover {
            background: transparent;
            border: none;
        }
        """)

    def _on_eye_clicked(self) -> None:
        self._visible = not self._visible
        self._eye.setIcon(_eye_icon(self._visible))
        self._eye.setToolTip("Show" if not self._visible else "Hide")
        self._label.setProperty("hidden", "false" if self._visible else "true")
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)
        self._label.update()
        self.toggled.emit(self._name, self._visible)

    def _on_more_clicked(self) -> None:
        pos = self._more.mapToGlobal(self._more.rect().bottomLeft())
        self.more_requested.emit(self._name, pos)

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_visible(self) -> bool:
        return self._visible

    def set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        self._eye.setIcon(_eye_icon(visible))
        self._eye.setToolTip("Hide" if visible else "Show")
        self._label.setProperty("hidden", "false" if visible else "true")
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)
        self._label.update()

    @property
    def eye_button(self) -> QToolButton:
        return self._eye

    @property
    def settings_button(self) -> QToolButton:
        return self._settings

    @property
    def source_button(self) -> QToolButton:
        return self._source

    @property
    def delete_button(self) -> QToolButton:
        return self._delete

    @property
    def more_button(self) -> QToolButton:
        return self._more

    @property
    def label(self) -> QLabel:
        return self._label


class IndicatorVisibilityPanel(QFrame):
    visibility_changed = Signal(str, bool)
    settings_requested = Signal(str)
    source_requested = Signal(str)
    indicator_removed = Signal(str)
    more_requested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IndicatorVisibilityPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_STYLE)
        self.setFixedWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self._rows: dict[str, _IndicatorRow] = {}
        self._layout = lay
        self.hide()

    def add_indicator(self, name: str) -> None:
        if name in self._rows:
            return
        display = "Vol" if name.lower() == "volume" else name
        key = name
        if name.lower() == "volume":
            key = "Vol"
            display = "Vol"
        if key in self._rows:
            return
        row = _IndicatorRow(display, True, self)
        row._name = key  # type: ignore[attr-defined]
        row.toggled.connect(self._on_row_toggled)
        row.settings_requested.connect(self.settings_requested.emit)
        row.source_requested.connect(self.source_requested.emit)
        row.delete_requested.connect(self._on_delete)
        row.more_requested.connect(self._on_more)
        self._layout.addWidget(row)
        self._rows[key] = row
        self._update_geometry()
        self.show()
        self.raise_()

    def remove_indicator(self, name: str) -> None:
        key = "Vol" if name.lower() == "volume" else name
        row = self._rows.pop(key, None)
        if row is None:
            row = self._rows.pop(name, None)
            if row is None:
                return
            key = name
        self._layout.removeWidget(row)
        row.deleteLater()
        self._update_geometry()
        self.indicator_removed.emit(key)
        if not self._rows:
            self.hide()
        else:
            self.show()

    def has_indicator(self, name: str) -> bool:
        key = "Vol" if name.lower() == "volume" else name
        return key in self._rows or name in self._rows

    def set_indicators(self, names: tuple[str, ...]) -> None:
        existing_vis = {n: row.is_visible for n, row in self._rows.items()}
        for row in list(self._rows.values()):
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        for name in names:
            key = "Vol" if name.lower() == "volume" else name
            display = "Vol" if name.lower() == "volume" else name
            visible = existing_vis.get(key, existing_vis.get(name, True))
            row = _IndicatorRow(display, visible, self)
            row._name = key  # type: ignore[attr-defined]
            row.toggled.connect(self._on_row_toggled)
            row.settings_requested.connect(self.settings_requested.emit)
            row.source_requested.connect(self.source_requested.emit)
            row.delete_requested.connect(self._on_delete)
            row.more_requested.connect(self._on_more)
            self._layout.addWidget(row)
            self._rows[key] = row
        self._update_geometry()
        if self._rows:
            self.show()
        else:
            self.hide()

    def _on_row_toggled(self, name: str, visible: bool) -> None:
        self.visibility_changed.emit(name, visible)

    def _on_delete(self, name: str) -> None:
        self.remove_indicator(name)

    def _on_more(self, name: str, pos: object) -> None:
        menu = QMenu(self)
        menu.addAction("Settings", lambda: self.settings_requested.emit(name))
        menu.addAction("Source code", lambda: self.source_requested.emit(name))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self.remove_indicator(name))
        with contextlib.suppress(Exception):
            menu.exec(pos)  # type: ignore[arg-type]
        self.more_requested.emit(name, pos)

    def _update_geometry(self) -> None:
        count = len(self._rows)
        h = 8 + count * 28 + max(0, count - 1) * 2 if count else 0
        self.setFixedHeight(h if count else 0)
        self.updateGeometry()

    def is_visible_for(self, name: str) -> bool:
        key = "Vol" if name.lower() == "volume" else name
        row = self._rows.get(key) or self._rows.get(name)
        return row.is_visible if row else True

    def set_indicator_visible(self, name: str, visible: bool) -> None:
        key = "Vol" if name.lower() == "volume" else name
        row = self._rows.get(key) or self._rows.get(name)
        if row is not None:
            row.set_visible(visible)

    @property
    def indicators(self) -> tuple[str, ...]:
        return tuple(self._rows.keys())

    def row(self, name: str) -> _IndicatorRow | None:
        key = "Vol" if name.lower() == "volume" else name
        return self._rows.get(key) or self._rows.get(name)

    @property
    def rows(self) -> tuple[_IndicatorRow, ...]:
        return tuple(self._rows.values())

    def clear(self) -> None:
        for row in list(self._rows.values()):
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._update_geometry()
        self.hide()
