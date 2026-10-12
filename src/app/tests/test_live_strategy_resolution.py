"""Regression tests for strategy resolution and identity authority.

Covers:
1. Valid 'OBR C1C4' selection resolves to the correct registered strategy
   (id='obr-c1c4', name='OBR C1C4').
2. UI display name maps to the correct backend canonical strategy ID.
3. Unknown strategies are rejected with a useful error listing available strategies.
4. Missing strategy files or registry entries are detected before START.
5. Strategy ID survives snapshot, selection and remote command round trips.
6. Broker connection remains independent of strategy resolution.
7. LiveTradingService._build_sessions() succeeds with registered strategy
   without 'strategy not found' error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.live_trading_service import LiveConfigError, LiveTradingService
from remote.snapshot import build_snapshot
from strategy.registry import reset_strategy_registry


@pytest.fixture(autouse=True)
def clean_registry():
    reset_strategy_registry()
    yield
    reset_strategy_registry()


def test_obr_c1c4_resolves_to_correct_registered_strategy(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")

    # 1. Resolve via display name
    rec = service._resolve_strategy_record("OBR C1C4")
    assert rec is not None
    assert rec.id == "obr-c1c4"
    assert rec.name == "OBR C1C4"
    assert len(rec.code) > 0

    # 2. Resolve via canonical id
    rec_id = service._resolve_strategy_record("obr-c1c4")
    assert rec_id is not None
    assert rec_id.id == "obr-c1c4"
    assert rec_id.name == "OBR C1C4"

    # 3. Resolve via alias / kind
    rec_kind = service._resolve_strategy_record("obr_c1c4")
    assert rec_kind is not None
    assert rec_kind.id == "obr-c1c4"


def test_ui_display_name_maps_to_canonical_id_in_facts(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")
    facts = service._registry_strategy_facts("OBR C1C4")
    assert facts.get("strategy_id") == "obr-c1c4"
    assert facts.get("canonical_id") == "obr-c1c4"
    assert facts.get("display_name") == "OBR C1C4"


def test_unknown_strategy_is_rejected_with_useful_error_listing_available(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")
    service.configure(strategy_name="NonExistentStrategy999")

    blockers = service.validate()
    strat_blockers = [b for b in blockers if "strategy not found" in b]
    assert len(strat_blockers) == 1
    assert "NonExistentStrategy999" in strat_blockers[0]
    assert "available:" in strat_blockers[0]
    assert "OBR C1C4" in strat_blockers[0]

    # Checklist gate check also fails with explicit available list
    ok, reason = service._strategy_gate_check("NonExistentStrategy999")
    assert not ok
    assert "NonExistentStrategy999" in reason
    assert "available:" in reason


def test_missing_strategy_detected_before_start(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")
    service.configure(strategy_name="NoSuchStrategy")

    ok, reasons = service.start()
    assert not ok
    assert any("strategy not found" in r for r in reasons)


def test_strategy_id_survives_snapshot_and_remote_round_trip(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")
    service.configure(strategy_name="OBR C1C4")

    snap = service.snapshot()
    strat = snap.get("strategy", {})
    assert strat.get("id") == "OBR C1C4"
    assert strat.get("strategy_id") == "obr-c1c4"
    assert strat.get("canonical_id") == "obr-c1c4"
    assert strat.get("display_name") == "OBR C1C4"

    # Remote build_snapshot preserves both display name and canonical ID
    remote_snap = build_snapshot(snap)
    remote_strat = remote_snap.get("strategy", {})
    assert remote_strat.get("id") == "OBR C1C4"
    assert remote_strat.get("name") == "OBR C1C4"
    assert remote_strat.get("strategy_id") == "obr-c1c4"
    assert remote_strat.get("canonical_id") == "obr-c1c4"


def test_broker_connection_remains_independent_of_strategy_resolution(tmp_path: Path):
    manager = MagicMock()
    adapter = MagicMock()
    adapter.connected = True
    manager.is_connected.return_value = True
    manager.snapshot.return_value = {
        "status": "CONNECTED",
        "selected": "fyers",
        "brokers": [
            {
                "id": "fyers",
                "name": "FYERS",
                "display_name": "FYERS",
                "connected": True,
                "status": "CONNECTED",
                "reason": "all checks passed",
            }
        ],
    }
    sel_item = MagicMock()
    sel_item.name = "fyers"
    sel_item.reason = "user-selected"
    selection = MagicMock()
    selection.current.return_value = sel_item

    service = LiveTradingService(
        data_dir=tmp_path,
        strategy_dir=tmp_path / "strategies",
        broker_manager=manager,
        selection_service=selection,
    )
    service.attach_broker_view(manager, selection)

    # Configure with unknown strategy
    service.configure(strategy_name="UnknownStrategy")
    snap = service.snapshot()

    # Broker must stay connected despite strategy failure
    assert snap["broker"]["connected"] is True
    assert snap["broker"]["name"] == "FYERS"
    assert any("strategy not found" in b for b in snap["start_blockers"])


def test_build_sessions_resolves_obr_c1c4_without_strategy_not_found(tmp_path: Path):
    service = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path / "strategies")
    service.configure(strategy_name="OBR C1C4", timeframe="30m")

    # Ensure symbols are populated
    service._config.symbols = ("NSE:RELIANCE",)

    # _build_sessions should not raise "strategy not found"
    # (it may proceed to live venue probe or session setup)
    try:
        service._build_sessions()
    except LiveConfigError as exc:
        assert "strategy not found" not in str(exc), f"Unexpected strategy not found error: {exc}"
