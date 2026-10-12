"""SET AS DEFAULT — focused tests for the authoritative default broker.

Covers the required cases against the REAL implementation (the existing
``select_broker`` action + ``BrokerSelectionService`` persistence), not mocks:

1. FYERS becomes the persisted default.
2. Zerodha becomes the persisted default (both directions).
3. A DISCONNECTED broker can still become the default and is never silently
   replaced by the other broker.
4. A rejected/unavailable request does NOT report a false success.
5. The default survives a restart (fresh service, same store).
6. Opening a broker's detail does NOT change the default.
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
from app.services.broker_selection_service import (  # noqa: E402
    BrokerSelectionService,
    app_selection_store,
)
from remote.server import RemoteServer  # noqa: E402


def _store_default(data_dir: Path) -> str:
    """What a FRESH process resolves from the authoritative store."""
    return BrokerSelectionService(app_selection_store(data_dir)).current().name


def _select(data_dir: Path, broker: str) -> dict:
    """The exact operation the SET AS DEFAULT button performs."""
    return headless._live_action(
        str(data_dir), str(data_dir), {"action": "select_broker", "broker": broker}
    )


def _seed(data_dir: Path, broker: str) -> None:
    BrokerSelectionService(app_selection_store(data_dir)).select(broker)


# ── Test 1: selecting FYERS ───────────────────────────────────────────────


def test_set_fyers_as_default(tmp_path: Path) -> None:
    _seed(tmp_path, "zerodha")
    assert _store_default(tmp_path) == "zerodha"

    _select(tmp_path, "fyers")

    assert _store_default(tmp_path) == "fyers", "persisted store must hold fyers"
    raw = json.loads((tmp_path / "broker_selection.json").read_text(encoding="utf-8"))
    assert raw["name"] == "fyers"
    assert raw["reason"] == "user-selected"


# ── Test 2: selecting Zerodha (the other direction) ───────────────────────


def test_set_zerodha_as_default(tmp_path: Path) -> None:
    _seed(tmp_path, "fyers")

    _select(tmp_path, "zerodha")

    assert _store_default(tmp_path) == "zerodha"
    # SYSTEM and LIVE both consume this one store.
    snap = _select(tmp_path, "zerodha")
    assert str(snap.get("broker", {}).get("name", "")).lower() == "zerodha"


# ── Test 3: a DISCONNECTED broker is still eligible ───────────────────────


def test_disconnected_broker_becomes_default_and_is_not_substituted(
    tmp_path: Path,
) -> None:
    """FYERS disconnected must still win the default, with no fallback."""
    _seed(tmp_path, "zerodha")
    # No broker manager: nothing is connected anywhere.
    _select(tmp_path, "fyers")

    assert _store_default(tmp_path) == "fyers", "a disconnected broker may still be the default"
    headless._TRADING_SERVICES.clear()
    assert _store_default(tmp_path) == "fyers", "no silent substitution back to zerodha"


# ── Test 4: failure must not fake success ─────────────────────────────────


def test_unknown_broker_is_refused_without_false_success(tmp_path: Path) -> None:
    _seed(tmp_path, "fyers")

    result = _select(tmp_path, "not-a-broker")

    assert _store_default(tmp_path) == "fyers", "store untouched on refusal"
    assert "refused" in str(result.get("action_note", "")).lower()


def test_gateway_translates_only_a_well_formed_broker_id() -> None:
    """A malformed request never reaches the backend at all."""
    server = RemoteServer.__new__(RemoteServer)
    assert server._translate_command("select_broker", {}, "r") is None
    assert server._translate_command("select_broker", {"broker": "   "}, "r") is None
    assert server._translate_command("select_broker", {"broker": 7}, "r") is None
    assert server._translate_command("select_broker", {"broker": " FYERS "}, "r") == {
        "action": "select_broker",
        "broker": "fyers",
    }


# ── Test 5: restart persistence ───────────────────────────────────────────


@pytest.mark.parametrize("target", ["fyers", "zerodha"])
def test_default_survives_restart(tmp_path: Path, target: str) -> None:
    _select(tmp_path, target)
    headless._TRADING_SERVICES.clear()  # simulate the process restarting

    assert _store_default(tmp_path) == target
    # And a live snapshot after the restart agrees.
    snap = _select(tmp_path, target)
    assert str(snap.get("broker", {}).get("name", "")).lower() == target


# ── Test 6: opening a detail must NOT change the default ──────────────────


@pytest.mark.parametrize("default", ["fyers", "zerodha"])
def test_opening_a_detail_panel_does_not_change_the_default(tmp_path: Path, default: str) -> None:
    """Regression: merely LOOKING at a broker used to replace the default."""
    _seed(tmp_path, default)
    manager = None
    snap = headless._system_snapshot(
        str(tmp_path), "fyers" if default == "zerodha" else "zerodha", manager, str(tmp_path)
    )

    assert _store_default(tmp_path) == default, (
        "opening the other broker's detail must not change the global default"
    )
    assert snap["selected_id"] == default, "selected_id is the DEFAULT, not the panel"
    assert snap["panel_id"] == ("fyers" if default == "zerodha" else "zerodha"), (
        "panel_id is the card that is open"
    )


def test_snapshot_panel_and_default_are_independent(tmp_path: Path) -> None:
    _seed(tmp_path, "fyers")
    snap = headless._system_snapshot(str(tmp_path), "zerodha", None, str(tmp_path))

    assert snap["selected_id"] == "fyers"
    assert snap["panel_id"] == "zerodha"
    # The sidebar still marks FYERS as the default, not the open panel.
    marked = [b["id"] for b in snap["brokers"] if b.get("selected")]
    assert marked == ["fyers"]
