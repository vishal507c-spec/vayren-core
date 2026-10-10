"""NSE instrument master — authoritative equity catalog ingestion (stdlib only).

The catalog behind the Strategy Lab / Live stock selector. It is NOT a
hand-curated list: it is the exchange instrument dump published by the
supported market-data provider (Kite ``/instruments/NSE``), filtered to
plain NSE cash-equity rows and written to a local atomic cache.

Trust model:
- Only rows that are NSE, ``instrument_type == EQ`` and carry a plain equity
  ticker are admitted. Govt securities (SDL/GOI/SGB), NCD/bonds, indices and
  placeholder ISIN-style rows are excluded, never silently kept.
- A refresh replaces the cache only after the whole payload parses and yields
  a plausible number of equities. A failed or partial refresh leaves the last
  good cache untouched and records the failure.
- Staleness is explicit: the snapshot carries ``fetched_at`` and ``stale``.
- The 30-symbol registry seed is a development fallback only. It is reported
  as ``source == "seed"`` and never presented as a complete catalog.

Consumers reach the catalog through :func:`load_catalog`; the canonical
registry (``strategy.instrument_registry``) is what resolves identity.
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import io
import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

KITE_NSE_INSTRUMENTS_URL = "https://api.kite.trade/instruments/NSE"

SOURCE_KITE = "kite-instruments-nse"
SOURCE_SEED = "seed"

_CACHE_KIND = "vayren.nse_equity_master"
_CACHE_VERSION = 1
_CACHE_NAME = "nse_equity_master.json"

#: Valid NSE equity ticker grammar: letters, digits, ``&`` and ``-`` (real
#: names such as BAJAJ-AUTO use a hyphen).
_EQUITY_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9&-]{0,19}$")
#: Hyphen suffixes that are NSE debt/SME *series codes*, not part of a company
#: name. Only an exact series token after the final hyphen triggers exclusion,
#: so ordinary hyphenated names (BAJAJ-AUTO) are kept. Derived from the NSE
#: dump: NCD tranches (N0-N9, NA-NZ), SME/trade-to-trade (BE, BZ, SM, ST),
#: govt/T-bill/gold (SG, GS, GB, TB) and REIT/InvIT units (RR, IV, RL, SF).
_NON_EQUITY_SERIES = frozenset(
    {"SG", "GS", "GB", "TB", "BZ", "BE", "SM", "ST", "RR", "RL", "IV", "SF"}
    | {f"N{c}" for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    | {f"Y{c}" for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
)
#: Sanity floor: a real NSE equity dump has thousands of EQ rows. A payload
#: below this is treated as partial/corrupt and never replaces the cache.
MIN_PLAUSIBLE_EQUITIES = 1000
#: Refresh older than this is reported as stale (consumers may still use it).
STALE_AFTER = datetime.timedelta(hours=24)
FETCH_TIMEOUT_SECONDS = 20


class InstrumentMasterError(RuntimeError):
    """A refresh could not produce a trustworthy catalog."""


@dataclass(frozen=True)
class CatalogSnapshot:
    """Read model for the selector: what is known, from where, how fresh."""

    symbols: tuple[str, ...] = ()
    source: str = ""
    fetched_at: str = ""
    error: str = ""
    stale: bool = False
    rejected_rows: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_authoritative(self) -> bool:
        return self.source == SOURCE_KITE and bool(self.symbols)

    @property
    def is_empty(self) -> bool:
        return not self.symbols


#: Name fragments that mark govt securities, bonds, bills and bond-like units
#: that the provider also tags ``EQ``. Matched on the instrument name, not the
#: ticker, so hyphenated real equities (e.g. BAJAJ-AUTO) are unaffected.
_NON_EQUITY_NAME_RE = re.compile(
    r"\b(SDL|GOI|T-?BILL|NCD|BOND|GOLD\s*BOND|SGB|GS\s*\d|DEBENTURE|TREASURY|REIT|INVIT)\b"
)


def classify_row(row: dict) -> str | None:
    """Return the bare equity symbol for an admissible row, else None.

    Decided from the provider's own metadata, not from ticker spelling:
    - exchange/segment NSE, instrument type EQ, no expiry (cash segment only);
    - lot size exactly 1: govt securities and bonds trade in lots of 100+;
    - the instrument name is not a bond/bill/REIT-style name;
    - the ticker is a valid equity symbol (letters, digits, ``&``, ``-``).

    A row that fails any check is excluded and counted, never guessed in.
    """
    if str(row.get("exchange", "")).strip().upper() != "NSE":
        return None
    if str(row.get("segment", "")).strip().upper() != "NSE":
        return None
    if str(row.get("instrument_type", "")).strip().upper() != "EQ":
        return None
    if str(row.get("expiry", "")).strip():
        return None
    try:
        if int(float(str(row.get("lot_size", "") or "0").strip())) != 1:
            return None
    except ValueError:
        return None
    name = str(row.get("name", "")).strip().upper()
    if _NON_EQUITY_NAME_RE.search(name):
        return None
    symbol = str(row.get("tradingsymbol", "")).strip().upper()
    if not _EQUITY_TICKER_RE.match(symbol):
        return None
    if "-" in symbol and symbol.rsplit("-", 1)[1] in _NON_EQUITY_SERIES:
        return None
    return symbol


def parse_instruments_csv(text: str) -> tuple[tuple[str, ...], int]:
    """Parse the provider CSV into (sorted unique equity symbols, rejected rows).

    Raises InstrumentMasterError when the payload is not the expected shape.
    """
    reader = csv.DictReader(io.StringIO(text))
    required = {"tradingsymbol", "exchange", "segment", "instrument_type"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise InstrumentMasterError("instrument payload missing required columns")
    symbols: set[str] = set()
    rejected = 0
    for row in reader:
        symbol = classify_row(row)
        if symbol is None:
            rejected += 1
        else:
            symbols.add(symbol)
    if len(symbols) < MIN_PLAUSIBLE_EQUITIES:
        raise InstrumentMasterError(
            f"only {len(symbols)} NSE equities parsed; refusing a partial catalog"
        )
    return tuple(sorted(symbols)), rejected


def fetch_instruments_csv(url: str = KITE_NSE_INSTRUMENTS_URL) -> str:
    """Download the authoritative NSE instrument dump (raises on any failure)."""
    request = urllib.request.Request(url, headers={"User-Agent": "vayren-core/instrument-master"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise InstrumentMasterError(f"instrument source returned HTTP {response.status}")
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
        raise InstrumentMasterError(f"instrument source unavailable: {exc}") from exc


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _write_cache(path: Path, symbols: tuple[str, ...], fetched_at: str, rejected: int) -> None:
    payload = {
        "kind": _CACHE_KIND,
        "version": _CACHE_VERSION,
        "source": SOURCE_KITE,
        "fetched_at": fetched_at,
        "rejected_rows": rejected,
        "symbols": list(symbols),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".nse_master_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_cache(path: Path) -> CatalogSnapshot | None:
    """Last good cache, or None when absent/corrupt (corrupt is logged)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("NSE instrument cache %s is unreadable; ignoring", path)
        return None
    if not isinstance(data, dict) or data.get("kind") != _CACHE_KIND:
        logger.warning("NSE instrument cache %s has unknown kind; ignoring", path)
        return None
    raw = data.get("symbols")
    if not isinstance(raw, list):
        return None
    symbols = tuple(sorted({str(s).strip().upper() for s in raw if str(s).strip()}))
    if len(symbols) < MIN_PLAUSIBLE_EQUITIES:
        logger.warning("NSE instrument cache %s is implausibly small; ignoring", path)
        return None
    fetched_at = str(data.get("fetched_at") or "")
    return CatalogSnapshot(
        symbols=symbols,
        source=SOURCE_KITE,
        fetched_at=fetched_at,
        stale=_is_stale(fetched_at),
        rejected_rows=int(data.get("rejected_rows") or 0),
    )


