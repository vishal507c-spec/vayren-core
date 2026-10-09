"""StrategyUniverse model + store (Phase 1): NSE-only, per-strategy isolation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.models.universe import (  # noqa: E402
    StrategyUniverse,
    normalize_symbol,
    validate_symbols,
)
from strategy.universe_store import (  # noqa: E402
    StrategyUniverseStore,
    UniverseStoreError,
    universe_strategy_id,
)


def test_normalize_accepts_nse_case_insensitive() -> None:
    assert normalize_symbol("NSE:RELIANCE") == "NSE:RELIANCE"
    assert normalize_symbol("  nse:tcs  ") == "NSE:TCS"


def test_normalize_rejects_everything_not_nse() -> None:
    assert normalize_symbol("BSE:SBIN") is None
    assert normalize_symbol("RELIANCE") is None
    assert normalize_symbol("") is None
    assert normalize_symbol("   ") is None
    assert normalize_symbol(None) is None
    assert normalize_symbol("NSE:") is None


def test_validate_symbols_splits_and_dedups() -> None:
    accepted, rejected = validate_symbols(
        ("NSE:RELIANCE", "nse:tcs", "NSE:RELIANCE", "BSE:SBIN", "INFY")
    )
    assert accepted == ("NSE:RELIANCE", "NSE:TCS")
    assert rejected == ("BSE:SBIN", "INFY")


def test_universe_is_frozen_and_honest() -> None:
    universe = StrategyUniverse(strategy_id="obr-c1c4", symbols=("NSE:INFY",))
    assert universe.symbol_count == 1
    assert not universe.is_empty
    assert StrategyUniverse(strategy_id="x").is_empty
    with pytest.raises(ValueError):
        StrategyUniverse(strategy_id="  ")
    with pytest.raises(ValueError):
        StrategyUniverse(strategy_id="x", symbols=("BSE:SBIN",))


def _store(tmp_path: Path) -> StrategyUniverseStore:
    return StrategyUniverseStore(tmp_path / "live" / "strategy_universes.json")


def test_save_and_get_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save("OBR C1C4", ("NSE:RELIANCE", "NSE:TCS"))
    assert saved.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert saved.updated_at != ""
    assert store.symbols_for("OBR C1C4") == ("NSE:RELIANCE", "NSE:TCS")


def test_strategies_never_share_symbols(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("Strategy A", ("NSE:RELIANCE",))
    store.save("Strategy B", ("NSE:SBIN",))
    assert store.symbols_for("Strategy A") == ("NSE:RELIANCE",)
    assert store.symbols_for("Strategy B") == ("NSE:SBIN",)
    # Re-saving A leaves B untouched.
    store.save("Strategy A", ("NSE:INFY",))
    assert store.symbols_for("Strategy A") == ("NSE:INFY",)
    assert store.symbols_for("Strategy B") == ("NSE:SBIN",)


def test_restart_restores_saved_universes(tmp_path: Path) -> None:
    _store(tmp_path).save("Strategy A", ("NSE:RELIANCE", "NSE:TCS"))
    reopened = _store(tmp_path)
    assert reopened.symbols_for("Strategy A") == ("NSE:RELIANCE", "NSE:TCS")
    assert reopened.symbols_for("Strategy B") == ()


def test_missing_or_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    assert _store(tmp_path).load_all() == {}
    path = tmp_path / "live" / "strategy_universes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert _store(tmp_path).load_all() == {}
    assert _store(tmp_path).symbols_for("Strategy A") == ()


def test_save_refuses_ownerless_and_non_nse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(UniverseStoreError):
        store.save("", ("NSE:RELIANCE",))
    with pytest.raises(UniverseStoreError):
        store.save("Strategy A", ("NSE:RELIANCE", "BSE:SBIN"))
    # Refused writes persist nothing.
    assert store.symbols_for("Strategy A") == ()


def test_store_file_carries_kind_and_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save("Strategy A", ("NSE:RELIANCE",))
    assert saved.instrument_ids == ("NSE:EQUITY:RELIANCE",)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["kind"] == "vayren.strategy_universes"
    # Schema v2 (Phase 2): canonical instrument ids are the authority,
    # display symbols ride along as the user-facing projection.
    assert data["version"] == 2
    entry = data["universes"]["Strategy A"]
    assert entry["instruments"] == ["NSE:EQUITY:RELIANCE"]
    assert entry["symbols"] == ["NSE:RELIANCE"]


def test_strategy_id_prefers_registry_id() -> None:
    assert universe_strategy_id("OBR C1C4") == "obr-c1c4"
    assert universe_strategy_id("Unknown Strategy") == "Unknown Strategy"
    assert universe_strategy_id("") == ""
    assert universe_strategy_id(None) == ""
