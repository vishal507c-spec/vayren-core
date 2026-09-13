"""Institutional design system for the VAYREN Strategy Lab workstation.

Single source of truth for the lab's dark low-glare palette, typography
scale and shared QSS fragments. Presentation only — no logic, no bus.

The scale below is deliberately opinionated: decision-critical numbers are
never rendered below 16px, table content never below 13px, and labels stay
at 11px. Hierarchy comes from size + weight + colour, not from shrinking.
"""

BG0 = "#070B10"
BG1 = "#0B1017"
BG2 = "#0F151D"
PANEL = "#101720"
PANEL2 = "#131B24"
PANEL3 = "#18212C"
BORDER = "#202B36"
BORDER_SOFT = "#1A232D"
ACCENT = "#00C7B7"
ACCENT_DIM = "#00A99D"
ACCENT_DEEP = "#04211E"
TEXT = "#E6EDF3"
TEXT2 = "#8B98A7"
MUTED = "#596675"
POS = "#21C58B"
POS_DIM = "#16493A"
NEG = "#F05A67"
NEG_DIM = "#4A2027"
WARN = "#DDAA45"
WARN_DIM = "#4A3C18"

FONT_UI = "Segoe UI"
FONT_CODE = "JetBrains Mono, Consolas"

# ── Typography scale (px) — spec §30 ────────────────────────────────
FS_DISPLAY = 24  # screen identity / strategy name
FS_TITLE = 18  # section titles
FS_HERO = 26  # primary result (Net P&L)
FS_METRIC = 19  # level-2 metric values
FS_METRIC_SM = 16  # level-3 metric values
FS_BODY = 14  # body / control text
FS_TABLE = 13  # table cells
FS_SMALL = 12  # secondary text
FS_LABEL = 11  # micro labels (floor — never go below)

# ── Spacing / geometry (px) ─────────────────────────────────────────
SP_XS = 2
SP_SM = 4
SP_MD = 8
SP_LG = 12
SP_XL = 16
SP_XXL = 24

H_TOPBAR = 42
H_CONTEXT = 34
H_TABS = 34
H_STRIP = 36
RADIUS = 4
RADIUS_SM = 3

SPLITTER_QSS = (  # noqa: UP031
    "QSplitter::handle { background: %(border)s; }"
    "QSplitter::handle:hover { background: %(accent_dim)s; }"
    "QSplitter::handle:horizontal { width: 1px; }"
    "QSplitter::handle:vertical { height: 1px; }"
) % {"border": BORDER, "accent_dim": ACCENT_DIM}

INPUT_QSS = (  # noqa: UP031
    "QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox {"
    " background: %(bg1)s; border: 1px solid %(border)s; border-radius: 3px;"
    " padding: 4px 7px; color: %(text)s; font-size: 13px; selection-background-color: %(accent_dim)s;}"  # noqa: E501
    "QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {"
    " border-color: %(accent)s;}"
    "QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled { color: %(muted)s; }"
    "QComboBox::drop-down { border: none; width: 18px; }"
    "QComboBox QAbstractItemView { background: %(panel)s; color: %(text)s;"
    " border: 1px solid %(border)s; selection-background-color: %(panel2)s;"
    " selection-color: %(accent)s; outline: none; font-size: 13px;}"
) % {
    "bg1": BG1,
    "border": BORDER,
    "text": TEXT,
    "accent": ACCENT,
    "accent_dim": ACCENT_DIM,
    "panel": PANEL,
    "panel2": PANEL2,
    "muted": MUTED,
}

BUTTON_QSS = (  # noqa: UP031
    "QPushButton { background: transparent; border: 1px solid %(border)s; border-radius: 3px;"
    " padding: 5px 12px; color: %(text2)s; font-size: 12px; font-weight: 600;}"
    "QPushButton:hover { background: %(panel2)s; color: %(text)s; border-color: %(muted)s;}"
    "QPushButton:pressed { background: %(bg2)s; }"
    "QPushButton:disabled { color: %(muted)s; border-color: %(border)s; }"
) % {"border": BORDER, "text2": TEXT2, "text": TEXT, "panel2": PANEL2, "bg2": BG2, "muted": MUTED}

