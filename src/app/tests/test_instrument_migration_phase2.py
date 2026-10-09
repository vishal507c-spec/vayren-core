"""PHASE 2 migration + integration — canonical references, Phase 1 intact.

A Phase-1 v1 universe file migrates transparently: valid symbols become
canonical instrument references, unresolvable entries are reported (never
silently deleted), strategy isolation survives, and no global fallback or
fake symbol is introduced anywhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.services.live_trading_service import (  # noqa: E402
    LiveConfigError,
    LiveTradingService,
)
from strategy.instrument_registry import (  # noqa: E402
    get_instrument_registry,
    reset_instrument_registry,
)
from strategy.universe_store import StrategyUniverseStore  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    return str(tmp_path), str(strategy_dir)


def _v1_file(path: Path, universes: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "vayren.strategy_universes", "version": 1, "universes": universes}),
        encoding="utf-8",
    )


def _store(tmp_path: Path) -> StrategyUniverseStore:
    return StrategyUniverseStore(tmp_path / "live" / "strategy_universes.json")


def test_13_phase1_universe_migrates_to_canonical_ids(tmp_path: Path) -> None:
    _v1_file(
        tmp_path / "live" / "strategy_universes.json",
        {"Momentum": {"symbols": ["NSE:RELIANCE", "NSE:TCS"], "updated_at": "t"}},
    )
    store = _store(tmp_path)
    universe = store.get("Momentum")
    assert universe is not None
    assert universe.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert universe.instrument_ids == ("NSE:EQUITY:RELIANCE", "NSE:EQUITY:TCS")
    assert universe.unresolved == ()
    report = store.migration_report()
    assert report["Momentum"] == {"resolved": 2, "unresolved": []}


def test_14_unresolved_entries_are_reported_not_deleted(tmp_path: Path) -> None:
    _v1_file(
        tmp_path / "live" / "strategy_universes.json",
        {
            "Momentum": {
                "symbols": ["NSE:RELIANCE", "NSE:ABCXYZ", "nse:tcs"],
                "updated_at": "t",
            }
        },
    )
    store = _store(tmp_path)
    universe = store.get("Momentum")
    assert universe is not None
    # Valid entries resolve; the unknown one is reported with its reason.
    assert universe.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert universe.unresolved == (("NSE:ABCXYZ", "NOT_FOUND"),)
    report = store.migration_report()["Momentum"]
    assert report["resolved"] == 2
    assert report["unresolved"] == [("NSE:ABCXYZ", "NOT_FOUND")]


def test_unknown_symbol_save_is_refused_never_invented(
    dirs: tuple[str, str],
) -> None:
    service = LiveTradingService(dirs[0], dirs[1])
    service.configure(strategy_name="Momentum")
    with pytest.raises(LiveConfigError, match="not found in NSE Equity registry"):
        service.configure(symbols=("NSE:RELIANCE", "NSE:ABCXYZ"))
    assert service.config.symbols == ()
    assert service._universes.symbols_for("Momentum") == ()


def test_inactive_symbol_save_is_refused(dirs: tuple[str, str]) -> None:
    get_instrument_registry().set_status("NSE:EQUITY:SBIN", "INACTIVE")
    service = LiveTradingService(dirs[0], dirs[1])
    service.configure(strategy_name="Momentum")
    with pytest.raises(LiveConfigError, match="inactive"):
        service.configure(symbols=("NSE:SBIN",))
    assert service.config.symbols == ()


def test_same_instrument_may_belong_to_many_strategies(
    dirs: tuple[str, str],
) -> None:
    service = LiveTradingService(dirs[0], dirs[1])
    service.configure(strategy_name="Momentum")
    service.configure(symbols=("NSE:RELIANCE",))
    service.configure(strategy_name="Breakout")
    service.configure(symbols=("NSE:RELIANCE", "NSE:SBIN"))
    assert service._universes.symbols_for("Momentum") == ("NSE:RELIANCE",)
    assert service._universes.symbols_for("Breakout") == ("NSE:RELIANCE", "NSE:SBIN")
    assert service._universes.instrument_ids_for("Momentum") == ("NSE:EQUITY:RELIANCE",)


def test_17_isolation_survives_canonical_layer(dirs: tuple[str, str]) -> None:
    service = LiveTradingService(dirs[0], dirs[1])
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:RELIANCE", "NSE:TCS"))
    service.configure(strategy_name="Strategy B")
    service.configure(symbols=("NSE:SBIN", "NSE:HDFCBANK"))
    service.configure(strategy_name="Strategy A")
    assert service.config.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert service._universes.instrument_ids_for("Strategy B") == (
        "NSE:EQUITY:SBIN",
        "NSE:EQUITY:HDFCBANK",
    )


def test_19_no_global_fallback_after_migration(dirs: tuple[str, str]) -> None:
    service = LiveTradingService(dirs[0], dirs[1])
    assert service.available_symbols() == ()
    assert service.available_symbols("OBR C1C4") == ()
    snap = service.snapshot()
    assert snap["strategy"]["id"] == ""
    assert snap["available_symbols"] == ()


def test_headless_setup_refusal_keeps_snapshot(dirs: tuple[str, str]) -> None:
    import app.headless as headless

    headless._ARMED[0] = False
    # Real UI flow: select first (loads the saved universe), then edit —
    # a combined select+symbols call carries the previous screen's rows and
    # is ignored by the Phase-1 anti-leak rule, so refusal needs step two.
    headless._live_action(dirs[0], dirs[1], {"action": "setup", "strategy_name": "Momentum"})
    snap = headless._live_action(dirs[0], dirs[1], {"action": "setup", "symbols": ["NSE:ABCXYZ"]})
    assert "not found in NSE Equity registry" in snap.get("action_note", "")
    # Refusal keeps the live book — never a degraded blank page.
    assert "error" not in snap
    assert snap["mode"] in ("PAPER", "LIVE")