def _is_stale(fetched_at: str) -> bool:
    try:
        stamp = datetime.datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.UTC)
    return _utcnow() - stamp > STALE_AFTER


def refresh_catalog(
    cache_path: Path,
    *,
    fetch: object = fetch_instruments_csv,
) -> CatalogSnapshot:
    """Fetch, validate and atomically cache the NSE equity master.

    Never raises for a source failure: the failure is returned in ``error`` and
    the previous good cache (if any) is returned instead, marked stale.
    """
    try:
        text = fetch()  # type: ignore[operator]
        symbols, rejected = parse_instruments_csv(text)
    except InstrumentMasterError as exc:
        logger.warning("NSE instrument refresh failed: %s", exc)
        previous = _read_cache(cache_path)
        if previous is not None:
            return CatalogSnapshot(
                symbols=previous.symbols,
                source=previous.source,
                fetched_at=previous.fetched_at,
                error=str(exc),
                stale=True,
                rejected_rows=previous.rejected_rows,
                notes=("kept last good catalog after failed refresh",),
            )
        return CatalogSnapshot(error=str(exc))
    fetched_at = _utcnow().isoformat()
    _write_cache(cache_path, symbols, fetched_at, rejected)
    return CatalogSnapshot(
        symbols=symbols,
        source=SOURCE_KITE,
        fetched_at=fetched_at,
        stale=False,
        rejected_rows=rejected,
    )


def load_catalog(cache_path: Path, seed: tuple[str, ...] = ()) -> CatalogSnapshot:
    """Read the cached authoritative catalog; never fetches.

    Falls back to the development seed only when no authoritative cache
    exists, and says so explicitly via ``source == "seed"``.
    """
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached
    if seed:
        bare = tuple(sorted({s.split(":")[-1].strip().upper() for s in seed if s.strip()}))
        return CatalogSnapshot(
            symbols=bare,
            source=SOURCE_SEED,
            stale=True,
            notes=("development seed only — not a complete NSE catalog; run a refresh",),
        )
    return CatalogSnapshot(error="NSE instrument master not loaded yet", stale=True)


def catalog_path(data_dir: Path | str) -> Path:
    """Canonical cache location, next to the strategy universe store."""
    return Path(data_dir) / "live" / _CACHE_NAME


__all__ = [
    "CatalogSnapshot",
    "InstrumentMasterError",
    "KITE_NSE_INSTRUMENTS_URL",
    "MIN_PLAUSIBLE_EQUITIES",
    "SOURCE_KITE",
    "SOURCE_SEED",
    "catalog_path",
    "classify_row",
    "fetch_instruments_csv",
    "load_catalog",
    "parse_instruments_csv",
    "refresh_catalog",
]
