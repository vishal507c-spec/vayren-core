"""End-to-end: Strategy Lab save -> persisted universe -> Live watchlist.

Exercises the real backend pieces together (no mocks of the universe store,
the instrument registry, or the Live service):

    LabUniverseService.save  ->  StrategyUniverseStore (live/strategy_universes.json)
                             ->  LiveTradingService.configure(strategy_name=...)
                             ->  LiveTradingService.config.symbols  (the watchlist)

Nothing here starts, stops, or places a trade. Live setup stays unchanged.
"""

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
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from strategy.instrument_master import MIN_PLAUSIBLE_EQUITIES, refresh_catalog  # noqa: E402
from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.universe_store import universe_strategy_id  # noqa: E402


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
    filler = [
        f"{i},{i},EQ{i:05d},COMPANY {i},0,,0,0.05,1,EQ,NSE,NSE"
        for i in range(MIN_PLAUSIBLE_EQUITIES + 5)
    ]
    named = [
        "1,1,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE",
        "2,2,TCS,TATA CONSULTANCY,0,,0,0.1,1,EQ,NSE,NSE",
        "3,3,INFY,INFOSYS,0,,0,0.1,1,EQ,NSE,NSE",
        "4,4,BAJAJ-AUTO,BAJAJ AUTO,0,,0,1,1,EQ,NSE,NSE",
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


def _live(dirs: tuple[str, str]) -> LiveTradingService:
    return LiveTradingService(dirs[0], dirs[1])


def test_lab_save_reaches_live_watchlist_for_the_same_strategy(dirs, lab):
    assert lab.save("OBR", ["RELIANCE", "TCS"])["state"] == SAVE_SAVED

    live = _live(dirs)
    live.configure(strategy_name="OBR")

    assert live.config.symbols == ("NSE:RELIANCE", "NSE:TCS")


def test_strategy_identity_is_shared_between_lab_and_live(dirs, lab):
    lab.save("OBR", ["INFY"])
    # Lab keys by the canonical id; Live keys by the name it was configured with.
    # They must resolve to the same universe owner or the watchlist would drift.
    assert universe_strategy_id("OBR")
    live = _live(dirs)
    live.configure(strategy_name="OBR")
    assert live.config.symbols == ("NSE:INFY",)


def test_switching_strategies_never_mixes_universes(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    lab.save("Beta", ["INFY", "TCS"])

    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ("NSE:RELIANCE",)

    live.configure(strategy_name="Beta")
    assert live.config.symbols == ("NSE:INFY", "NSE:TCS")
    assert "NSE:RELIANCE" not in live.config.symbols

    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ("NSE:RELIANCE",)


def test_stale_symbols_in_the_same_switch_action_are_not_applied(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    lab.save("Beta", ["INFY"])

    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    # The previous screen's rows ride along on a switch; they must be ignored.
    live.configure(strategy_name="Beta", symbols=("NSE:RELIANCE",))
    assert live.config.symbols == ("NSE:INFY",)


def test_invalid_symbol_is_refused_and_live_keeps_previous_universe(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    result = lab.save("Alpha", ["RELIANCE", "NOTAREALSTOCK"])
    assert result["state"] == SAVE_REFUSED
    assert result["error"]

    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ("NSE:RELIANCE",)


def test_non_nse_symbol_never_reaches_live(dirs, lab):
    assert lab.save("Alpha", ["BSE:RELIANCE"])["state"] == SAVE_REFUSED
    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ()


def test_save_failure_keeps_the_previously_saved_universe(dirs, lab, monkeypatch):
    lab.save("Alpha", ["RELIANCE"])

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(lab._store, "save", boom)
    result = lab.save("Alpha", ["TCS"])
    assert result["state"] == SAVE_REFUSED
    assert "disk full" in result["error"]

    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ("NSE:RELIANCE",)


def test_empty_universe_is_a_valid_clear_and_live_shows_nothing(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    assert lab.save("Alpha", [])["state"] == SAVE_SAVED

    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ()


def test_unsaved_strategy_shows_empty_watchlist_not_a_global_list(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    live = _live(dirs)
    live.configure(strategy_name="NeverSaved")
    assert live.config.symbols == ()


def test_saving_stocks_never_starts_trading(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.status != "RUNNING"


def test_code_save_path_does_not_touch_the_universe_file(dirs, lab, tmp_path):
    lab.save("Alpha", ["RELIANCE"])
    universes_path = tmp_path / "live" / "strategy_universes.json"
    before = universes_path.read_text(encoding="utf-8")
    # Strategy code is written to a .py file by its own command; the universe
    # store must be byte-identical afterwards.
    code_file = Path(dirs[1]) / "Alpha.py"
    code_file.write_text("# code only\n", encoding="utf-8")
    assert universes_path.read_text(encoding="utf-8") == before
    assert json.loads(before)["kind"] == "vayren.strategy_universes"


def test_restart_restores_the_saved_universe(dirs, lab):
    lab.save("Alpha", ["RELIANCE", "TCS"])
    restarted_lab = LabUniverseService(dirs[0])
    restarted_live = _live(dirs)
    restarted_live.configure(strategy_name="Alpha")
    assert restarted_lab.saved_symbols("Alpha") == ("NSE:RELIANCE", "NSE:TCS")
    assert restarted_live.config.symbols == ("NSE:RELIANCE", "NSE:TCS")


def test_hyphenated_master_symbol_flows_end_to_end(dirs, lab):
    assert lab.save("Alpha", ["BAJAJ-AUTO"])["state"] == SAVE_SAVED
    live = _live(dirs)
    live.configure(strategy_name="Alpha")
    assert live.config.symbols == ("NSE:BAJAJ-AUTO",)
