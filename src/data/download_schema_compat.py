"""Backward-compatible repair for legacy Historical Download candle databases.

Older builds created ``history_boundaries`` with only ``boundary_type``. The
preserved engine reads ``boundary_date``, so those databases fail with
``OperationalError: no such column: boundary_date``. This module adds the
missing columns IN PLACE, after a one-time backup, without touching candles or
inventing boundary dates. It is deliberately outside
``historical_download_engine.py`` so the preserved engine stays byte-identical.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

_MISSING_COLUMN_DDL = {
    "boundary_date": "ALTER TABLE history_boundaries ADD COLUMN boundary_date TEXT",
    "verified": ("ALTER TABLE history_boundaries ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"),
    "verified_at": "ALTER TABLE history_boundaries ADD COLUMN verified_at TEXT",
}
_BACKUP_SUFFIX = ".pre-boundary-migration.bak"


def ensure_history_boundaries(path: str | Path) -> bool:
    """Add missing boundary columns to one DB. Returns True only if changed.

    Safe to call repeatedly. A missing table is left alone; the engine's own
    DDL creates the current schema on first connect. Existing rows are kept,
    and a pre-migration backup is written once (never overwritten).
    """
    db = Path(path)
    if not db.is_file():
        return False
    conn = sqlite3.connect(db)
    try:
        try:
            rows = conn.execute("PRAGMA table_info(history_boundaries)").fetchall()
        except sqlite3.Error:
            return False
        columns = {str(row[1]) for row in rows}
        if not columns:  # absent table -> engine DDL creates the current schema
            return False
        missing = [name for name in _MISSING_COLUMN_DDL if name not in columns]
        if not missing:
            return False
        backup = db.with_name(db.name + _BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(db, backup)
        for name in missing:
            conn.execute(_MISSING_COLUMN_DDL[name])
        conn.commit()
        return True
    finally:
        conn.close()


def ensure_download_schema(data_dir: str) -> dict[str, Any]:
    """Repair every legacy candle DB in ``data_dir``; return an honest summary."""
    root = Path(data_dir)
    checked = 0
    repaired: list[str] = []
    failed: list[str] = []
    if root.is_dir():
        for db in sorted(root.glob("*.db")):
            if db.name.endswith(_BACKUP_SUFFIX):
                continue
            checked += 1
            try:
                if ensure_history_boundaries(db):
                    repaired.append(db.name)
            except Exception:  # noqa: BLE001 - one bad DB must not abort the run
                failed.append(db.name)
    return {"checked": checked, "repaired": repaired, "failed": failed}
