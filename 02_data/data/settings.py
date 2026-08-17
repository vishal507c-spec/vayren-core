"""Download settings — the configuration boundary of the historical downloader.

VAYREN has no configuration framework: modules receive everything through
constructor injection from Bootstrap (CLI args + environment). This dataclass
is that boundary for 02_data. Machine-specific hard-coded paths (the old
engine's ``D:\\ZerodhaTradingData``, ``Desktop\\token.json``) are gone — every
path is derived from ``data_dir`` and overridable.

Broker credentials are NOT part of the settings: they are read from the
environment at auth time by the selected provider's adapter and never appear
in events, logs, manifests or the system model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Canonical interval ids (broker-agnostic) and their human-readable labels.
# The selected provider adapter maps these to broker-specific interval ids.
INTERVAL_LABEL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
}

INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
}

# NSE market holidays — preserved unchanged from the original engine.
NSE_HOLIDAYS: frozenset[str] = frozenset(
    {
        # 2024
        "2024-01-22",
        "2024-01-26",
        "2024-03-08",
        "2024-03-25",
        "2024-03-29",
        "2024-04-11",
        "2024-04-14",
        "2024-04-17",
        "2024-04-21",
        "2024-05-01",
        "2024-05-23",
        "2024-06-17",
        "2024-07-17",
        "2024-08-15",
        "2024-10-02",
        "2024-10-12",
        "2024-10-14",
        "2024-11-01",
        "2024-11-15",
        "2024-11-20",
        "2024-12-25",
        # 2025
        "2025-01-26",
        "2025-02-26",
        "2025-03-14",
        "2025-03-31",
        "2025-04-10",
        "2025-04-14",
        "2025-04-18",
        "2025-04-21",
        "2025-05-01",
        "2025-06-07",
        "2025-07-06",
        "2025-08-15",
        "2025-08-27",
        "2025-10-02",
        "2025-10-20",
        "2025-10-23",
        "2025-11-05",
        "2025-12-25",
        # 2026
        "2026-01-26",
        "2026-03-03",
        "2026-03-20",
        "2026-04-02",
        "2026-04-03",
        "2026-04-14",
        "2026-05-01",
        "2026-06-26",
        "2026-07-25",
        "2026-08-15",
        "2026-09-16",
        "2026-10-02",
        "2026-11-09",
        "2026-11-14",
        "2026-12-25",
    }
)


@dataclass(frozen=True)
class DownloadSettings:
    """Tunables of the historical download engine (old CONFIG section).

    Values are the original engine constants; only the paths are rebuilt:
    everything lives under ``data_dir`` unless explicitly overridden.
    """

    data_dir: str | Path
    symbols_csv: str | Path | None = None  # default: <data_dir>/symbols.csv
    token_file: str | Path | None = None  # default: <data_dir>/token.json
    logs_dir: str | Path | None = None  # default: <data_dir>/logs
    provider: str = "zerodha"
    exchange: str = "NSE"

    chunk_days: int = 200
    max_history_years: int = 10
    max_retries: int = 5
    retry_delay_s: float = 5.0

    chunk_delay_min: float = 1.0
    chunk_delay_max: float = 2.0
    symbol_delay_min: float = 2.0
    symbol_delay_max: float = 4.0

    max_consecutive_429: int = 3

    market_open_h: int = 9
    market_open_m: int = 15
    market_close_h: int = 12
    market_close_m: int = 40

    min_inter_call_seconds: float = 0.5

    tail_lag_tolerance_days: int = 5
    head_tolerance_trading_days: int = 5

    lock_stale_seconds: int = 120
    max_passes: int = 5

    default_interval: str = "15m"

    holidays: frozenset[str] = field(default_factory=lambda: NSE_HOLIDAYS)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        object.__setattr__(self, "symbols_csv", self._resolve("symbols.csv", self.symbols_csv))
        object.__setattr__(self, "token_file", self._resolve("token.json", self.token_file))
        object.__setattr__(self, "logs_dir", self._resolve("logs", self.logs_dir))

    def _resolve(self, default_name: str, override: str | Path | None) -> Path:
        if override is None:
            return Path(self.data_dir) / default_name
        return Path(override)

    @property
    def lock_file(self) -> Path:
        """Single-instance lock file inside the data directory."""
        return Path(self.data_dir) / "hist_engine.lock"

    @property
    def db_path(self) -> Path:
        """Directory holding one per-symbol candle database."""
        return Path(self.data_dir)
