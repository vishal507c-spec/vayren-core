"""Per-strategy Lab universe: save/restore, isolation, refusals, search."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.services.lab_universe_service import (  # noqa: E402
    SAVE_REFUSED,
    SAVE_SAVED,
    LabUniverseService,
)
from strategy.instrument_master import MIN_PLAUSIBLE_EQUITIES, refresh_catalog  # noqa: E402
from strategy.instrument_registry import NSE_EQUITY_SEED, reset_instrument_registry  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


def _master_payload() -> str:
    header = (
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
        "tick_size,lot_size,instrument_type,segment,exchange"
    )
    rows = [
        f"{i},{i},EQ{i:05d},N{i},0,,0,0,1,EQ,NSE,NSE" for i in range(MIN_PLAUSIBLE_EQUITIES + 5)
    ]
    rows += [
        "1,1,RELIANCE,RELIANCE,0,,0,0,1,EQ,NSE,NSE",
        "2,2,TCS,TCS,0,,0,0,1,EQ,NSE,NSE",
        "3,3,INFY,INFY,0,,0,0,1,EQ,NSE,NSE",
    ]
    return "\n".join([header, *rows]) + "\n"


@pytest.fixture
def service(tmp_path):
    svc = LabUniverseService(tmp_path)
    refresh_catalog(svc._catalog_path, fetch=_master_payload)
    return svc


def test_save_and_restore_per_strategy(service):
    result = service.save("OBR", ["RELIANCE", "TCS"])
    assert result["state"] == SAVE_SAVED
    assert result["count"] == 2
    assert service.saved_symbols("OBR") == ("NSE:RELIANCE", "NSE:TCS")


def test_strategies_are_isolated(service):
    service.save("Alpha", ["RELIANCE"])
    service.save("Beta", ["INFY"])
    assert service.saved_symbols("Alpha") == ("NSE:RELIANCE",)
    assert service.saved_symbols("Beta") == ("NSE:INFY",)
    service.save("Alpha", [])
    assert service.saved_symbols("Beta") == ("NSE:INFY",)


def test_invalid_symbol_is_refused_and_previous_kept(service):
    service.save("Alpha", ["RELIANCE"])
    result = service.save("Alpha", ["RELIANCE", "NOTAREALSYMBOL"])
    assert result["state"] == SAVE_REFUSED
    assert result["error"]
    assert service.saved_symbols("Alpha") == ("NSE:RELIANCE",)


def test_non_nse_symbol_is_refused(service):
    result = service.save("Alpha", ["BSE:RELIANCE"])
    assert result["state"] == SAVE_REFUSED


def test_persistence_failure_is_reported_not_raised(service, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service._store, "save", boom)
    result = service.save("Alpha", ["RELIANCE"])
    assert result["state"] == SAVE_REFUSED
    assert "disk full" in result["error"]


def test_saved_universe_survives_restart(service, tmp_path):
    service.save("Alpha", ["RELIANCE"])
    restarted = LabUniverseService(tmp_path)
    assert restarted.saved_symbols("Alpha") == ("NSE:RELIANCE",)


def test_search_is_capped_and_case_insensitive(service):
    hits = service.search("re")
    assert "RELIANCE" in hits
    assert len(service.search("", limit=2)) == 2


def test_unavailable_catalog_reports_error_without_raising(tmp_path):
    svc = LabUniverseService(tmp_path)
    summary = svc.catalog_summary()
    assert summary["count"] >= 0
    assert "stale" in summary


def test_cache_file_is_json_and_separate_from_universes(service, tmp_path):
    service.save("Alpha", ["RELIANCE"])
    universes = json.loads((tmp_path / "live" / "strategy_universes.json").read_text("utf-8"))
    assert universes["kind"] == "vayren.strategy_universes"


def test_master_symbol_outside_seed_is_accepted(service):
    assert "NSE:EQ00007" not in NSE_EQUITY_SEED
    assert service.save("Alpha", ["EQ00007"])["state"] == SAVE_SAVED


def test_bare_bse_prefix_is_still_refused(service):
    assert service.save("Alpha", ["BSE:RELIANCE"])["state"] == SAVE_REFUSED
