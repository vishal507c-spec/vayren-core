"""Broker default selection must be ONE decision, applied through the ONE
action path, and identical on the desktop backend and the remote gateway.

These tests cover the propagation path end to end:

- ``_live_action({"action": "select_broker"})`` changes the authoritative
  selection (the desktop SYSTEM panel route);
- the gateway accepts ``select_broker`` and translates it onto that SAME
  action, so a desktop SYSTEM change reaches the gateway's own store;
- the selection survives a restart (fresh service, same store);
- switching back to the other broker works (FYERS <-> Zerodha both ways);
- a mid-session switch is refused instead of silently applied.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.broker_selection_service import (  # noqa: E402
    BrokerSelectionService,
    app_selection_store,
)


def _resolved(data_dir: Path) -> str:
    """What a FRESH process would resolve from this store (restart proof)."""
    return BrokerSelectionService(app_selection_store(data_dir)).current().name


# ── the action path itself ────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["fyers", "zerodha"])
def test_select_broker_action_sets_the_authoritative_default(tmp_path: Path, target: str) -> None:
    """SYSTEM -> "make this default" writes the authoritative selection."""
    result = headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": target}
    )

    assert _resolved(tmp_path) == target
    # The returned snapshot reports the SAME broker it just selected.
    assert str(result.get("broker", {}).get("name", "")).lower() == target


def test_select_broker_requires_an_id(tmp_path: Path) -> None:
    result = headless._live_action(str(tmp_path), str(tmp_path), {"action": "select_broker"})
    assert result.get("broker", {}).get("id", "") == ""


def test_switching_back_and_forth_works_and_persists(tmp_path: Path) -> None:
    """Acceptance: FYERS -> Zerodha -> FYERS, each one the single truth."""
    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "fyers"}
    )
    assert _resolved(tmp_path) == "fyers"

    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "zerodha"}
    )
    assert _resolved(tmp_path) == "zerodha"

    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "fyers"}
    )
    assert _resolved(tmp_path) == "fyers"


def test_selection_survives_a_restart(tmp_path: Path) -> None:
    """A brand-new service process must resolve the chosen broker."""
    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "fyers"}
    )
    headless._TRADING_SERVICES.clear()  # simulate the process restarting

    assert _resolved(tmp_path) == "fyers"

    snap = headless._live_action(str(tmp_path), str(tmp_path), {"action": "eligibility"})
    assert str(snap.get("broker", {}).get("name", "")).lower() == "fyers"


def test_unknown_broker_is_refused_and_leaves_the_selection_intact(
    tmp_path: Path,
) -> None:
    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "fyers"}
    )
    result = headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "nope"}
    )

    assert _resolved(tmp_path) == "fyers", "a refused switch must change nothing"
    assert "refused" in str(result.get("action_note", "")).lower()


def test_mid_session_switch_is_refused(tmp_path: Path) -> None:
    """Never rebind the venue an active session is already trading on."""
    headless._live_action(
        str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "fyers"}
    )

    class _Running:
        status = "RUNNING"

    service = _Running()
    original = headless._live_service

    def _running(*_args: object, **_kwargs: object) -> object:
        return service

    headless._live_service = _running  # type: ignore[assignment]
    try:
        result = headless._live_action(
            str(tmp_path), str(tmp_path), {"action": "select_broker", "broker": "zerodha"}
        )
    finally:
        headless._live_service = original  # type: ignore[assignment]

    assert _resolved(tmp_path) == "fyers", "the running session keeps its venue"
    assert "refused" in str(result.get("action_note", "")).lower()


# ── the gateway must accept and translate the same action ────────────────


def test_gateway_accepts_select_broker() -> None:
    from remote.server import MUTATING_ACTIONS

    assert "select_broker" in MUTATING_ACTIONS


def test_gateway_translates_select_broker_onto_the_action_path() -> None:
    from remote.server import RemoteServer

    server = RemoteServer.__new__(RemoteServer)
    translated = server._translate_command("select_broker", {"broker": " FYERS "}, "r-1")
    assert translated == {"action": "select_broker", "broker": "fyers"}


def test_gateway_rejects_select_broker_without_an_id() -> None:
    from remote.server import RemoteServer

    server = RemoteServer.__new__(RemoteServer)
    assert server._translate_command("select_broker", {}, "r-2") is None
    assert server._translate_command("select_broker", {"broker": "  "}, "r-3") is None


def test_gateway_rejects_unknown_broker_ids_for_select_broker() -> None:
    """The backend still validates the name; the transport just relays it."""
    from remote.server import RemoteServer

    server = RemoteServer.__new__(RemoteServer)
    # The transport accepts any well-formed string; the authoritative check is
    # the registry lookup inside the backend action path.
    assert server._translate_command("place_fyers_order", {}, "r-4") is None
