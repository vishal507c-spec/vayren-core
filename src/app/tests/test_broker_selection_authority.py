"""Regression tests: SYSTEM is the single source of truth for the broker.

Every surface (SYSTEM, LIVE, capital, order routing) must read the SAME
authoritative selection. These tests pin the specific defects that let a
second, unrelated venue take over:

- LIVE promoted "whichever broker is connected" over the selection, so a
  connected Zerodha silently displaced a chosen FYERS;
- ``BrokerManager`` rewrote the persisted selection on every connect, so a
  second venue stole the operator's choice;
- ``active_broker`` reported the first connected broker, contradicting the
  selection and dragging other surfaces onto the wrong venue;
- the first-run default hardcoded Zerodha even when FYERS was configured;
- a broker switch could be applied while a trading session was running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.broker_manager import BrokerManager  # noqa: E402
from app.services.broker_selection_service import (  # noqa: E402
    BrokerSelectionService,
    app_selection_store,
)
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from broker.selection import SelectionError  # noqa: E402
from broker.status import BrokerStatus  # noqa: E402

# ── helpers ────────────────────────────────────────────────────────────────


def _service(tmp_path: Path, *, selected: str, brokers: list[dict[str, Any]]) -> Any:
    """A LIVE service pinned to `selected`, with the given venue facts."""
    svc = LiveTradingService(data_dir=tmp_path)
    svc._selection = MagicMock()
    svc._selection.current.return_value = SimpleNamespace(
        name=selected, environment="paper", reason="user-selected"
    )
    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {"active_broker": selected, "brokers": brokers}
    manager.is_connected.return_value = False
    svc._broker_manager = manager
    return svc


def _venue(
    bid: str,
    *,
    connected: bool,
    configured: bool = True,
    funds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": bid,
        "name": bid.upper(),
        "status": "connected" if connected else "disconnected",
        "connected": connected,
        "configured": configured,
        "funds": funds,
    }


# ── 1. LIVE must not substitute a connected broker for the selection ───────


def test_live_does_not_substitute_a_connected_broker_for_the_selection(
    tmp_path: Path,
) -> None:
    """The regression: FYERS selected, Zerodha connected => still FYERS.

    The old code took `next(e for e in brokers if e.connected)` and reported
    Zerodha, so capital and order routing followed a venue the operator
    never selected.
    """
    svc = _service(
        tmp_path,
        selected="fyers",
        brokers=[
            _venue("fyers", connected=False),  # selected but NOT connected
            _venue("zerodha", connected=True, funds={"available_margin": 999.0}),
        ],
    )

    view = svc._broker_view()

    assert view["id"] == "fyers", "LIVE must follow the SYSTEM selection"
    assert view["connected"] is False, (
        "a disconnected selection is reported disconnected (fail-closed), "
        "never silently replaced by a connected sibling"
    )
    # Zerodha's funds must NOT leak into the selected broker's view.
    assert view["funds"] == {}, "another broker's funds must never be shown"


def test_capital_follows_the_selected_broker_not_the_connected_one(
    tmp_path: Path,
) -> None:
    svc = _service(
        tmp_path,
        selected="fyers",
        brokers=[
            _venue("fyers", connected=False),
            _venue("zerodha", connected=True, funds={"available_margin": 424242.0}),
        ],
    )

    cap = svc.snapshot()["capital"]

    assert cap["broker_name"] == "FYERS"
    assert cap["available_margin"] is None, "Zerodha's margin must not appear on FYERS"


def test_disconnected_selection_reports_its_own_verified_status(tmp_path: Path) -> None:
    """Requirement: display the selected broker and its VERIFIED status."""
    svc = _service(
        tmp_path,
        selected="fyers",
        brokers=[_venue("fyers", connected=False), _venue("zerodha", connected=True)],
    )

    view = svc._broker_view()

    assert view["id"] == "fyers"
    assert view["connected"] is False
    assert view["status"] == "DISCONNECTED"


def test_compatibility_default_does_not_outrank_a_connected_venue(
    tmp_path: Path,
) -> None:
    """No user choice on record must not pin the migration placeholder.

    The compatibility default (Zerodha when nothing is configured) is a
    placeholder, not a decision. With no explicit selection the manager's
    connected venue wins, which is what first-run UX needs — while an
    explicit choice still binds hard (proven by the tests above).
    """
    svc = LiveTradingService(data_dir=tmp_path)
    svc._selection = MagicMock()
    svc._selection.current.return_value = SimpleNamespace(
        name="zerodha",
        environment="paper",
        reason="compatibility-default (no explicit selection yet)",
    )
    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": [
            _venue("fyers", connected=True, funds={"available_margin": 5000.0}),
            _venue("zerodha", connected=False),
        ],
    }
    manager.is_connected.return_value = False
    svc._broker_manager = manager

    view = svc._broker_view()

    assert view["id"] == "fyers", (
        "with no explicit selection the connected venue is used, not the hardcoded placeholder"
    )


def test_explicit_selection_binds_hard_even_when_placeholder_differs(
    tmp_path: Path,
) -> None:
    """The mirror image: an explicit choice is never overridden."""
    svc = _service(
        tmp_path,
        selected="zerodha",
        brokers=[_venue("zerodha", connected=False), _venue("fyers", connected=True)],
    )

    assert svc._broker_view()["id"] == "zerodha"


def test_no_selection_still_degrades_honestly_rather_than_inventing(
    tmp_path: Path,
) -> None:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._selection = MagicMock()
    svc._selection.current.return_value = SimpleNamespace(
        name="paper", environment="paper", reason="user-selected"
    )
    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {"active_broker": "", "brokers": []}
    manager.is_connected.return_value = False
    svc._broker_manager = manager

    view = svc._broker_view()

    assert view["id"] == "", "no selection => empty view, never an invented broker"
    assert view["connected"] is False


# ── 2. connecting a broker must not steal the SYSTEM selection ────────────


def test_connecting_a_second_broker_does_not_overwrite_the_selection(
    tmp_path: Path,
) -> None:
    """Regression: `_promote_connected` used to call select() unconditionally."""
    svc = BrokerSelectionService(app_selection_store(tmp_path))
    svc.select("fyers")

    manager = BrokerManager(data_dir=str(tmp_path))
    # Simulate the auto-select hook that runs after a successful connect.
    manager._auto_select_if_unset("zerodha")

    assert svc.current().name == "fyers", (
        "an explicit SYSTEM choice must survive another venue connecting"
    )


def test_first_run_still_seeds_a_selection_when_none_exists(tmp_path: Path) -> None:
    """The auto-select must still work on a virgin store (no user choice).

    The guard keys off the persisted ``reason``: with nothing stored, the
    compatibility default is adopted (first run), and the connecting venue
    is recorded so first-run UX is unchanged.
    """
    manager = BrokerManager(data_dir=str(tmp_path))
    manager._auto_select_if_unset("fyers")

    svc = BrokerSelectionService(app_selection_store(tmp_path))
    assert svc.current().name == "fyers", "first run must still seed a selection"


def test_active_broker_reports_the_selection_not_the_first_connected(
    tmp_path: Path,
) -> None:
    """`active_broker` is what other surfaces trust; it must not lie."""
    svc = BrokerSelectionService(app_selection_store(tmp_path))
    svc.select("fyers")

    manager = BrokerManager(data_dir=str(tmp_path))
    # Build the two venue states with the manager's own factory so the
    # snapshot sees exactly the keys it expects.
    fyers_state = manager._fresh_state(manager._specs["fyers"])
    zerodha_state = manager._fresh_state(manager._specs["zerodha"])
    fyers_state["status"] = BrokerStatus.LOGIN_REQUIRED
    fyers_state["configured"] = True
    zerodha_state["status"] = BrokerStatus.CONNECTED
    zerodha_state["configured"] = True
    manager._states["fyers"] = fyers_state
    manager._states["zerodha"] = zerodha_state
    snap = manager.snapshot()

    assert snap["active_broker"] == "fyers", (
        "active_broker must be the selection even when another venue is connected"
    )


# ── 3. persistence across restarts ─────────────────────────────────────────


def test_selection_persists_across_service_instances(tmp_path: Path) -> None:
    first = BrokerSelectionService(app_selection_store(tmp_path))
    first.select("fyers")

    # A brand-new service = a fresh process reading the same store.
    second = BrokerSelectionService(app_selection_store(tmp_path))
    assert second.current().name == "fyers"
    assert second.loaded_from_store is True


def test_compatibility_default_prefers_a_configured_broker_over_zerodha(
    tmp_path: Path,
) -> None:
    """First run with only FYERS configured must not default to Zerodha."""
    from broker.common.credentials_store import default_store

    default_store(tmp_path).save("fyers_config", {"api_key": "k"})

    svc = BrokerSelectionService(app_selection_store(tmp_path))
    current = svc.current()

    assert current.name == "fyers", (
        "the configured venue wins; the hardcoded Zerodha default must not win"
    )


# ── 4. never switch brokers under an active session ───────────────────────


def test_select_is_refused_while_a_session_is_running(tmp_path: Path) -> None:
    svc = BrokerSelectionService(app_selection_store(tmp_path))
    svc.select("fyers")

    with pytest.raises(SelectionError, match="session"):
        svc.select("zerodha", session_running=True)

    assert svc.current().name == "fyers", "a refused switch must change nothing"


def test_select_is_allowed_when_no_session_is_running(tmp_path: Path) -> None:
    svc = BrokerSelectionService(app_selection_store(tmp_path))
    svc.select("fyers")

    outcome = svc.select("zerodha", session_running=False)

    assert outcome.selection.name == "zerodha"
    assert outcome.persisted is True


def test_live_session_guard_fails_closed_when_the_service_is_unreachable(
    tmp_path: Path,
) -> None:
    """If we cannot prove the session stopped, the switch must be blocked."""
    original = headless._live_service

    def _unreachable(*_args: Any, **_kwargs: Any) -> None:
        return None

    headless._live_service = _unreachable  # type: ignore[assignment]
    try:
        assert headless._live_session_running(str(tmp_path), str(tmp_path)) is True
    finally:
        headless._live_service = original  # type: ignore[assignment]


# ── 5. SYSTEM snapshot reflects the authoritative selection ────────────────


def test_system_snapshot_marks_the_selected_broker(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("fyers")

    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {
        "brokers": [
            {"id": "fyers", "name": "FYERS", "status": "connected", "connected": True},
            {"id": "zerodha", "name": "ZERODHA", "status": "connected", "connected": True},
        ],
        "active_broker": "fyers",
        "callback_url": "",
    }

    snap = headless._system_snapshot(str(tmp_path), None, manager, str(tmp_path))

    selected = [b for b in snap["brokers"] if b.get("selected")]
    assert [b["id"] for b in selected] == ["fyers"]
    assert snap.get("selected_id") == "fyers"


def test_opening_a_panel_never_changes_the_default_even_while_running(
    tmp_path: Path,
) -> None:
    """Opening a detail panel is VIEW state, never a switch attempt.

    The switch moved to the explicit `select_broker` action, so this path
    must leave the persisted default alone - including while a session is
    running. (It used to call ``selection.select()`` here, so merely looking
    at a broker silently replaced the global default.)
    """
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("fyers")

    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {
        "brokers": [
            {"id": "fyers", "name": "FYERS", "status": "connected", "connected": True},
            {"id": "zerodha", "name": "ZERODHA", "status": "disconnected", "connected": False},
        ],
        "active_broker": "fyers",
        "callback_url": "",
    }

    original = headless._live_service
    running = MagicMock()
    running.status = "RUNNING"

    def _running(*_args: Any, **_kwargs: Any) -> Any:
        return running

    headless._live_service = _running  # type: ignore[assignment]
    try:
        snap = headless._system_snapshot(str(tmp_path), "zerodha", manager, str(tmp_path))
    finally:
        headless._live_service = original  # type: ignore[assignment]

    selected = [b for b in snap["brokers"] if b.get("selected")]
    assert [b["id"] for b in selected] == ["fyers"], (
        "the default must still be fyers - opening zerodha's panel changed nothing"
    )
    assert service.current().name == "fyers", "the persisted default is untouched"
    assert snap["selected_id"] == "fyers", "selected_id is the DEFAULT"
    assert snap["panel_id"] == "zerodha", "panel_id is the card that is open"


def test_selection_file_contains_no_secret_values(tmp_path: Path) -> None:
    """The persisted selection is a decision, not a credential."""
    BrokerSelectionService(app_selection_store(tmp_path)).select("fyers")

    raw = (tmp_path / "broker_selection.json").read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload["kind"] == "vayren.broker_selection"
    assert payload["name"] == "fyers"
    for banned in ("token", "password", "api_key", "secret"):
        assert banned not in raw.lower()


# ── 6. acceptance-criterion matrix ─────────────────────────────────────────
# The explicit matrix requested for this fix, exercised against the REAL
# services (no mocks of the selection path).


def _matrix_service(tmp_path: Path, selected: str, brokers: list[dict[str, Any]]) -> Any:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._selection = BrokerSelectionService(app_selection_store(tmp_path))
    svc._selection.select(selected)
    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {"active_broker": selected, "brokers": brokers}
    manager.is_connected.return_value = False
    svc._broker_manager = manager
    return svc


def test_acceptance_fyers_selected_but_disconnected_never_shows_zerodha(
    tmp_path: Path,
) -> None:
    """ACCEPTANCE: FYERS selected + disconnected => FYERS, DISCONNECTED."""
    brokers = [
        _venue("fyers", connected=False),
        _venue("zerodha", connected=True, funds={"available_margin": 111.0}),
    ]
    svc = _matrix_service(tmp_path, "fyers", brokers)

    view = svc._broker_view()
    cap = svc.snapshot()["capital"]

    assert view["id"] == "fyers"
    assert view["connected"] is False
    assert cap["broker_name"] == "FYERS"
    assert cap["status"] == "DISCONNECTED"
    assert cap["available_margin"] is None, "Zerodha's funds must never surface"
    assert "ZERODHA" not in str(cap["broker_name"]).upper() or cap["broker_name"] == "FYERS"


def test_acceptance_explicit_zerodha_selection_is_honoured(tmp_path: Path) -> None:
    """ACCEPTANCE: an explicit Zerodha selection sticks even with FYERS up."""
    brokers = [
        _venue("fyers", connected=True, funds={"available_margin": 999.0}),
        _venue("zerodha", connected=True, funds={"available_margin": 42.0}),
    ]
    svc = _matrix_service(tmp_path, "zerodha", brokers)

    view = svc._broker_view()
    cap = svc.snapshot()["capital"]

    assert view["id"] == "zerodha"
    assert cap["broker_name"] == "ZERODHA"
    assert cap["available_margin"] == 42.0, "Zerodha shows ZERODHA's own funds"


def test_acceptance_multiple_connected_brokers_respect_the_selection(
    tmp_path: Path,
) -> None:
    """With every venue connected, the SELECTION decides — not ordering."""
    brokers = [
        _venue("fyers", connected=True, funds={"available_margin": 1.0}),
        _venue("zerodha", connected=True, funds={"available_margin": 2.0}),
    ]
    for selected in ("fyers", "zerodha"):
        sub = tmp_path / selected
        sub.mkdir()
        svc = _matrix_service(sub, selected, brokers)
        assert svc._broker_view()["id"] == selected
        assert svc.snapshot()["capital"]["broker_name"] == selected.upper()


def test_acceptance_selection_survives_application_restart(tmp_path: Path) -> None:
    """A fresh service (new process) must resolve the same broker."""
    brokers = [
        _venue("fyers", connected=False),
        _venue("zerodha", connected=True, funds={"available_margin": 7.0}),
    ]
    first = _matrix_service(tmp_path, "fyers", brokers)
    assert first._broker_view()["id"] == "fyers"

    # Restart: brand-new selection service + brand-new trading service.
    restarted = LiveTradingService(data_dir=tmp_path)
    restarted._broker_manager = MagicMock(spec=BrokerManager)
    restarted._broker_manager.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": brokers,
    }
    restarted._broker_manager.is_connected.return_value = False
    # Attach the persisted selection the way headless does on every poll.
    restarted.attach_broker_view(
        restarted._broker_manager, BrokerSelectionService(app_selection_store(tmp_path))
    )

    view = restarted._broker_view()
    assert view["id"] == "fyers", "restart must not drift to another broker"
    assert view["connected"] is False


def test_acceptance_system_and_live_agree_on_the_broker(tmp_path: Path) -> None:
    """SYSTEM's selected_id and LIVE's broker must be the same venue."""
    for selected in ("fyers", "zerodha"):
        sub = tmp_path / selected
        sub.mkdir()
        BrokerSelectionService(app_selection_store(sub)).select(selected)

        manager = MagicMock(spec=BrokerManager)
        manager.snapshot.return_value = {
            "brokers": [
                {
                    "id": "fyers",
                    "name": "FYERS",
                    "status": "connected",
                    "connected": True,
                },
                {
                    "id": "zerodha",
                    "name": "ZERODHA",
                    "status": "connected",
                    "connected": True,
                },
            ],
            "active_broker": selected,
            "callback_url": "",
        }
        system = headless._system_snapshot(str(sub), None, manager, str(sub))

        live = LiveTradingService(data_dir=sub)
        live._broker_manager = manager
        live.attach_broker_view(manager, BrokerSelectionService(app_selection_store(sub)))
        live_view = live._broker_view()

        assert system["selected_id"] == selected
        assert live_view["id"] == selected, f"LIVE diverged from SYSTEM for {selected}"
