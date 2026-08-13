"""Theme — centralized palette-based stylesheet for the chart window.

Applied once on ChartWindow so every control inherits the same visual
language: 4px corner radius, transparent resting buttons with soft hover and
press states, a highlight accent for active/selected controls, framed menus,
and hairline panel separators.

Pure presentation: only palette color roles are used (no hardcoded colors),
so the sheet follows the system palette automatically. No logic, no events.
"""

APP_STYLE = """
QToolButton {
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    background: transparent;
}
QToolButton:hover {
    background: palette(midlight);
}
QToolButton:pressed {
    background: palette(mid);
}
QPushButton {
    border: none;
    border-radius: 4px;
    padding: 3px 10px;
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
}
TimeframeToolbar {
    border-bottom: 1px solid palette(mid);
}
QMenu {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 4px;
}
QMenu::item {
    padding: 4px 18px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QMenu::item:disabled {
    color: palette(placeholder-text);
}
QSplitter::handle {
    background: palette(mid);
}
"""
