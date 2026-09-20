"""DownloadReporter — the observation boundary between engine and UI.

The engine never prints and never knows about the bus. It reports real
progress through this protocol; the worker maps the calls to observable
signals, the UI maps them to the status view, and tests use a recorder. A
missing method is fine (duck typing) — the default implementation is a no-op.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class NullReporter:
    """No-op reporter — the engine's default."""

    def on_status(self, message: str) -> None: ...
    def on_symbol_started(
        self,
        symbol: str,
        interval: str,
        reason: str,
        from_dt: datetime,
        to_dt: datetime,
        total_chunks: int,
    ) -> None: ...
    def on_chunk(
        self,
        symbol: str,
        interval: str,
        chunk: int,
        total_chunks: int,
        chunk_start: datetime,
        chunk_end: datetime,
        new_rows: int,
        db_total: int,
    ) -> None: ...
    def on_symbol_finished(
        self,
        symbol: str,
        interval: str,
        new_rows: int,
        db_total: int,
        trading_days: int,
    ) -> None: ...
    def on_coverage(self, info: Any) -> None: ...
    def on_error(self, symbol: str, interval: str, message: str) -> None: ...


class RecordingReporter(NullReporter):
    """Test helper: records every report call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[str] = []

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def on_status(self, message: str) -> None:
        self._record("on_status", message=message)
        self.messages.append(message)

    def on_symbol_started(
        self,
        symbol: str,
        interval: str,
        reason: str,
        from_dt: datetime,
        to_dt: datetime,
        total_chunks: int,
    ) -> None:
        self._record(
            "on_symbol_started",
            symbol=symbol,
            interval=interval,
            reason=reason,
            from_dt=from_dt,
            to_dt=to_dt,
            total_chunks=total_chunks,
        )

    def on_chunk(
        self,
        symbol: str,
        interval: str,
        chunk: int,
        total_chunks: int,
        chunk_start: datetime,
        chunk_end: datetime,
        new_rows: int,
        db_total: int,
    ) -> None:
        self._record(
            "on_chunk",
            symbol=symbol,
            interval=interval,
            chunk=chunk,
            total_chunks=total_chunks,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            new_rows=new_rows,
            db_total=db_total,
        )

    def on_symbol_finished(
        self, symbol: str, interval: str, new_rows: int, db_total: int, trading_days: int
    ) -> None:
        self._record(
            "on_symbol_finished",
            symbol=symbol,
            interval=interval,
            new_rows=new_rows,
            db_total=db_total,
            trading_days=trading_days,
        )

    def on_coverage(self, info: Any) -> None:
        self._record("on_coverage", info=info)

    def on_error(self, symbol: str, interval: str, message: str) -> None:
        self._record("on_error", symbol=symbol, interval=interval, message=message)
        self.messages.append(f"ERROR {symbol} {interval}: {message}")
