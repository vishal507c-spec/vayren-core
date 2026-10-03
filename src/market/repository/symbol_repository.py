"""Symbol repository — per-symbol candle store access for the execution engine.

Restored legacy slice (tracked debt: `docs/language_retention.json`). It holds
NO market maths: base-bar detection, the timeframe ladder, bucket alignment
and aggregation are all Rust-owned and are called through the
`market.native_aggregate` / `market.native_timeframe` bridges. What lives here
is the row read (one read-only SQLite file per symbol, the store layout the
downloader writes) and the `Bar` payload construction.

The session anchor is the canonical 09:15 constant resolved THROUGH the Rust
kernel — the same anchor `app.services.market_data_service` uses for the chart
and the Lab, so all three readers bucket identically. This reader overlaps that
app-level adapter on purpose: the execution engine cannot import the app
composition root (that would be a cycle), and the Rust market store is the
migration target that removes the overlap.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from market.models.bar import Bar
from market.native_aggregate import aggregate, mode, session_anchor_seconds
from market.native_timeframe import available_timeframes, fetch_plan, seconds_of
from market.repository.store_connection import read_store

logger = logging.getLogger(__name__)

_SESSION_ANCHOR = "09:15"
_DETECTION_SAMPLE = 2000


class SymbolRepository:
    """Read-only candle access, one SQLite file per symbol."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    # ── store access ───────────────────────────────────────────────────

    def _path(self, symbol: str) -> Path:
        """Store file for `symbol`, tolerating the `VENUE:SYMBOL` convention.

        Configured symbols arrive venue-qualified (`NSE:KAYNES`) while the
        store is one file per bare symbol (`KAYNES.db`) — the same split the
        app-level market adapter resolves at its call sites.
        """
        clean = str(symbol).split(":")[-1].strip().upper()
        return self._directory / f"{clean}.db"

    def list_symbols(self) -> tuple[str, ...]:
        """Every symbol with a store file, sorted (never invented)."""
        try:
            return tuple(sorted(path.stem for path in self._directory.glob("*.db")))
        except OSError as exc:
            logger.warning("Symbol discovery failed for %s: %s", self._directory, exc)
            return ()

    def _rows(
        self,
        symbol: str,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[tuple[str, float, float, float, float, int]]:
        """Validated `(stamp, o, h, l, c, v)` rows ascending.

        The window is pushed into SQL (`candle_time` is ISO TEXT, so the bounds
        are lexicographic) and a non-positive `limit` returns nothing.
        """
        path = self._path(symbol)
        if not path.is_file():
            return []
        clauses: list[str] = []
        window: list[object] = []
        if start is not None:
            clauses.append("candle_time >= ?")
            window.append(f"{start} 00:00:00")
        if end is not None:
            clauses.append("candle_time <= ?")
            window.append(f"{end} 23:59:59")
        tail = " ORDER BY candle_time ASC"
        if limit is not None:
            if limit <= 0:
                return []
            tail = " ORDER BY candle_time DESC LIMIT ?"
            window.append(int(limit))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT candle_time, open, high, low, close, volume FROM ohlcv{where}{tail}"
        try:
            raw = read_store(path, lambda con: con.execute(query, tuple(window)).fetchall())
        except sqlite3.Error as exc:
            logger.warning("%s: cannot read store: %s", symbol, exc)
            return []
        if limit is not None:
            raw = list(reversed(raw))
        rows: list[tuple[str, float, float, float, float, int]] = []
        for stamp, o, h, low, c, v in raw:
            try:
                fo, fh, fl, fc = float(o), float(h), float(low), float(c)
            except (TypeError, ValueError):
                continue
            if fo <= 0 or fh <= 0 or fl <= 0 or fc <= 0 or fh < fl:
                continue
            if not (fh >= fo and fh >= fc and fh >= fl and fl <= fo and fl <= fc and fl <= fh):
                logger.warning(
                    "%s: invalid OHLC row skipped (o=%s h=%s l=%s c=%s)",
                    symbol,
                    fo,
                    fh,
                    fl,
                    fc,
                )
                continue
            try:
                volume = int(v) if v is not None else 0
            except (TypeError, ValueError):
                volume = 0
            rows.append((str(stamp), fo, fh, fl, fc, max(0, volume)))
        return rows

    # ── kernel-backed detection ────────────────────────────────────────

    def _base_seconds(self, rows: list[tuple]) -> int | None:
        """Dominant bar duration in seconds (Rust mode over real deltas)."""
        if len(rows) < 2:
            return None
        epochs: list[float] = []
        for stamp, *_ in rows:
            try:
                epochs.append(datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").timestamp())
            except ValueError:
                continue
        deltas = [int(b - a) for a, b in zip(epochs, epochs[1:], strict=False) if b > a]
        if not deltas:
            return None
        base = mode(deltas)
        return int(base) if base and base > 0 else None

    def detect(self, symbol: str, sample: int = _DETECTION_SAMPLE) -> tuple[int, int] | None:
        """One-shot `(base_seconds, session_start)` for polling callers.

        Tail providers cache this per symbol and pass it back as the
        `detection` hint, so the steady state costs one bounded window read
        instead of a sample scan per poll.
        """
        rows = self._rows(symbol, sample)
        base = self._base_seconds(rows)
        if base is None:
            return None
        return base, int(session_anchor_seconds(_SESSION_ANCHOR))

    def detect_timeframes(self, symbol: str) -> tuple[str, ...]:
        """Ladder the symbol's own rows can produce (Rust ladder)."""
        base = self._base_seconds(self._rows(symbol, _DETECTION_SAMPLE))
        if base is None:
            return ()
        return available_timeframes(base)

    def date_range(self, symbol: str) -> tuple[str, str]:
        """First and last stored bar date for `symbol`."""
        last = self._rows(symbol)
        if not last:
            return ("", "")
        return (last[0][0][:10], last[-1][0][:10])

    # ── bars ───────────────────────────────────────────────────────────

    def _bars(self, symbol: str, rows: list[tuple], label: str) -> list[Bar]:
        return [
            Bar(
                symbol=symbol,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                timestamp=stamp,
                bar_size=label,
                source="zerodha-sqlite",
            )
            for stamp, o, h, low, c, v in rows
        ]

    def get_candles(self, symbol: str, limit: int | None) -> list[Bar]:
        """Base-timeframe candles ascending; `limit=None` = full history."""
        rows = self._rows(symbol, limit)
        # Detect the base bar size from the same rows already fetched (the
        # newest up-to-200 of them), instead of a second database scan.
        base = self._base_seconds(rows[-200:])
        label = _label_of(base)
        if not label:
            logger.warning("%s: base bar size undetectable; returning no candles", symbol)
            return []
        return self._bars(symbol, rows, label)

    def get_candles_timeframe(
        self,
        symbol: str,
        timeframe: str,
        limit: int | None,
        start: str | None = None,
        end: str | None = None,
        detection: tuple[int, int] | None = None,
    ) -> list[Bar]:
        """Candles at `timeframe`, aggregated by the Rust kernel.

        `detection` is the cached `(base_seconds, session_start)` hint from
        :meth:`detect`; without it one bounded sample read pays for detection.
        Bounds apply to the aggregation window only, so bucket alignment never
        shifts with the window.
        """
        seconds = seconds_of(timeframe)
        if seconds is None:
            return []
        if detection is not None:
            base, anchor = detection
        else:
            detected = self.detect(symbol)
            if detected is None:
                return []
            base, anchor = detected
        plan = fetch_plan(timeframe, base, limit)
        if plan.plain:
            rows = self._rows(symbol, limit, start, end)
            return self._bars(symbol, rows, timeframe)
        rows = self._rows(symbol, plan.row_budget, start, end)
        if not rows:
            return []
        days: list[int] = []
        secs: list[int] = []
        for stamp, *_ in rows:
            days.append(date(int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10])).toordinal())
            secs.append(int(stamp[11:13]) * 3600 + int(stamp[14:16]) * 60 + int(stamp[17:19]))
        buckets = aggregate(
            days,
            secs,
            [r[1] for r in rows],
            [r[2] for r in rows],
            [r[3] for r in rows],
            [r[4] for r in rows],
            [float(r[5]) for r in rows],
            plan.seconds,
            anchor,
        )
        bars: list[Bar] = []
        for day, index, o, h, low, c, v in buckets:
            total = (int(anchor) + int(index) * int(plan.seconds)) % 86_400
            stamp = datetime.combine(date.fromordinal(int(day)), datetime.min.time())
            stamp = stamp.replace(
                hour=total // 3600,
                minute=(total % 3600) // 60,
                second=total % 60,
            )
            bars.append(
                Bar(
                    symbol=symbol,
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=int(v),
                    timestamp=stamp.strftime("%Y-%m-%d %H:%M:%S"),
                    bar_size=timeframe,
                    source="zerodha-sqlite",
                )
            )
        bars.sort(key=lambda bar: bar.timestamp)
        if plan.keep_last is not None:
            bars = bars[-plan.keep_last :]
        if limit is not None:
            bars = bars[-limit:]
        return bars


def _label_of(base_seconds: int | None) -> str:
    if not base_seconds:
        return ""
    from market.native_timeframe import name_of

    return name_of(base_seconds) or f"{base_seconds}s"


__all__ = ["SymbolRepository"]
