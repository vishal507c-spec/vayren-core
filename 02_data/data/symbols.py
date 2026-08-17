"""Symbol list loading — symbols.csv with a data-directory fallback.

The original engine hard-required ``symbols.csv`` next to the script and
exited when missing. In VAYREN the CSV path comes from settings, and when no
CSV exists the engine falls back to the candle databases themselves (the
engine's own "candle DBs are the only source of truth" rule) — so a fresh
data directory always yields a usable symbol list.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("HistDownloadEngine")


def load_symbols(symbols_csv: str | Path, default_interval: str = "15m") -> list[dict[str, str]]:
    """Parse ``trading_symbol,interval`` rows; empty rows are skipped."""
    symbols: list[dict[str, str]] = []
    with open(symbols_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): v.strip() for k, v in row.items()}
            ts = row.get("trading_symbol", "").strip()
            iv = row.get("interval", default_interval).strip()
            if ts:
                symbols.append({"trading_symbol": ts, "interval": iv})
    return symbols


def discover_symbols(data_dir: str | Path, default_interval: str = "15m") -> list[dict[str, str]]:
    """Symbols derived from the data directory (one entry per *.db file).

    Used when no symbols.csv exists. The interval defaults to the canonical
    base granularity (15m).
    """
    directory = Path(data_dir)
    return [
        {"trading_symbol": path.stem, "interval": default_interval}
        for path in sorted(directory.glob("*.db"))
    ]


def resolve_symbols(settings) -> list[dict[str, Any]]:
    """CSV when present, otherwise database-derived symbols."""
    if Path(settings.symbols_csv).is_file():
        symbols = load_symbols(settings.symbols_csv, settings.default_interval)
        log.info(f"Loaded {len(symbols)} symbol(s) from {settings.symbols_csv}")
    else:
        symbols = discover_symbols(settings.data_dir, settings.default_interval)
        log.info(f"No symbols.csv — derived {len(symbols)} symbol(s) from data directory")
    if not symbols:
        log.error("No symbols found (empty CSV or empty data directory).")
    return symbols
