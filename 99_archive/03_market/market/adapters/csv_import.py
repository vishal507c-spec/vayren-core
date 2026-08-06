from pathlib import Path
from typing import Any

import pandas as pd

from market.models.bar import Bar


class CSVImportAdapter:
    """Import market data from CSV files."""

    REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}

    def import_bars(self, path: str | Path, symbol: str = "") -> list[Bar]:
        df = pd.read_csv(path)
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            msg = f"Missing required columns: {missing}"
            raise ValueError(msg)
        bars: list[Bar] = []
        for _, row in df.iterrows():
            bars.append(Bar(
                symbol=symbol or row.get("symbol", ""),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                timestamp=str(row["timestamp"]),
            ))
        return bars
