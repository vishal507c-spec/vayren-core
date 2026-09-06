"""TopNavBar — the VAYREN command-center navigation strip."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

_SECTIONS = ("MARKET", "STRATEGY LAB", "RESEARCH", "PORTFOLIO", "LIVE", "SYSTEM")

_SECTION_TIP = {
    "RESEARCH": "Not available yet",
    "PORTFOLIO": "Not available yet",
}

_BAR_STYLE = """
TopNavBar {
    background: palette(window);
    border-bottom: 1px solid palette(mid);
}
TopNavBar QLabel#brand {
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 1px;
    color: palette(highlight);
}
TopNavBar QPushButton {
    border: none;
    border-radius: 3px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
    background: transparent;
    color: palette(text);
}
TopNavBar QPushButton:hover {
    background: palette(midlight);
}
TopNavBar QPushButton:checked {
    background: transparent;
    color: #E6EDF3;
    border-bottom: 2px solid #00C7B7;
    border-radius: 0;
    padding-bottom: 2px;
}
TopNavBar QPushButton:disabled {
    color: palette(placeholder-text);
}
"""


class TopNavBar(QWidget):
    """Horizontal navigation: brand + section buttons."""

    market_clicked = Signal()
    strategy_lab_clicked = Signal()
    live_clicked = Signal()
    system_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopNavBar")
        self.setFixedHeight(34)
        self.setStyleSheet(_BAR_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(4)

        brand = QLabel("VAYREN", self)
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(16)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for section in _SECTIONS:
            button = QPushButton(section, self)
            button.setCheckable(True)
            button.setCursor(  # type: ignore[no-untyped-call]
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CursorShape.PointingHandCursor
            )
            if section in _SECTION_TIP:
                button.setEnabled(False)
                button.setToolTip(_SECTION_TIP[section])
            elif section == "MARKET":
                button.setChecked(True)
                button.clicked.connect(self.market_clicked)
            elif section == "STRATEGY LAB":
                button.clicked.connect(self.strategy_lab_clicked)
            elif section == "LIVE":
                button.clicked.connect(self.live_clicked)
            elif section == "SYSTEM":
                button.clicked.connect(self.system_clicked)
            else:
                button.clicked.connect(lambda _c=False, _s=section: None)  # noqa: ARG005
            self._group.addButton(button)
            self._buttons[section] = button
            layout.addWidget(button)

        layout.addStretch(1)

    @property
    def active(self) -> str:
        for section, button in self._buttons.items():
            if button.isChecked():
                return section
        return "MARKET"

    def set_active(self, section: str) -> None:
        button = self._buttons.get(section)
        if button is not None and button.isEnabled():
            button.setChecked(True)
