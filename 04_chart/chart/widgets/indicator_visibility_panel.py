"""IndicatorVisibilityPanel — TradingView-style indicator overlay bar.

Each indicator row carries exactly THREE compact actions, rendered from the
single SVG asset ``vayren_3_action_toolbar.svg``:

* eye      — show/hide the indicator on the chart (state lives in the chart
             widget; the eye is only a toggle, never an owner of data);
* settings — open the indicator's real parameter panel (``IndicatorSettingsDialog``);
* delete   — remove the indicator completely (row + plots + renderer objects).

No fourth button, no code/source button, no "more" menu — the old duplicated
glyph row (eye + settings + source + delete + more) is replaced by this one
consistent horizontal group. Indicator calculation logic is untouched: the eye
only flips rendering visibility, settings only edit stored parameters, delete
only clears what the chart already rendered.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from chart.theme import ACCENT, PLACEHOLDER, TEXT

_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "indicator_bar"
_ASSET_FILE = _ASSET_DIR / "vayren_3_action_toolbar.svg"

_ICON_SIZE = 16
_BUTTON_SIZE = 20
_ROW_HEIGHT = 28

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
QToolButton#EyeButton, QToolButton#ActionButton {
    border: none;
    border-radius: 4px;
    background: transparent;
    padding: 0px;
}
QToolButton#EyeButton:hover, QToolButton#ActionButton:hover {
    background: rgba(255, 255, 255, 0.08);
}
QToolButton#EyeButton:pressed, QToolButton#ActionButton:pressed {
    background: rgba(255, 255, 255, 0.16);
}
QToolButton#EyeButton:checked {
    background: rgba(38, 166, 154, 0.16);
}
"""

# One renderer per tint (currentColor is substituted before parsing).
_RENDERER_CACHE: dict[str, object] = {}
_PIXMAP_CACHE: dict[tuple[str, str, int], QPixmap] = {}


def _asset_text() -> str:
    return _ASSET_FILE.read_text(encoding="utf-8")


def _renderer(color: QColor) -> object:
    key = color.name()
    renderer = _RENDERER_CACHE.get(key)
    if renderer is None:
        from PySide6.QtSvg import QSvgRenderer

        data = _asset_text().replace("currentColor", key)
        renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
        _RENDERER_CACHE[key] = renderer
    return renderer


def _toolbar_pixmap(kind: str, color: QColor, size: int = _ICON_SIZE) -> QPixmap:
    """Render one icon of the shared asset, aspect-fit into ``size`` px.

    Crisp at any DPR: the pixmap is drawn at 4x and device-pixel-ratio scaled.
    """
    key = (kind, color.name(), size)
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    from PySide6.QtSvg import QSvgRenderer

    renderer = _RENDERER_CACHE.get(color.name())
    if renderer is None:
        renderer = _renderer(color)
    assert isinstance(renderer, QSvgRenderer)
    scale = 4
    side = size * scale
    pixmap = QPixmap(side, side)
    pixmap.setDevicePixelRatio(float(scale))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bounds = renderer.boundsOnElement(kind)
    if not bounds.isEmpty():
        # uniform aspect-fit + centering so every icon shares one optical size
        fit = min(size / bounds.width(), size / bounds.height())
        w = bounds.width() * fit
        h = bounds.height() * fit
        target = QRectF((size - w) / 2.0, (size - h) / 2.0, w, h)
        renderer.render(painter, kind, target)
    painter.end()
    _PIXMAP_CACHE[key] = pixmap
    return pixmap


def _action_icon(kind: str) -> QIcon:
    """Settings/delete icons: a single normal state in the text color."""
    icon = QIcon()
    icon.addPixmap(_toolbar_pixmap(kind, TEXT), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_toolbar_pixmap(kind, PLACEHOLDER), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def _eye_icon() -> QIcon:
    """Eye icon: ON (visible) shows the open eye in the accent color, OFF
    (hidden) shows the crossed eye muted."""
    icon = QIcon()
    icon.addPixmap(_toolbar_pixmap("eye", ACCENT), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_toolbar_pixmap("eye_off", TEXT), QIcon.Mode.Normal, QIcon.State.Off)
    return icon


class _IndicatorRow(QWidget):
    """One indicator name + its three compact actions."""

    toggled = Signal(str, bool)
    settings_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, name: str, visible: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._visible = visible
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_ROW_HEIGHT)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 8, 4)
        lay.setSpacing(2)

        self._label = QLabel(name, self)
        self._label.setObjectName("IndicatorName")
        self._label.setProperty("hidden", "false" if visible else "true")
        f = QFont("Inter, Segoe UI, Arial, sans-serif", 10)
        f.setWeight(QFont.Weight.Medium)
        self._label.setFont(f)
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._label, 1)

        # ── the ONLY three actions: eye · settings · delete ────────────
        self._eye = QToolButton(self)
        self._eye.setObjectName("EyeButton")
        self._eye.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._eye.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eye.setCheckable(True)
        self._eye.setChecked(visible)
        self._eye.setToolTip("Hide" if visible else "Show")
        self._eye.setIcon(_eye_icon())
        self._eye.clicked.connect(self._on_eye_clicked)
        lay.addWidget(self._eye, 0, Qt.AlignmentFlag.AlignVCenter)

        self._settings = QToolButton(self)
        self._settings.setObjectName("ActionButton")
        self._settings.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._settings.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings.setToolTip(f"Settings — {name}")
        self._settings.setIcon(_action_icon("settings"))
        self._settings.clicked.connect(lambda: self.settings_requested.emit(self._name))
        lay.addWidget(self._settings, 0, Qt.AlignmentFlag.AlignVCenter)

        self._delete = QToolButton(self)
        self._delete.setObjectName("ActionButton")
        self._delete.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._delete.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete.setToolTip(f"Remove — {name}")
        self._delete.setIcon(_action_icon("delete"))
        self._delete.clicked.connect(lambda: self.delete_requested.emit(self._name))
        lay.addWidget(self._delete, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setStyleSheet(
            """
            _IndicatorRow {
                background: transparent;
                border: none;
            }
            """
        )

    def _on_eye_clicked(self) -> None:
        self.set_visible(not self._visible)
        self.toggled.emit(self._name, self._visible)

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_visible(self) -> bool:
        return self._visible

    def set_visible(self, visible: bool) -> None:
        """Synchronize eye state (icon, check, label dim) — chart-side source
        of truth calls this; it never repaints the chart itself."""
        if visible == self._visible:
            self._eye.blockSignals(True)
            self._eye.setChecked(visible)
            self._eye.blockSignals(False)
            return
        self._visible = visible
        self._eye.blockSignals(True)
        self._eye.setChecked(visible)
        self._eye.blockSignals(False)
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
    def delete_button(self) -> QToolButton:
        return self._delete

    @property
    def label(self) -> QLabel:
        return self._label


class IndicatorVisibilityPanel(QFrame):
    visibility_changed = Signal(str, bool)
    settings_requested = Signal(str)
    indicator_removed = Signal(str)

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

    def _connect_row(self, row: _IndicatorRow) -> None:
        row.toggled.connect(self._on_row_toggled)
        row.settings_requested.connect(self.settings_requested.emit)
        row.delete_requested.connect(self._on_delete)

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
        self._connect_row(row)
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
            self._connect_row(row)
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
