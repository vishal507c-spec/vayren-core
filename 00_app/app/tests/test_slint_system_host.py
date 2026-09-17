"""Slint System host: bridge contract + viewport behavior.

The host draws nothing itself (pixels come from Rust) and computes nothing
(derivation lives in Rust `BrokerPanel::from_json`); these tests pin the
bridge contract: snapshot mapping (selection + manager record → flat
broker facts), fail-closed loading, lifecycle, and that a real native
frame actually paints varied pixels offscreen.
"""

import json

import pytest

from app.services.slint_system_host import (
    NativeViewError,
    SlintSystemHost,
    find_view_library,
    load_view_library,
    system_snapshot_dict,
)


def _connected_state() -> dict:
    """Manager-shaped selection of a connected paper broker."""
    return {
        "selection": {"name": "paper", "environment": "paper"},
        "record": {
            "id": "paper",
            "name": "Paper",
            "status": "CONNECTED",
            "reason": "",
            "checks": {"connection": "READY", "funds": "READY"},
        },
    }


def test_snapshot_mapping_preserves_backend_facts() -> None:
    snapshot = system_snapshot_dict(_connected_state())
    assert snapshot["broker_id"] == "paper"
    assert snapshot["display_name"] == "Paper"
    assert snapshot["environment"] == "paper"
    assert snapshot["health"] == "CONNECTED"
    assert snapshot["live_ready"] is False
    assert snapshot["capabilities"] == [
        {"id": "connection", "label": "SUPPORTED", "kind": 0},
        {"id": "funds", "label": "SUPPORTED", "kind": 0},
    ]
    assert snapshot["blockers"] == []
    # JSON-serializable exactly as the Rust bridge consumes it.
    json.dumps(snapshot, sort_keys=True)


def test_snapshot_mapping_failed_checks_and_live_ready() -> None:
    snapshot = system_snapshot_dict(
        {
            "selection": {"name": "zerodha-live", "environment": "live"},
            "record": {
                "id": "zerodha-live",
                "name": "Zerodha",
                "status": "LIVE_READY",
                "reason": "",
                "checks": {"connection": "READY", "funds": "FAILED: timeout"},
            },
        }
    )
    assert snapshot["live_ready"] is True
    assert snapshot["capabilities"] == [
        {"id": "connection", "label": "SUPPORTED", "kind": 0},
        {"id": "funds", "label": "FAILED: timeout", "kind": 2},
    ]
    assert snapshot["blockers"] == []


def test_snapshot_mapping_reason_becomes_blocker() -> None:
    snapshot = system_snapshot_dict(
        {
            "selection": {"name": "zerodha", "environment": "paper"},
            "record": {
                "id": "zerodha",
                "name": "Zerodha",
                "status": "LOGIN_REQUIRED",
                "reason": "token expired",
                "checks": {},
            },
        }
    )
    assert snapshot["health"] == "LOGIN_REQUIRED"
    assert snapshot["blockers"] == ["token expired"]


def test_snapshot_mapping_degrades_honestly() -> None:
    empty = system_snapshot_dict({})
    assert empty["display_name"] == "NOT CONFIGURED"
    assert empty["health"] == "UNKNOWN"
    assert empty["blockers"] == ["no broker selected"]
    assert system_snapshot_dict(None) == system_snapshot_dict({})
    assert system_snapshot_dict("bogus") == system_snapshot_dict({})
    assert system_snapshot_dict({"selection": None, "record": None}) == system_snapshot_dict({})
    assert system_snapshot_dict({"selection": {"name": "x"}, "record": None}) == (
        system_snapshot_dict({})
    )


def test_missing_library_is_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_SYSTEM_VIEW_LIB", str(tmp_path / "absent.dll"))
    with pytest.raises(NativeViewError):
        find_view_library()
    with pytest.raises(NativeViewError):
        load_view_library()


def _needs_cdylib() -> pytest.MarkDecorator:
    try:
        find_view_library()
    except NativeViewError:
        return pytest.mark.skip(reason="native System view cdylib not built")
    return pytest.mark.skipif(False, reason="")


@_needs_cdylib()
def test_host_renders_real_native_frame(qt_app) -> None:
    """End-to-end offscreen: backend facts → Rust → varied real pixels."""
    assert qt_app is not None
    host = SlintSystemHost(state_provider=_connected_state)
    try:
        assert host.is_native_available
        host.resize(1280, 720)
        host.show()
        assert host._ensure_view()
        assert host._view is not None
        host._push_snapshot(force=True)
        host._on_pump()
        frame = bytes(host._frame)
        assert len(frame) == 1280 * 720 * 3
        assert any(b != frame[0] for b in frame), "native frame must contain varied pixels"
        # Recompose at a second size without a second window/view.
        host.resize(900, 700)
        host._on_pump()
        assert len(bytes(host._frame)) == 900 * 700 * 3
        assert any(b != frame[0] for b in bytes(host._frame))
    finally:
        host.destroy_view()
        host.destroy_view()  # idempotent
        host.close()


@_needs_cdylib()
def test_host_without_provider_stays_honest(qt_app) -> None:
    assert qt_app is not None
    host = SlintSystemHost(state_provider=None)
    try:
        host.resize(640, 480)
        host.show()
        assert host._ensure_view()
        host._on_pump()
        assert len(bytes(host._frame)) == 640 * 480 * 3
    finally:
        host.destroy_view()
        host.close()


def test_dispatch_action_signals_outward(qt_app) -> None:
    """Accepted UI intents re-emerge as the Qt signal contract (with the
    current selection as broker_id); garbage and no-selection are dropped."""
    assert qt_app is not None
    host = SlintSystemHost(
        state_provider=lambda: {
            "selection": {"name": "paper", "environment": "paper"},
            "record": {"id": "paper", "status": "CONNECTED"},
            "callback_url": "http://x/cb",
        }
    )
    try:
        got: dict = {}

        def record(name: str, payload: object = None) -> None:
            got[name] = payload

        host.login_requested.connect(lambda bid: record("login", bid))
        host.refresh_requested.connect(lambda bid: record("refresh", bid))
        host.disconnect_requested.connect(lambda bid: record("disconnect", bid))
        host.remove_requested.connect(lambda bid: record("remove", bid))
        host.configure_requested.connect(lambda bid, values: record("configure", (bid, values)))
        host.copy_callback_requested.connect(lambda bid: record("copy", bid))

        host._dispatch_action({"action": "login"})
        assert got["login"] == "paper"
        host._dispatch_action({"action": "refresh"})
        host._dispatch_action({"action": "disconnect"})
        host._dispatch_action({"action": "remove"})
        host._dispatch_action({"action": "copy_url"})
        host._dispatch_action({"action": "configure", "api_key": "K", "api_secret": "S"})
        assert got["configure"] == ("paper", {"api_key": "K", "api_secret": "S"})
        # Unknown/garbage never crash or emit.
        before = dict(got)
        host._dispatch_action({"action": "self-destruct"})
        host._dispatch_action(None)
        assert got == before
    finally:
        host.destroy_view()
        host.close()


def test_dispatch_drops_actions_without_selection(qt_app) -> None:
    assert qt_app is not None
    host = SlintSystemHost(state_provider=lambda: {"selection": None, "record": None})
    try:
        fired: list = []
        host.login_requested.connect(lambda bid: fired.append(bid))
        host._dispatch_action({"action": "login"})
        assert fired == []
    finally:
        host.destroy_view()
        host.close()
