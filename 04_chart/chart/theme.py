"""Theme — the VAYREN institutional design system.

A fixed dark terminal palette (``APP_PALETTE``) plus a palette-role
stylesheet (``APP_STYLE``). Both are applied once on ChartWindow, so every
control inherits the same visual language regardless of the OS theme:
cool near-black surfaces, hairline structural separators, a single teal
accent for active/selected states, and restrained positive/negative tints.

Design principles: dense, precise, calm, information-first. No gradients,
no shadows, no animation, no hardcoded QSS colors — everything resolves
through palette roles from ``APP_PALETTE``.

Pure presentation: no logic, no events.
"""

from PySide6.QtGui import QColor, QPalette

WINDOW = QColor("#101418")
BASE = QColor("#101418")
ALTERNATE = QColor("#161c26")
MIDLIGHT = QColor("#1f2632")
MID = QColor("#2a3342")
DARK = QColor("#3b4659")
TEXT = QColor("#cfd8dc")
PLACEHOLDER = QColor("#5d6778")
ACCENT = QColor("#26a69a")
ACCENT_TEXT = QColor("#0b0f13")
TOOLTIP_BG = QColor("#1b212c")


def _build_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, WINDOW)
    palette.setColor(QPalette.ColorRole.WindowText, TEXT)
    palette.setColor(QPalette.ColorRole.Base, BASE)
    palette.setColor(QPalette.ColorRole.AlternateBase, ALTERNATE)
    palette.setColor(QPalette.ColorRole.Text, TEXT)
    palette.setColor(QPalette.ColorRole.Button, ALTERNATE)
    palette.setColor(QPalette.ColorRole.ButtonText, TEXT)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Midlight, MIDLIGHT)
    palette.setColor(QPalette.ColorRole.Mid, MID)
    palette.setColor(QPalette.ColorRole.Dark, DARK)
    palette.setColor(QPalette.ColorRole.Light, QColor("#46536b"))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Highlight, ACCENT)
    palette.setColor(QPalette.ColorRole.HighlightedText, ACCENT_TEXT)
    palette.setColor(QPalette.ColorRole.PlaceholderText, PLACEHOLDER)
    palette.setColor(QPalette.ColorRole.Link, ACCENT)
    palette.setColor(QPalette.ColorRole.ToolTipBase, TOOLTIP_BG)
    palette.setColor(QPalette.ColorRole.ToolTipText, TEXT)
    for group in (QPalette.ColorGroup.Disabled, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.WindowText, PLACEHOLDER)
        palette.setColor(group, QPalette.ColorRole.Text, PLACEHOLDER)
        palette.setColor(group, QPalette.ColorRole.ButtonText, PLACEHOLDER)
        palette.setColor(group, QPalette.ColorRole.Highlight, MID)
        palette.setColor(group, QPalette.ColorRole.HighlightedText, PLACEHOLDER)
    return palette


APP_PALETTE = _build_palette()

APP_STYLE = """
QToolButton {
    border: none;
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 12px;
    background: transparent;
}
QToolButton:hover {
    background: palette(midlight);
}
QToolButton:pressed {
    background: palette(mid);
}
QToolButton:disabled {
    color: palette(placeholder-text);
}
QPushButton {
    border: none;
    border-radius: 3px;
    padding: 2px 10px;
    font-size: 12px;
    background: transparent;
}
QPushButton:hover {
    background: palette(midlight);
}
QPushButton:pressed {
    background: palette(mid);
}
QPushButton:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    font-weight: 600;
}
TimeframeToolbar {
    border-bottom: 1px solid palette(mid);
}
TimeframeToolbar QPushButton {
    min-width: 44px;
    min-height: 24px;
    padding: 3px 8px;
    font-weight: 500;
}
QMenu {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 4px;
}
QMenu::item {
    padding: 5px 22px;
    border-radius: 4px;
    font-size: 12px;
}
QMenu::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QMenu::item:checked {
    color: palette(highlight);
    font-weight: 600;
}
QMenu::item:disabled {
    color: palette(placeholder-text);
}
QMenu::separator {
    height: 1px;
    background: palette(midlight);
    margin: 4px 10px;
}
QSplitter::handle {
    background: palette(mid);
}
QSplitter::handle:hover {
    background: palette(highlight);
}
QToolTip {
    color: palette(tooltip-text);
    background: palette(tooltip-base);
    border: 1px solid palette(mid);
    padding: 3px 6px;
}
"""
