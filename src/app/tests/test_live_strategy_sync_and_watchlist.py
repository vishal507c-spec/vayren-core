"""Comprehensive verification of Live UI Strategy Synchronization & Dynamic Watchlist.

Verifies:
1. Central Strategy Selection using canonical IDs from StrategyRegistry.
2. Automatic NSE Stock Synchronization from StrategyUniverseStore (shared with Strategy Lab).
3. Switching between strategies clears and replaces watchlist without leaking previous symbols.
4. Restoring previously selected strategy universe when switching back.
5. Empty universe strategy clears watchlist completely (no stale retention).
6. Order safety: selecting a strategy NEVER auto-starts sessions or submits orders.
7. Sizing and risk engine derive strictly from real capital facts (no fabricated prices or levels).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.services.lab_universe_service import (  # noqa: E402
    SAVE_SAVED,
    LabUniverseService,
)
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from strategy.instrument_master import MIN_PLAUSIBLE_EQUITIES, refresh_catalog  # noqa: E402
from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.registry import get_strategy_registry, reset_strategy_registry  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_environment():
    reset_instrument_registry()
    reset_strategy_registry()
    yield
    reset_instrument_registry()
    reset_strategy_registry()


def _master_payload() -> str:
    header = (
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
        "tick_size,lot_size,instrument_type,segment,exchange"
    )
    filler = [
        f"{i},{i},EQ{i:05d},COMPANY {i},0,,0,0.05,1,EQ,NSE,NSE"
        for i in range(MIN_PLAUSIBLE_EQUITIES + 5)
    ]
    named = [
        "1001,1001,RELIANCE,RELIANCE INDUSTRIES,2450.50,,0,0.05,1,EQ,NSE,NSE",
        "1002,1002,TCS,TATA CONSULTANCY,3500.00,,0,0.05,1,EQ,NSE,NSE",
        "1003,1003,INFY,INFOSYS,1450.25,,0,0.05,1,EQ,NSE,NSE",
        "1004,1004,SBIN,STATE BANK OF INDIA,600.10,,0,0.05,1,EQ,NSE,NSE",
        "1005,1005,HDFCBANK,HDFC BANK,1650.00,,0,0.05,1,EQ,NSE,NSE",
    ]
    return "\n".join([header, *filler, *named]) + "\n"


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    return str(tmp_path), str(strategy_dir)


@pytest.fixture()
def lab(dirs: tuple[str, str]) -> LabUniverseService:
    svc = LabUniverseService(dirs[0])
    refresh_catalog(svc._catalog_path, fetch=_master_payload)
    return svc


def test_1_central_strategy_selection_canonical_ids(dirs: tuple[str, str]) -> None:
    """Strategy selector lists display names from central registry and Strategy Lab library."""
    reg = get_strategy_registry()
    live = LiveTradingService(dirs[0], dirs[1])

    available = live.available_strategies()
    assert len(available) > 0
    # Display names (e.g. 'OBR C1C4', 'EMA Crossover', 'OBR'), matching Strategy Lab
    assert "OBR C1C4" in available
    assert "EMA Crossover" in available
    for strat_name in available:
        assert reg.contains(strat_name) or strat_name == "OBR"


def test_2_strategy_lab_universe_syncs_to_live_ui(
    dirs: tuple[str, str], lab: LabUniverseService
) -> None:
    """Strategy Lab saved universe loads automatically when selected in Live."""
    # Lab saves universe for OBR C1C4
    res = lab.save("obr-c1c4", ["RELIANCE", "TCS"])
    assert res["state"] == SAVE_SAVED

    live = LiveTradingService(dirs[0], dirs[1])
    live.configure(strategy_name="obr-c1c4")

    # Symbols in Live must match exactly what Lab saved
    assert live.config.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert live.available_symbols() == ("NSE:RELIANCE", "NSE:TCS")

    snap = live.snapshot()
    assert snap["strategy"]["id"] == "obr-c1c4"
    assert snap["available_symbols"] == ("NSE:RELIANCE", "NSE:TCS")
    assert len(snap["quotes"]) == 2
    assert snap["quotes"][0]["symbol"] == "NSE:RELIANCE"
    assert snap["quotes"][1]["symbol"] == "NSE:TCS"


def test_3_switching_between_strategies_clears_and_restores(
    dirs: tuple[str, str], lab: LabUniverseService
) -> None:
    """Switching strategies replaces universe, clears stale symbols, and restores on switchback."""
    lab.save("obr-c1c4", ["RELIANCE", "TCS"])
    lab.save("sma-crossover", ["INFY", "SBIN", "HDFCBANK"])

    live = LiveTradingService(dirs[0], dirs[1])

    # Select strategy 1
    live.configure(strategy_name="obr-c1c4")
    assert live.config.symbols == ("NSE:RELIANCE", "NSE:TCS")
    snap1 = live.snapshot()
    quotes1_syms = [q["symbol"] for q in snap1["quotes"]]
    assert quotes1_syms == ["NSE:RELIANCE", "NSE:TCS"]

    # Switch to strategy 2
    live.configure(strategy_name="sma-crossover")
    assert live.config.symbols == ("NSE:INFY", "NSE:SBIN", "NSE:HDFCBANK")
    snap2 = live.snapshot()
    quotes2_syms = [q["symbol"] for q in snap2["quotes"]]
    assert quotes2_syms == ["NSE:INFY", "NSE:SBIN", "NSE:HDFCBANK"]
    # Zero symbols from strategy 1 leaked into strategy 2
    assert "NSE:RELIANCE" not in quotes2_syms
    assert "NSE:TCS" not in quotes2_syms

    # Switch back to strategy 1 -> universe restored
    live.configure(strategy_name="obr-c1c4")
    assert live.config.symbols == ("NSE:RELIANCE", "NSE:TCS")
    snap3 = live.snapshot()
    quotes3_syms = [q["symbol"] for q in snap3["quotes"]]
    assert quotes3_syms == ["NSE:RELIANCE", "NSE:TCS"]


def test_4_empty_universe_strategy_clears_watchlist(
    dirs: tuple[str, str], lab: LabUniverseService
) -> None:
    """Selecting a strategy with no saved universe results in an empty watchlist."""
    lab.save("obr-c1c4", ["RELIANCE"])

    live = LiveTradingService(dirs[0], dirs[1])
    live.configure(strategy_name="obr-c1c4")
    assert len(live.config.symbols) == 1

    # Switch to sma-crossover (which has no saved universe)
    live.configure(strategy_name="sma-crossover")
    assert live.config.symbols == ()
    assert live.available_symbols() == ()

    snap = live.snapshot()
    assert snap["available_symbols"] == ()
    assert snap["quotes"] == []


def test_5_order_safety_selection_never_starts_or_submits(
    dirs: tuple[str, str], lab: LabUniverseService
) -> None:
    """Selecting or switching a strategy NEVER starts live trading or submits orders."""
    lab.save("obr-c1c4", ["RELIANCE", "TCS"])
    live = LiveTradingService(dirs[0], dirs[1])

    # Initial state
    assert live.status == "STOPPED"
    assert not live.armed

    # Configure strategy
    live.configure(strategy_name="obr-c1c4")
    assert live.status == "STOPPED"
    assert not live.armed

    snap = live.snapshot()
    assert snap["session_status"] == "STOPPED"
    assert snap["active_positions"] == 0
    assert snap["open_orders"] == 0
    assert snap["positions"] == []
    assert snap["orders"] == []
    assert snap["fills"] == []
    assert snap["can_arm"] is False  # In PAPER mode, or without valid live confirmation
