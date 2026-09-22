"""Canonical market-data adapter — the single OHLCV read path.

Composition-root glue (``00_app`` owns wiring): resolves the candle store,
discovers real per-symbol SQLite files, normalizes rows into the canonical
:class:`market.Bar` shape, and serves both the Chart snapshot and the
Strategy Lab / backtester from the SAME service instance. No second loader,
no duplicated market-data logic.

Source layout (written by the historical downloader, never modified here)::
    <data_dir>/<SYMBOL>.db  →  ohlcv(candle_time TEXT PK, open, high, low,
    close, volume) + non_trading + history_boundaries

Decisions that are math live in the Rust kernels and are only projected
here: base-bar detection via ``market.native_aggregate.mode``,
timeframe ladder/availability/fetch-plan via ``market.native_timeframe``,
bucket accumulation via ``market.native_aggregate.aggregate``. This module
does SQL IO, timestamp parsing/formatting and row validation only.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LEGACY_DATA_DIR = r"D:\ZerodhaTradingData"
_SESSION_ANCHOR = "09:15"
_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class MarketDataError(Exception):
    """Actionable market-data failure (message is safe to show in the UI)."""


@dataclass(frozen=True)
class Quote:
    """Latest quote fact for one symbol (absent quote = N/A downstream)."""

    symbol: str
    price: float | None
    change_pct: float | None


@dataclass(frozen=True)
class DiscoveryReport:
    """What symbol discovery found (and what it skipped, with reasons)."""

    data_dir: str
    symbols: tuple[str, ...]
    valid_sources: int
    skipped: tuple[tuple[str, str], ...]


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the candle store (priority: explicit → env → legacy → home).

    Raises:
        MarketDataError: When no candidate exists (actionable message).
    """
    candidates: list[tuple[str, Path]] = []
    if explicit is not None and str(explicit).strip():
        candidates.append(("explicit", Path(str(explicit)).expanduser()))
    env_dir = (os.environ.get("VAYREN_DATA_DIR") or "").strip()
    if env_dir:
        candidates.append(("VAYREN_DATA_DIR", Path(env_dir).expanduser()))
    legacy = Path(_LEGACY_DATA_DIR)
    candidates.append(("legacy", legacy))
    candidates.append(("default", Path.home() / ".vayren" / "data"))
    for origin, path in candidates:
        if path.is_dir():
            if origin != "explicit":
                logger.info("Data directory resolved from %s: %s", origin, path)
            return path
    tried = ", ".join(f"{origin}={path}" for origin, path in candidates)
    raise MarketDataError(f"Data directory not found (tried {tried})")


def _parse_stamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) == 16:  # "YYYY-MM-DD HH:MM" → pad seconds
        text += ":00"
    try:
        return datetime.strptime(text, _STAMP_FORMAT)
    except ValueError:
        return None


def _epoch(date_value: date) -> int:
    return date_value.toordinal()


def _valid_ohlcv(o: float, h: float, lo: float, c: float) -> bool:
    values = (o, h, lo, c)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return False
    if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
        return False
    return h >= lo


