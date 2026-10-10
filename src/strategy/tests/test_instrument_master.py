"""NSE instrument master ingestion: classification, cache, failure handling."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.instrument_master import (  # noqa: E402
    MIN_PLAUSIBLE_EQUITIES,
    SOURCE_KITE,
    SOURCE_SEED,
    InstrumentMasterError,
    classify_row,
    load_catalog,
    parse_instruments_csv,
    refresh_catalog,
)

HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange"
)


def _eq_rows(count: int) -> list[str]:
    return [f"{i},{i},EQ{i:05d},NAME {i},0,,0,0,1,EQ,NSE,NSE" for i in range(count)]


def _payload(extra: list[str], count: int = MIN_PLAUSIBLE_EQUITIES + 5) -> str:
    return "\n".join([HEADER, *_eq_rows(count), *extra]) + "\n"


def _row(symbol: str, **overrides: str) -> dict:
    base = {
        "tradingsymbol": symbol,
        "exchange": "NSE",
        "segment": "NSE",
        "instrument_type": "EQ",
        "expiry": "",
        "lot_size": "1",
        "name": "COMPANY",
    }
    base.update(overrides)
    return base


def test_classify_keeps_plain_equity_and_drops_non_equity():
    assert classify_row(_row("RELIANCE")) == "RELIANCE"
    assert classify_row(_row("M&M")) == "M&M"
    for symbol in ("AAFS27A-N0", "ZTECH-SM", "NIFTY 50", "182D101226-TB"):
        assert classify_row(_row(symbol)) is None
    assert classify_row(_row("RELIANCE", instrument_type="CE")) is None
    assert classify_row(_row("RELIANCE", exchange="BSE")) is None
    assert classify_row(_row("RELIANCE", lot_size="100")) is None


def test_hyphenated_real_equity_is_kept():
    base = {
        "exchange": "NSE",
        "segment": "NSE",
        "instrument_type": "EQ",
        "expiry": "",
        "lot_size": "1",
        "name": "BAJAJ AUTO",
    }
    assert classify_row({**base, "tradingsymbol": "BAJAJ-AUTO"}) == "BAJAJ-AUTO"
    assert classify_row({**base, "tradingsymbol": "NAM-INDIA"}) == "NAM-INDIA"


def test_debt_series_and_bond_names_are_excluded():
    base = {
        "exchange": "NSE",
        "segment": "NSE",
        "instrument_type": "EQ",
        "expiry": "",
        "lot_size": "1",
        "name": "SOME COMPANY",
    }
    for symbol in ("AAFS27A-N0", "AARNAV-BE", "IIFL060326-YA", "SCLZC26F-Y9"):
        assert classify_row({**base, "tradingsymbol": symbol}) is None
    bond = {**base, "tradingsymbol": "ABCDEF", "name": "SDL KA 6.56% 2030"}
    assert classify_row(bond) is None
    assert classify_row({**base, "tradingsymbol": "ABCDEF", "name": "GOLD BOND 2026"}) is None
    assert (
        classify_row(
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "BSE",
                "segment": "NSE",
                "instrument_type": "EQ",
                "expiry": "",
            }
        )
        is None
    )


def test_parse_rejects_partial_payload():
    with pytest.raises(InstrumentMasterError):
        parse_instruments_csv(_payload([], count=3))


def test_parse_rejects_wrong_shape():
    with pytest.raises(InstrumentMasterError):
        parse_instruments_csv("foo,bar\n1,2\n")


def test_refresh_writes_cache_and_loads_back(tmp_path):
    cache = tmp_path / "live" / "nse_equity_master.json"
    snap = refresh_catalog(cache, fetch=lambda: _payload(["1,1,RELIANCE,R,0,,0,0,1,EQ,NSE,NSE"]))
    assert snap.error == ""
    assert snap.source == SOURCE_KITE
    assert "RELIANCE" in snap.symbols
    loaded = load_catalog(cache)
    assert loaded.source == SOURCE_KITE
    assert loaded.symbols == snap.symbols
    assert loaded.stale is False


def test_failed_refresh_keeps_last_good_cache(tmp_path):
    cache = tmp_path / "nse_equity_master.json"
    good = refresh_catalog(cache, fetch=lambda: _payload([]))

    def broken() -> str:
        raise InstrumentMasterError("instrument source unavailable: timeout")

    failed = refresh_catalog(cache, fetch=broken)
    assert failed.error
    assert failed.stale is True
    assert failed.symbols == good.symbols
    assert load_catalog(cache).symbols == good.symbols


def test_failed_refresh_without_cache_reports_empty(tmp_path):
    def broken() -> str:
        raise InstrumentMasterError("instrument source unavailable")

    snap = refresh_catalog(tmp_path / "missing.json", fetch=broken)
    assert snap.is_empty
    assert "unavailable" in snap.error


def test_seed_is_labelled_as_development_fallback(tmp_path):
    snap = load_catalog(tmp_path / "missing.json", seed=("NSE:RELIANCE", "NSE:TCS"))
    assert snap.source == SOURCE_SEED
    assert snap.is_authoritative is False
    assert snap.symbols == ("RELIANCE", "TCS")
    assert any("not a complete" in note for note in snap.notes)


def test_corrupt_cache_is_ignored(tmp_path):
    cache = tmp_path / "nse_equity_master.json"
    cache.write_text("{not json", encoding="utf-8")
    assert load_catalog(cache).is_empty
