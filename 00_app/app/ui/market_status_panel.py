"""MarketStatusPanel — regime state (honest unknown until an engine reports)
+ real data-status display."""

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from app.ui import lab_theme as t

_SECTION_STYLE = t.label()
_KEY_STYLE = t.body(size=t.FS_LABEL, color=t.TEXT2)
_VALUE_STYLE = t.body(size=t.FS_LABEL, weight=600)
_VALUE_MUTED = t.body(size=t.FS_LABEL, color=t.TEXT2)
_SEPARATOR = f"background: {t.BORDER_SOFT}; border: none;"


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    line.setStyleSheet(_SEPARATOR)
    return line


class MarketStatusPanel(QWidget):
    """Two sections: MARKET REGIME (unknown until an engine reports) and
    DATA STATUS (real). Unknown is styled muted, never zero-filled."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SP_MD, t.SP_SM, t.SP_MD, t.SP_SM)
        layout.setSpacing(t.SP_SM)

        # ── Market Regime (explicit unavailable until an engine supplies values) ──
        regime_title = QLabel("MARKET REGIME", self)
        regime_title.setStyleSheet(_SECTION_STYLE)
        layout.addWidget(regime_title)
        self._regime_grid = QGridLayout()
        self._regime_grid.setContentsMargins(0, 0, 0, 0)
        self._regime_grid.setHorizontalSpacing(t.SP_MD)
        self._regime_grid.setVerticalSpacing(t.SP_XS)
        self._regime_labels: dict[str, QLabel] = {}
        for row, key in enumerate(("Current regime", "Trend strength", "Volatility", "Momentum")):
            kl = QLabel(key, self)
            kl.setStyleSheet(_KEY_STYLE)
            vl = QLabel("--", self)
            vl.setStyleSheet(_VALUE_MUTED)
            self._regime_grid.addWidget(kl, row, 0)
            self._regime_grid.addWidget(vl, row, 1)
            self._regime_labels[key] = vl
        layout.addLayout(self._regime_grid)

        layout.addWidget(_separator(self))

        # ── Data Status (real provider/last-update/bars) ──
        status_title = QLabel("DATA STATUS", self)
        status_title.setStyleSheet(_SECTION_STYLE)
        layout.addWidget(status_title)
        self._status_grid = QGridLayout()
        self._status_grid.setContentsMargins(0, 0, 0, 0)
        self._status_grid.setHorizontalSpacing(t.SP_MD)
        self._status_grid.setVerticalSpacing(t.SP_XS)
        self._status_labels: dict[str, QLabel] = {}
        for row, key in enumerate(("Data provider", "Latency", "Last update", "Bars loaded")):
            kl = QLabel(key, self)
            kl.setStyleSheet(_KEY_STYLE)
            vl = QLabel("--", self)
            vl.setStyleSheet(_VALUE_MUTED)
            self._status_grid.addWidget(kl, row, 0)
            self._status_grid.addWidget(vl, row, 1)
            self._status_labels[key] = vl
        layout.addLayout(self._status_grid)
        layout.addStretch(1)

    def set_regime(self, values: dict[str, str] | None) -> None:
        """Update regime rows; None resets every row to ``"--"``."""
        for key, label in self._regime_labels.items():
            text = (values or {}).get(key, "--") if values is not None else "--"
            label.setText(text)
            label.setStyleSheet(_VALUE_STYLE if text != "--" else _VALUE_MUTED)

    def set_data_status(
        self,
        provider: str | None = None,
        latency: str | None = None,
        last_update: str | None = None,
        bars_loaded: str | int | None = None,
    ) -> None:
        """Update one or more data-status rows (None = leave as ``"--"``)."""
        updates: dict[str, str | None] = {
            "Data provider": provider,
            "Latency": latency,
            "Last update": last_update,
            "Bars loaded": str(bars_loaded) if bars_loaded is not None else None,
        }
        for key, value in updates.items():
            if value is None:
                continue
            label = self._status_labels[key]
            label.setText(value)
            label.setStyleSheet(_VALUE_STYLE if value != "--" else _VALUE_MUTED)

    def clear(self) -> None:
        """Reset all rows to ``"--"``."""
        for label in (*self._regime_labels.values(), *self._status_labels.values()):
            label.setText("--")
            label.setStyleSheet(_VALUE_MUTED)