# The ONE dominant action (spec §8). Only the primary run control uses this.
PRIMARY_QSS = (  # noqa: UP031
    "QPushButton { background: %(accent)s; border: 1px solid %(accent)s; border-radius: 4px;"
    " padding: 9px 22px; color: %(deep)s; font-size: 14px; font-weight: 800;"
    " letter-spacing: 0.4px;}"
    "QPushButton:hover { background: %(accent_dim)s; border-color: %(accent_dim)s; }"
    "QPushButton:pressed { background: %(accent_dim)s; }"
    "QPushButton:disabled { background: %(panel2)s; border-color: %(border)s; color: %(muted)s; }"
) % {
    "accent": ACCENT,
    "accent_dim": ACCENT_DIM,
    "deep": ACCENT_DEEP,
    "panel2": PANEL2,
    "border": BORDER,
    "muted": MUTED,
}

# Sticky mirror of the primary action (top bar) — clearly subordinate to
# the filled primary, never an equally dominant second button.
TOP_RUN_QSS = (  # noqa: UP031
    "QPushButton { background: transparent; border: 1px solid %(accent)s; border-radius: 4px;"
    " padding: 6px 14px; color: %(accent)s; font-size: 12px; font-weight: 700;"
    " letter-spacing: 0.4px;}"
    "QPushButton:hover { background: %(accent)s; color: %(deep)s; }"
    "QPushButton:disabled { border-color: %(border)s; color: %(muted)s; }"
) % {"accent": ACCENT, "deep": ACCENT_DEEP, "border": BORDER, "muted": MUTED}

# Armed stop state — semantic, never a second run affordance.
STOP_QSS = (  # noqa: UP031
    "QPushButton { background: %(neg)s; border: 1px solid %(neg)s; border-radius: 4px;"
    " padding: 6px 14px; color: #1A0508; font-size: 12px; font-weight: 800;}"
    "QPushButton:hover { background: #FF6B77; }"
) % {"neg": NEG}

TOOL_QSS = (  # noqa: UP031
    "QToolButton { background: transparent; border: none; border-radius: 3px;"
    " padding: 4px 7px; color: %(text2)s; font-size: 12px; }"
    "QToolButton:hover { background: %(panel2)s; color: %(text)s; }"
    "QToolButton:checked { color: %(accent)s; background: %(panel2)s; }"
    "QToolButton:disabled { color: %(muted)s; }"
) % {"text2": TEXT2, "text": TEXT, "panel2": PANEL2, "accent": ACCENT, "muted": MUTED}

TABBAR_QSS = (  # noqa: UP031
    "QWidget#WorkspaceTabBar QPushButton { background: transparent; border: none;"
    " border-bottom: 2px solid transparent; border-radius: 0; padding: 7px 14px 6px;"
    " color: %(text2)s; font-size: 12px; font-weight: 600; letter-spacing: 0.4px;}"
    "QWidget#WorkspaceTabBar QPushButton:hover { color: %(text)s; }"
    "QWidget#WorkspaceTabBar QPushButton:checked { color: %(text)s;"
    " border-bottom: 2px solid %(accent)s; }"
) % {"text2": TEXT2, "text": TEXT, "accent": ACCENT}

# Analytical tab strip (results + selected stock) — readable, no tiny text.
TAB_QSS = (  # noqa: UP031
    "QPushButton { background: transparent; border: none;"
    " border-bottom: 2px solid transparent; border-radius: 0;"
    " padding: 8px 14px 7px; color: %(text2)s; font-size: 12px;"
    " font-weight: 700; letter-spacing: 0.6px;}"
    "QPushButton:hover { color: %(text)s; background: %(panel2)s; }"
    "QPushButton:checked { color: %(text)s; border-bottom: 2px solid %(accent)s; }"
) % {"text2": TEXT2, "text": TEXT, "panel2": PANEL2, "accent": ACCENT}

