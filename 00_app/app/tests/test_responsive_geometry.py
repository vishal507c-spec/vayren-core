"""Responsive geometry: no clipping/overlap at production window sizes.

Offscreen harness (real Segoe UI metrics via conftest): each surface is
resized to five production geometries and inspected for the screenshot bug
classes — clipped unwrapped text, zero-size critical controls, missing
scroll containment, degenerate tables. Scroll-area inhabitants are exempt
from width checks (they scroll by design).
"""

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QTableWidget

from app.ui.research_workspace import ResearchWorkspace
from app.ui.top_nav_bar import TopNavBar

SIZES = [(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440)]


def _pump() -> None:
    app = QApplication.instance()
    assert app is not None
    app.processEvents()


def _in_scroll(label: QLabel) -> bool:
    parent = label.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def _assert_labels_fit(root) -> list[str]:
    problems = []
    for label in root.findChildren(QLabel):
        if not label.isVisible():
            continue
        text = label.text()
        if not text or "\n" in text or label.wordWrap():
            continue
        if _in_scroll(label):
            continue
        if label.minimumWidth() > 0:
            continue
        need = label.fontMetrics().horizontalAdvance(text)
        if need > label.width() + 2:
            problems.append(f"{label.text()[:24]!r} needs {need}px in {label.width()}px")
    return problems


def _assert_tables_sane(root) -> list[str]:
    problems = []
    for table in root.findChildren(QTableWidget):
        if not table.isVisible():
            continue
        if table.columnCount() == 0:
            problems.append("table with zero columns")
        header = table.horizontalHeader()
        if table.viewport().width() <= 0:
            problems.append("table with zero-width viewport")
        widths = [header.sectionSize(i) for i in range(table.columnCount())]
        if widths and max(widths) <= 0:
            problems.append("table with all-zero column widths")
    return problems


def test_research_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    for width, height in SIZES:
        workspace.resize(width, height)
        workspace.show()
        _pump()
        problems = _assert_labels_fit(workspace)
        assert problems == [], f"{width}x{height}: {problems[:5]}"
        assert _assert_tables_sane(workspace) == []
        assert workspace._run_button.width() > 0
        assert workspace._save_button.width() > 0
    workspace.hide()


def test_nav_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    for width, _height in SIZES:
        nav.resize(width, 34)
        nav.show()
        _pump()
        problems = _assert_labels_fit(nav)
        assert problems == [], f"{width}: {problems[:5]}"
    nav.hide()
