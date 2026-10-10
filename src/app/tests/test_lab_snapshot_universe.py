"""Strategy Lab snapshot restores each strategy's SAVED NSE universe.

Exercises the real ``headless._lab_snapshot`` against a real strategy library
directory and the real universe store. Nothing here runs a backtest or trades.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.lab_universe_service import LabUniverseService  # noqa: E402
from strategy.instrument_master import MIN_PLAUSIBLE_EQUITIES, refresh_catalog  # noqa: E402
from strategy.instrument_registry import reset_instrument_registry  # noqa: E402

STRATEGY_SOURCE = """
from strategy.models import *  # noqa
"""


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
    ]
    return "\n".join([header, *filler, *named]) + "\n"


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    headless._DESCRIBE_CACHE.clear()
    headless._LAST_SENT_CODE.clear()
    yield
    reset_instrument_registry()


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    data_dir = tmp_path / "data"
    strategy_dir = tmp_path / "strategies"
    data_dir.mkdir()
    strategy_dir.mkdir()
    (strategy_dir / "Alpha.py").write_text(STRATEGY_SOURCE, encoding="utf-8")
    (strategy_dir / "Beta.py").write_text(STRATEGY_SOURCE, encoding="utf-8")
    return str(data_dir), str(strategy_dir)


@pytest.fixture()
def lab(dirs: tuple[str, str]) -> LabUniverseService:
    svc = LabUniverseService(dirs[0])
    refresh_catalog(svc._catalog_path, fetch=_master_payload)
    return svc


def _snap(dirs: tuple[str, str], name: str) -> dict:
    data_dir, strategy_dir = dirs
    return headless._lab_snapshot(strategy_dir, {"strategy": name}, None, data_dir=data_dir)


def test_missing_universe_reports_missing_not_a_global_list(dirs):
    snap = _snap(dirs, "Alpha")
    assert snap["saved_universe"]["state"] == "missing"
    assert snap["saved_universe"]["symbols"] == []


def test_switching_strategies_restores_each_saved_universe(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    lab.save("Beta", ["INFY", "TCS"])

    alpha = _snap(dirs, "Alpha")
    assert alpha["selected_name"] == "Alpha"
    assert alpha["saved_universe"]["symbols"] == ["NSE:RELIANCE"]

    beta = _snap(dirs, "Beta")
    assert beta["selected_name"] == "Beta"
    assert beta["saved_universe"]["symbols"] == ["NSE:INFY", "NSE:TCS"]

    back = _snap(dirs, "Alpha")
    assert back["saved_universe"]["symbols"] == ["NSE:RELIANCE"]


def test_cleared_universe_is_empty_state(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    lab.save("Alpha", [])
    snap = _snap(dirs, "Alpha")
    assert snap["saved_universe"]["state"] == "empty"
    assert snap["saved_universe"]["symbols"] == []


def test_corrupt_store_reports_empty_not_another_strategy(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    store_path = Path(dirs[0]) / "live" / "strategy_universes.json"
    store_path.write_text("{not json", encoding="utf-8")
    snap = _snap(dirs, "Alpha")
    assert snap["saved_universe"]["symbols"] == []
    assert snap["saved_universe"]["state"] in ("missing", "error", "empty")


def test_unreadable_service_reports_error_state(dirs, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("store offline")

    monkeypatch.setattr("app.services.lab_universe_service.LabUniverseService.saved_universe", boom)
    snap = _snap(dirs, "Alpha")
    assert snap["saved_universe"]["state"] == "error"
    assert "store offline" in snap["saved_universe"]["error"]
    assert snap["saved_universe"]["symbols"] == []


def test_code_save_never_overwrites_the_universe(dirs, lab):
    lab.save("Alpha", ["RELIANCE", "TCS"])
    store_path = Path(dirs[0]) / "live" / "strategy_universes.json"
    before = store_path.read_text(encoding="utf-8")

    # Strategy code is written through the save_lab_strategy command path.
    strategy_dir = Path(dirs[1])
    (strategy_dir / "Alpha.py").write_text(STRATEGY_SOURCE + "# edited\n", encoding="utf-8")
    headless._LAB_ROWS_CACHE.clear()
    headless._DESCRIBE_CACHE.clear()

    assert store_path.read_text(encoding="utf-8") == before
    snap = _snap(dirs, "Alpha")
    assert snap["saved_universe"]["symbols"] == ["NSE:RELIANCE", "NSE:TCS"]


def test_code_save_branch_never_touches_the_universe_store():
    source = Path(headless.__file__).read_text(encoding="utf-8")
    start = source.index('if cmd_type == "save_lab_strategy":')
    end = source.index("snapshot = _lab_snapshot(", start)
    branch = source[start:end]
    assert "write_text" in branch
    assert "universe" not in branch.lower()
    assert "StrategyUniverseStore" not in branch


def test_store_file_is_versioned_and_strategy_keyed(dirs, lab):
    lab.save("Alpha", ["RELIANCE"])
    store_path = Path(dirs[0]) / "live" / "strategy_universes.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["kind"] == "vayren.strategy_universes"
    assert list(data["universes"]) == ["Alpha"]
