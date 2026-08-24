"""Institutional design system for the VAYREN Strategy Lab workstation.

Single source of truth for the lab's dark low-glare palette, typography
scale and shared QSS fragments. Presentation only — no logic, no bus.
"""

BG0 = "#070B10"
BG1 = "#0B1017"
BG2 = "#0F151D"
PANEL = "#101720"
PANEL2 = "#131B24"
BORDER = "#202B36"
ACCENT = "#00C7B7"
ACCENT_DIM = "#00A99D"
TEXT = "#E6EDF3"
TEXT2 = "#8B98A7"
MUTED = "#596675"
POS = "#21C58B"
NEG = "#F05A67"
WARN = "#DDAA45"

FONT_UI = "Segoe UI"
FONT_CODE = "JetBrains Mono, Consolas"

SPLITTER_QSS = (  # noqa: UP031
    "QSplitter::handle { background: %(border)s; }"
    "QSplitter::handle:hover { background: %(accent_dim)s; }"
    "QSplitter::handle:horizontal { width: 1px; }"
    "QSplitter::handle:vertical { height: 1px; }"
) % {"border": BORDER, "accent_dim": ACCENT_DIM}

INPUT_QSS = (  # noqa: UP031
    "QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox {"
    " background: %(bg1)s; border: 1px solid %(border)s; border-radius: 3px;"
    " padding: 3px 6px; color: %(text)s; font-size: 11px; selection-background-color: %(accent_dim)s;}"  # noqa: E501
    "QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {"
    " border-color: %(accent)s;}"
    "QComboBox::drop-down { border: none; width: 16px; }"
    "QComboBox QAbstractItemView { background: %(panel)s; color: %(text)s;"
    " border: 1px solid %(border)s; selection-background-color: %(panel2)s;"
    " selection-color: %(accent)s; outline: none;}"
) % {
    "bg1": BG1,
    "border": BORDER,
    "text": TEXT,
    "accent": ACCENT,
    "accent_dim": ACCENT_DIM,
    "panel": PANEL,
    "panel2": PANEL2,
}

BUTTON_QSS = (  # noqa: UP031
    "QPushButton { background: transparent; border: 1px solid %(border)s; border-radius: 3px;"
    " padding: 4px 10px; color: %(text2)s; font-size: 11px; font-weight: 600;}"
    "QPushButton:hover { background: %(panel2)s; color: %(text)s; border-color: %(muted)s;}"
    "QPushButton:pressed { background: %(bg2)s; }"
    "QPushButton:disabled { color: %(muted)s; border-color: %(border)s; }"
) % {"border": BORDER, "text2": TEXT2, "text": TEXT, "panel2": PANEL2, "bg2": BG2, "muted": MUTED}

PRIMARY_QSS = (  # noqa: UP031
    "QPushButton { background: %(accent)s; border: none; border-radius: 3px;"
    " padding: 5px 14px; color: #04211E; font-size: 11px; font-weight: 700;}"
    "QPushButton:hover { background: %(accent_dim)s; }"
    "QPushButton:pressed { background: #008F84; }"
    "QPushButton:disabled { background: %(panel2)s; color: %(muted)s; }"
) % {"accent": ACCENT, "accent_dim": ACCENT_DIM, "panel2": PANEL2, "muted": MUTED}

TOOL_QSS = (  # noqa: UP031
    "QToolButton { background: transparent; border: none; border-radius: 3px;"
    " padding: 3px 6px; color: %(text2)s; font-size: 11px; }"
    "QToolButton:hover { background: %(panel2)s; color: %(text)s; }"
    "QToolButton:checked { color: %(accent)s; background: %(panel2)s; }"
    "QToolButton:disabled { color: %(muted)s; }"
) % {"text2": TEXT2, "text": TEXT, "panel2": PANEL2, "accent": ACCENT, "muted": MUTED}

TABBAR_QSS = (  # noqa: UP031
    "QWidget#WorkspaceTabBar QPushButton { background: transparent; border: none;"
    " border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 12px 5px;"
    " color: %(text2)s; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;}"
    "QWidget#WorkspaceTabBar QPushButton:hover { color: %(text)s; }"
    "QWidget#WorkspaceTabBar QPushButton:checked { color: %(text)s;"
    " border-bottom: 2px solid %(accent)s; }"
) % {"text2": TEXT2, "text": TEXT, "accent": ACCENT}

TABLE_QSS = (  # noqa: UP031
    "QTableWidget { background: %(bg1)s; border: none; gridline-color: %(border)s;"
    " color: %(text)s; font-size: 11px; alternate-background-color: %(bg2)s;"
    " selection-background-color: %(panel2)s; selection-color: %(text)s; }"
    "QHeaderView::section { background: %(bg0)s; color: %(text2)s; border: none;"
    " border-right: 1px solid %(border)s; border-bottom: 1px solid %(border)s;"
    " padding: 4px 8px; font-size: 10px; font-weight: 600; }"
    "QTableCornerButton::section { background: %(bg0)s; border: none; }"
) % {
    "bg1": BG1,
    "bg0": BG0,
    "bg2": BG2,
    "border": BORDER,
    "text": TEXT,
    "text2": TEXT2,
    "panel2": PANEL2,
}

SCROLLBAR_QSS = (  # noqa: UP031
    "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
    "QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 24px; }"
    "QScrollBar::handle:vertical:hover { background: %(muted)s; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar:add-page:vertical, QScrollBar:sub-page:vertical { background: transparent; }"
    "QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }"
    "QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 24px; }"
    "QScrollBar::handle:horizontal:hover { background: %(muted)s; }"
    "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
    "QScrollBar:add-page:horizontal, QScrollBar:sub-page:horizontal { background: transparent; }"
) % {"border": BORDER, "muted": MUTED}

MENU_QSS = (  # noqa: UP031
    "QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 4px; }"
    "QMenu::item { padding: 5px 18px 5px 12px; color: %(text2)s; font-size: 11px; border-radius: 2px; }"  # noqa: E501
    "QMenu::item:selected { background: %(panel2)s; color: %(text)s; }"
    "QMenu::item:disabled { color: %(muted)s; }"
    "QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }"
) % {
    "panel": PANEL,
    "border": BORDER,
    "text2": TEXT2,
    "text": TEXT,
    "panel2": PANEL2,
    "muted": MUTED,
}


def label(text_color: str = TEXT2, size: int = 10, weight: int = 600, spacing: float = 0.4) -> str:
    """Standard uppercase micro-label style."""
    return (
        f"color: {text_color}; font-size: {size}px; font-weight: {weight};"
        f" letter-spacing: {spacing}px;"
    )
