"""Read-only architecture description (`vayren --describe`).

Builds the composition root offscreen, renders the deterministic
`SystemModel` snapshot, and exits — never shows a window, never starts the
event loop, never mutates runtime state.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.bootstrap.bootstrap import Bootstrap


def describe_architecture(data_dir: str | Path, limit: int | None = None) -> str:
    """Return the rendered architecture snapshot for the given data dir."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    qt_app = QApplication.instance()
    if qt_app is None:
        qt_app = QApplication([])
    bootstrap = Bootstrap(data_dir=Path(data_dir), limit=limit)
    return bootstrap.system_model.snapshot().render()
