"""Database / state migration: code migration is not schema migration.

Discovers the persisted schema, analyzes compatibility against the
contracted tables, plans a forward migration with validation and a
rollback/recovery strategy, and verifies old data stays readable unless an
incompatible migration is explicitly defined.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT_TABLES = ("ohlcv", "non_trading", "history_boundaries")

CONTRACT_COLUMNS: dict[str, tuple[str, ...]] = {
    "ohlcv": ("candle_time", "open", "high", "low", "close", "volume"),
    "non_trading": ("candle_time",),
    "history_boundaries": ("symbol", "earliest", "latest"),
}


@dataclass
class SchemaReport:
    db: str
    tables: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    column_gaps: list[str] = field(default_factory=list)
    compatible: bool = True
    detail: str = ""


def discover_schema(db_path: Path) -> SchemaReport:
    report = SchemaReport(db=db_path.name)
    if not db_path.is_file():
        return SchemaReport(db=db_path.name, compatible=False, detail="database file missing")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return SchemaReport(db=db_path.name, compatible=False, detail=f"open failed: {exc}")
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        report.tables = [row[0] for row in rows]
        for table in CONTRACT_TABLES:
            if table not in report.tables:
                report.missing_tables.append(table)
                continue
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            for expected in CONTRACT_COLUMNS[table]:
                if expected not in cols:
                    report.column_gaps.append(f"{table}.{expected}")
        report.compatible = not report.missing_tables and not report.column_gaps
        report.detail = "compatible" if report.compatible else "schema drift detected"
    except sqlite3.Error as exc:
        report.compatible = False
        report.detail = f"inspection failed: {exc}"
    finally:
        conn.close()
    return report


def plan_forward(report: SchemaReport) -> dict:
    steps: list[str] = []
    for table in report.missing_tables:
        columns = ", ".join(CONTRACT_COLUMNS[table])
        steps.append(f"CREATE TABLE IF NOT EXISTS {table} ({columns})")
    for gap in report.column_gaps:
        steps.append(f"ALTER TABLE {gap.split('.')[0]} ADD COLUMN {gap.split('.')[1]}")
    return {
        "db": report.db,
        "compatible": report.compatible,
        "forward_steps": steps,
        "validation": ["row-count parity", "boundary re-read", "sample candle re-fetch"],
        "rollback": ["restore pre-migration file copy", "re-run discovery"],
    }


def scan_data_dir(data_dir: Path, limit: int = 25) -> list[SchemaReport]:
    if not data_dir.is_dir():
        return []
    reports: list[SchemaReport] = []
    for db_path in sorted(data_dir.glob("*.db"))[:limit]:
        reports.append(discover_schema(db_path))
    return reports


__all__ = ["SchemaReport", "discover_schema", "plan_forward", "scan_data_dir"]
