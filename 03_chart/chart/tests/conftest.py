"""Shared Qt bootstrap for the chart test directory.

Qt applications are process-global objects. Creating and discarding
`QApplication`/`QGuiApplication` wrappers across many test modules can crash
PySide6 on Windows during later widget construction. This conftest creates the
application once, at collection time (before any test module imports the Qt
classes it uses) and keeps a strong reference for the whole pytest process.
"""

from PySide6.QtWidgets import QApplication

_KEEP = QApplication.instance()
if not isinstance(_KEEP, QApplication):
    _KEEP = QApplication([])