TABLE_QSS = (  # noqa: UP031
    "QTableWidget { background: %(bg1)s; border: none; gridline-color: %(border_soft)s;"
    " color: %(text)s; font-size: 13px; alternate-background-color: %(bg2)s;"
    " selection-background-color: %(panel3)s; selection-color: %(text)s; }"
    "QTableWidget::item { padding: 3px 8px; }"
    "QHeaderView::section { background: %(bg0)s; color: %(text2)s; border: none;"
    " border-right: 1px solid %(border_soft)s; border-bottom: 1px solid %(border)s;"
    " padding: 6px 8px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }"
    "QTableCornerButton::section { background: %(bg0)s; border: none; }"
) % {
    "bg1": BG1,
    "bg0": BG0,
    "bg2": BG2,
    "border": BORDER,
    "border_soft": BORDER_SOFT,
    "text": TEXT,
    "text2": TEXT2,
    "panel3": PANEL3,
}

SCROLLBAR_QSS = (  # noqa: UP031
    "QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }"
    "QScrollBar::handle:vertical { background: %(border)s; border-radius: 5px; min-height: 28px; }"
    "QScrollBar::handle:vertical:hover { background: %(muted)s; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar:add-page:vertical, QScrollBar:sub-page:vertical { background: transparent; }"
    "QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }"
    "QScrollBar::handle:horizontal { background: %(border)s; border-radius: 5px; min-width: 28px; }"
    "QScrollBar::handle:horizontal:hover { background: %(muted)s; }"
    "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
    "QScrollBar:add-page:horizontal, QScrollBar:sub-page:horizontal { background: transparent; }"
) % {"border": BORDER, "muted": MUTED}

CARD_QSS = (  # noqa: UP031
    "background: %(panel)s; border: 1px solid %(border)s; border-radius: 4px;"
) % {"panel": PANEL, "border": BORDER}

QUIET_BUTTON_QSS = (  # noqa: UP031
    "QPushButton { background: transparent; border: 1px solid %(border)s; border-radius: 3px;"
    " padding: 4px 11px; color: %(text2)s; font-size: 11px; font-weight: 600;}"
    "QPushButton:hover { background: %(panel2)s; color: %(text)s; border-color: %(muted)s;}"
    "QPushButton:disabled { color: %(muted)s; border-color: %(border)s; }"
) % {"border": BORDER, "text2": TEXT2, "text": TEXT, "panel2": PANEL2, "muted": MUTED}

MENU_QSS = (  # noqa: UP031
    "QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 4px; }"
    "QMenu::item { padding: 6px 20px 6px 12px; color: %(text2)s; font-size: 12px; border-radius: 2px; }"  # noqa: E501
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

# Sticky bars: strategy context + primary action area (§27, §28).
STICKY_BAR_QSS = f"background: {BG0}; border-bottom: 1px solid {BORDER};"

SECTION_QSS = f"background: {PANEL}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;"


def label(
    text_color: str = TEXT2,
    size: int = FS_LABEL,
    weight: int = 600,
    spacing: float = 0.4,
) -> str:
    """Standard uppercase micro-label style."""
    return (
        f"color: {text_color}; font-size: {size}px; font-weight: {weight};"
        f" letter-spacing: {spacing}px;"
    )


def section_title(size: int = 12) -> str:
    """Section identity — readable, letter-spaced, never tiny."""
    return f"color: {TEXT}; font-size: {size}px; font-weight: 800; letter-spacing: 1.0px;"


def metric(size: int = FS_METRIC, color: str = TEXT, weight: int = 700) -> str:
    """Metric value style — values are always materially larger than labels."""
    return f"color: {color}; font-size: {size}px; font-weight: {weight};"


def body(size: int = FS_BODY, color: str = TEXT, weight: int = 500) -> str:
    """Body copy style."""
    return f"color: {color}; font-size: {size}px; font-weight: {weight};"


def semantic(value: float | None, *, neutral: str = TEXT) -> str:
    """Semantic colour for a signed number (never the only signal — §33)."""
    if value is None:
        return MUTED
    if value > 0:
        return POS
    if value < 0:
        return NEG
    return neutral
