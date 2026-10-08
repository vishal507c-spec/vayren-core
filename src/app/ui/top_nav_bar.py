"""TopNavBar — the VAYREN command-center navigation strip."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from app.ui import lab_theme as t

# PORTFOLIO is owned by the native Rust+Slint view (constitution §3): this
# button routes to the in-window Slint viewport and never mounts a legacy
# Qt portfolio surface (that workspace was removed — Slint only).
_SECTIONS = ("MARKET", "STRATEGY LAB", "RESEARCH", "PORTFOLIO", "LIVE", "SYSTEM")

_BAR_STYLE = """
TopNavBar {
    background: palette(window);
    border-bottom: 1px solid palette(mid);
}
TopNavBar QLabel#brand {
    font-size: __FS_TABLE__px;
    font-weight: 800;
    letter-spacing: 1px;
    color: palette(highlight);
}
TopNavBar QPushButton {
    border: none;
    border-radius: 3px;
    padding: __PAD_Y__px __PAD_X__px;
    font-size: __FS_LABEL__px;
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
    border-bottom: 2px solid __ACCENT__;
    border-radius: 0;
    padding-bottom: 2px;
}
TopNavBar QPushButton:disabled {
    color: palette(placeholder-text);
}
""".replace("__ACCENT__", t.ACCENT, 1)  # tokens injected, never literals
_BAR_STYLE = _BAR_STYLE.replace("__FS_TABLE__", str(t.FS_TABLE), 1)
_BAR_STYLE = _BAR_STYLE.replace("__FS_LABEL__", str(t.FS_LABEL), 1)
_BAR_STYLE = _BAR_STYLE.replace("__PAD_Y__", str(t.SP_SM), 1)
_BAR_STYLE = _BAR_STYLE.replace("__PAD_X__", str(t.SP_LG), 1)


class TopNavBar(QWidget):
    """Horizontal navigation: brand + section buttons."""

    market_clicked = Signal()
    strategy_lab_clicked = Signal()
    research_clicked = Signal()
    portfolio_clicked = Signal()
    live_clicked = Signal()
    system_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopNavBar")
        self.setFixedHeight(t.H_CONTEXT)
        self.setStyleSheet(_BAR_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(t.SP_LG, t.SP_XS, t.SP_LG, t.SP_XS)
        layout.setSpacing(t.SP_SM)

        brand = QLabel("VAYREN", self)
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(t.SP_XL)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for section in _SECTIONS:
            button = QPushButton(section, self)
            button.setCheckable(True)
            button.setCursor(  # type: ignore[no-untyped-call]
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CursorShape.PointingHandCursor
            )
            if section == "MARKET":
                button.setChecked(True)
                button.clicked.connect(self.market_clicked)
            elif section == "STRATEGY LAB":
                button.clicked.connect(self.strategy_lab_clicked)
            elif section == "RESEARCH":
                button.clicked.connect(self.research_clicked)
            elif section == "PORTFOLIO":
                button.clicked.connect(self.portfolio_clicked)
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