class MarketDataService:
    """Single canonical reader for per-symbol SQLite OHLCV stores."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = resolve_data_dir(data_dir)
        self._discovery: DiscoveryReport | None = None
        self._row_cache: dict[str, list[tuple[str, float, float, float, float, int]]] = {}
        self._quote_cache: dict[str, tuple[float, Quote]] = {}
        report = self.discovery_report()
        logger.info(
            "Market data ready: data_dir=%s discovered=%d valid=%d skipped=%d",
            report.data_dir,
            len(report.symbols),
            report.valid_sources,
            len(report.skipped),
        )
        for name, reason in report.skipped:
            logger.warning("Symbol file skipped: %s (%s)", name, reason)

    def refresh(self) -> DiscoveryReport:
        """Rescan the store (new downloads); returns the fresh report."""
        self._discovery = None
        self._row_cache.clear()
        return self.discovery_report()

    @property
    def data_dir(self) -> Path:
        """The resolved candle store folder."""
        return self._data_dir

    def _db_path(self, symbol: str) -> Path:
        safe = "".join(c for c in symbol if c.isalnum() or c in "-_").strip().upper()
        if not safe or safe != symbol.upper():
            raise MarketDataError(f"Invalid symbol name: {symbol!r}")
        return self._data_dir / f"{safe}.db"

    @staticmethod
    def _probe_file(path: Path) -> tuple[str | None, tuple[str, str] | None]:
        """Validate one ``*.db`` file by its SQLite header (no DB open).

        A 16-byte magic read keeps full-store scans in milliseconds; deeper
        table problems surface lazily on first access as actionable
        ``MarketDataError``s — one bad file never aborts discovery either way.
        """
        if not path.is_file():
            return None, (path.name, "not a regular file")
        stem = path.stem.strip().upper()
        if not stem or stem != path.stem.strip():
            return None, (path.name, "symbol name is empty or has whitespace")
        try:
            with open(path, "rb") as handle:
                magic = handle.read(16)
        except OSError as exc:
            return None, (path.name, f"unreadable file: {exc}")
        if magic != b"SQLite format 3\x00":
            return None, (path.name, "not a SQLite database")
        return stem, None

    def discovery_report(self) -> DiscoveryReport:
        """List real symbols; one invalid file never aborts discovery."""
        if self._discovery is not None:
            return self._discovery
        try:
            entries = sorted(self._data_dir.glob("*.db"))
        except OSError as exc:
            raise MarketDataError(f"Cannot list data directory {self._data_dir}: {exc}") from exc
        symbols: list[str] = []
        skipped: list[tuple[str, str]] = []
        for path in entries:
            stem, problem = MarketDataService._probe_file(path)
            if stem is not None:
                symbols.append(stem)
            elif problem is not None:
                skipped.append(problem)
        self._discovery = DiscoveryReport(
            data_dir=str(self._data_dir),
            symbols=tuple(sorted(symbols)),
            valid_sources=len(symbols),
            skipped=tuple(sorted(skipped)),
        )
        return self._discovery

    def list_symbols(self) -> list[str]:
        """Real discovered symbols, sorted (never invented)."""
        return list(self.discovery_report().symbols)

    def _read_rows(
        self,
        symbol: str,
        start: str | None,
        end: str | None,
        limit: int | None = None,
    ) -> list[tuple[str, float, float, float, float, int]]:
        """Raw validated base rows ``(stamp, o, h, l, c, v)`` ascending.

        Skips (never crashes on): unparseable stamps, non-finite or
        non-positive prices, high < low, duplicate stamps (last wins).
        ``limit`` trims newest-first inside SQL (indexed tail, no full scan).
        """
        if start is None and end is None and limit is None and symbol in self._row_cache:
            return list(self._row_cache[symbol])
        path = self._db_path(symbol)
        if not path.is_file():
            raise MarketDataError(f"No database found for symbol {symbol}")
        if limit is not None and start is None and end is None:
            query = (
                "SELECT candle_time, open, high, low, close, volume FROM ohlcv "
                "ORDER BY candle_time DESC LIMIT ?"
            )
            params: tuple = (int(limit),)
        else:
            query = (
                "SELECT candle_time, open, high, low, close, volume FROM ohlcv "
                "ORDER BY candle_time ASC"
            )
            params = ()
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        except sqlite3.Error as exc:
            raise MarketDataError(f"Cannot open {symbol} database: {exc}") from exc
        try:
            try:
                raw = con.execute(query, params).fetchall()
            except sqlite3.Error as exc:
                raise MarketDataError(
                    f"{symbol} database has no readable OHLCV records: {exc}"
                ) from exc
        finally:
            con.close()
        by_stamp: dict[str, tuple[str, float, float, float, float, int]] = {}
        invalid = 0
        for row in raw:
            stamp_raw, o, h, lo, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
            moment = _parse_stamp(stamp_raw)
            if moment is None:
                invalid += 1
                continue
            stamp = moment.strftime(_STAMP_FORMAT)
            try:
                fo, fh, fl, fc = float(o), float(h), float(lo), float(c)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if not _valid_ohlcv(fo, fh, fl, fc):
                invalid += 1
                continue
            try:
                volume = int(v) if v is not None else 0
            except (TypeError, ValueError):
                volume = 0
            if volume < 0:
                volume = 0
            if start is not None and stamp[:10] < start:
                continue
            if end is not None and stamp[:10] > end:
                continue
            by_stamp[stamp] = (stamp, fo, fh, fl, fc, volume)
        if invalid:
            logger.warning("%s: skipped %d invalid OHLCV rows", symbol, invalid)
        rows = [by_stamp[key] for key in sorted(by_stamp)]
        if start is None and end is None and limit is None:
            self._row_cache[symbol] = rows
            if len(self._row_cache) > 8:
                oldest = next(iter(self._row_cache))
                if oldest != symbol:
                    del self._row_cache[oldest]
        return list(rows)

    def _base_seconds(self, symbol: str) -> int:
        """Detected base bar duration via the kernel mode of timestamp deltas."""
        from market.native_aggregate import mode

        rows = self._read_rows(symbol, None, None, limit=200)
        if len(rows) < 2:
            rows = self._read_rows(symbol, None, None)
        if len(rows) < 2:
            raise MarketDataError(
                f"Insufficient data for {symbol}: only {len(rows)} usable candle(s)"
            )
        epochs = [datetime.strptime(stamp, _STAMP_FORMAT).timestamp() for stamp, *_ in rows]
        deltas = [int(b - a) for a, b in zip(epochs, epochs[1:], strict=False) if b > a]
        if not deltas:
            raise MarketDataError(f"{symbol} database has no ordered candle timestamps")
        base = mode(deltas)
        if base is None or base <= 0:
            raise MarketDataError(f"Cannot detect base timeframe for {symbol}")
        return int(base)

    def available_timeframes(self, symbol: str) -> tuple[str, ...]:
        """Producible timeframes for `symbol` (kernel ladder, never invented)."""
        from market import native_timeframe

        base = self._base_seconds(symbol)
        return native_timeframe.available_timeframes(base)

    def base_timeframe(self, symbol: str) -> str:
        """Base timeframe label (e.g. ``15m``) for `symbol`."""
        from market import native_timeframe

        base = self._base_seconds(symbol)
        return native_timeframe.name_of(base) or f"{base}s"

    def date_range(self, symbol: str) -> tuple[str, str]:
        """Actual ``(first_date, last_date)`` history bounds for `symbol`."""
        path = self._db_path(symbol)
        if not path.is_file():
            raise MarketDataError(f"No database found for symbol {symbol}")
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        except sqlite3.Error as exc:
            raise MarketDataError(f"Cannot open {symbol} database: {exc}") from exc
        try:
            try:
                row = con.execute("SELECT MIN(candle_time), MAX(candle_time) FROM ohlcv").fetchone()
            except sqlite3.Error as exc:
                raise MarketDataError(
                    f"{symbol} database has no readable OHLCV records: {exc}"
                ) from exc
        finally:
            con.close()
        if not row or row[0] is None or row[1] is None:
            raise MarketDataError(f"{symbol} database found but contains no OHLCV records")
        first, last = _parse_stamp(row[0]), _parse_stamp(row[1])
        if first is None or last is None:
            raise MarketDataError(f"{symbol} database has no ordered candle timestamps")
        return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")

    def get_bars(
        self,
        symbol: str,
        timeframe: str | None = None,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ):
        """Canonical bars for `symbol` as ``tuple[Bar, ...]`` ascending.

        ``timeframe=None`` returns base bars; higher multiples aggregate via
        the Rust kernel. ``limit=None`` means full history (never SQL-bound).
        Date bounds filter before aggregation (``YYYY-MM-DD``).
        """
        from market import Bar, native_aggregate, native_timeframe

        if start is not None and end is not None and start > end:
            raise MarketDataError(f"Invalid date range for {symbol}: {start} is after {end}")
        base = self._base_seconds(symbol)
        base_label = native_timeframe.name_of(base) or f"{base}s"
        wanted = (timeframe or "").strip() or base_label
        try:
            seconds = native_timeframe.seconds_of(wanted)
        except Exception as exc:
            raise MarketDataError(f"Unsupported timeframe {wanted!r} for {symbol}: {exc}") from exc
        if seconds is None:
            valid = ", ".join(native_timeframe.available_timeframes(base))
            raise MarketDataError(
                f"Unsupported timeframe {wanted!r} for {symbol} (available: {valid or base_label})"
            )
        plan = native_timeframe.fetch_plan(wanted, base, limit)
        if plan.plain:
            rows = self._read_rows(symbol, start, end, limit)
            if limit is not None:
                rows = rows[-limit:]
            return tuple(
                Bar(
                    symbol=symbol.upper(),
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=v,
                    timestamp=stamp,
                    bar_size=wanted,
                    source="zerodha-sqlite",
                )
                for stamp, o, h, lo, c, v in rows
            )
        if start is not None or end is not None:
            rows = self._read_rows(symbol, start, end)
        else:
            rows = self._read_rows(symbol, None, None, plan.row_budget)
            if plan.row_budget is not None:
                rows = rows[-plan.row_budget :]
        if not rows:
            raise MarketDataError(f"No candle data for {symbol} on {wanted} timeframe")
        anchor = native_aggregate.session_anchor_seconds(_SESSION_ANCHOR)
        days: list[int] = []
        secs: list[int] = []
        for stamp, _o, _h, _l, _c, _v in rows:
            moment = datetime.strptime(stamp, _STAMP_FORMAT)
            days.append(_epoch(moment.date()))
            secs.append(moment.hour * 3600 + moment.minute * 60 + moment.second)
        buckets = native_aggregate.aggregate(
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
        bars = [self._bucket_bar(symbol, wanted, plan.seconds, anchor, b) for b in buckets]
        bars.sort(key=lambda bar: bar.timestamp)
        if plan.keep_last is not None and start is None and end is None:
            bars = bars[-plan.keep_last :]
        if limit is not None:
            bars = bars[-limit:]
        if not bars:
            raise MarketDataError(f"No candle data for {symbol} on {wanted} timeframe")
        return tuple(bars)

    def _bucket_bar(self, symbol: str, label: str, tf: int, anchor: int, bucket: tuple):
        """Format one kernel bucket (domain IO; grouping math stays in Rust)."""
        from market import Bar

        day, index, o, h, lo, c, v = bucket
        if tf < 86_400:
            stamp = datetime.combine(
                date.fromordinal(int(day)),
                datetime.min.time(),
            )
            total = int(anchor) + int(index) * int(tf)
            total %= 86_400
            stamp = stamp.replace(hour=total // 3600, minute=(total % 3600) // 60)
            text = stamp.strftime(_STAMP_FORMAT)
        elif tf == 86_400:
            text = date.fromordinal(int(day)).strftime("%Y-%m-%d 00:00:00")
        else:
            text = date.fromordinal(int(day)).strftime("%Y-%m-%d 00:00:00")
        return Bar(
            symbol=symbol.upper(),
            open=float(o),
            high=float(h),
            low=float(lo),
            close=float(c),
            volume=int(v),
            timestamp=text,
            bar_size=label,
            source="zerodha-sqlite",
        )

    def _read_tail(self, symbol: str, count: int) -> list[tuple[str, float]]:
        """Last `count` validated ``(stamp, close)`` pairs (indexed tail read)."""
        path = self._db_path(symbol)
        if not path.is_file():
            raise MarketDataError(f"No database found for symbol {symbol}")
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        except sqlite3.Error as exc:
            raise MarketDataError(f"Cannot open {symbol} database: {exc}") from exc
        try:
            try:
                raw = con.execute(
                    "SELECT candle_time, open, high, low, close FROM ohlcv "
                    "ORDER BY candle_time DESC LIMIT ?",
                    (int(count),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise MarketDataError(
                    f"{symbol} database has no readable OHLCV records: {exc}"
                ) from exc
        finally:
            con.close()
        closes: list[tuple[str, float]] = []
        for row in reversed(raw):
            moment = _parse_stamp(row[0])
            if moment is None:
                continue
            try:
                fo, fh, fl, fc = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            except (TypeError, ValueError):
                continue
            if not _valid_ohlcv(fo, fh, fl, fc):
                continue
            closes.append((moment.strftime(_STAMP_FORMAT), fc))
        return closes

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Latest close + session change for each symbol (honest N/A on gaps).

        Cached per file-mtime: stores only change when a download lands, so
        repeated snapshots (symbol switches) stay instant.
        """
        quotes: list[Quote] = []
        for symbol in symbols:
            quote = self._cached_quote(symbol)
            quotes.append(quote if quote is not None else Quote(symbol, None, None))
        return quotes

    def _cached_quote(self, symbol: str) -> Quote | None:
        try:
            path = self._db_path(symbol)
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._quote_cache.get(symbol)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            tail = self._read_tail(symbol, 2)
        except MarketDataError:
            return None
        if not tail:
            return Quote(symbol=symbol, price=None, change_pct=None)
        last = tail[-1][1]
        change = None
        if len(tail) >= 2 and tail[-2][1]:
            change = 100.0 * (last - tail[-2][1]) / abs(tail[-2][1])
        quote = Quote(symbol=symbol, price=last, change_pct=change)
        self._quote_cache[symbol] = (mtime, quote)
        if len(self._quote_cache) > 2048:
            self._quote_cache.pop(next(iter(self._quote_cache)))
        return quote
