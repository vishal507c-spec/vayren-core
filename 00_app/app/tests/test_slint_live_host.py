"""Slint Live host: bridge contract + viewport behavior.

The host draws nothing itself (pixels come from Rust) and computes nothing
(derivation lives in Rust `live::project`); these tests pin the bridge
contract: snapshot mapping (incl. real Bar objects → OHLC), fail-closed
loading, the action-event outward contract, lifecycle, and that a real
native frame actually paints varied pixels offscreen.
"""

import json

import pytest

from app.services.slint_live_host import (
    NativeViewError,
    SlintLiveHost,
    find_view_library,
    live_snapshot_dict,
    load_view_library,
)


class _Bar:
    """Provider-shaped OHLC record (mirrors the Bar attribute surface)."""

    def __init__(self, o: float, h: float, low: float, c: float) -> None:
        self.open = o
        self.high = h
        self.low = low
        self.close = c


def _provider_state() -> dict:
    """Provider-shaped live snapshot (mirrors `_live_state_provider`)."""
    return {
        "mode": "PAPER",
        "broker": {
            "name": "NOT CONFIGURED",
            "environment": "",
            "connected": None,
            "reason": "no live venue adapter is registered",
            "capabilities": [],
            "latency_ms": None,
            "last_heartbeat": None,
        },
        "strategy": None,
        "position": {"flat": True},
        "positions": [],
        "orders": [
            {
                "order_id": "cid-1",
                "strategy": "OBR",
                "symbol": "TEST",
                "side": "BUY",
                "quantity": 10.0,
                "type": "LIMIT",
                "price": 100.0,
                "status": "OPEN",
                "time": "t",
                "broker": "paper",
            }
        ],
        "fills": [],
        "pnl": {
            "realized": 0.0,
            "unrealized": 0.0,
            "total": 0.0,
            "exposure": 0.0,
            "orders": 1,
            "fills": 0,
            "wins": 0,
            "losses": 0,
        },
        "risk": {"status": "READY", "limits": [["max_order_qty", 10.0, "ok"]], "decisions": []},
        "reconciliation": {
            "status": "NOT CONFIGURED",
            "positions": "N/A",
            "orders": "N/A",
            "last_check": None,
            "mismatches": [],
            "blocks_live": True,
        },
        "kill": {"halted": False, "level": "global"},
        "gates": [{"name": "ACCOUNT", "status": "READY", "reason": ""}],
        "can_arm": False,
        "arm_blockers": ["no live venue"],
        "can_halt": False,
        "session_status": "STOPPED",
        "status_reason": "",
        "lifecycle": "STOPPED",
        "events": [
            {
                "timestamp": "t",
                "strategy": "OBR",
                "symbol": "TEST",
                "event": "SESSION_START",
                "status": "ok",
            }
        ],
        "market_symbol": "TEST",
        "market_timeframe": "15m",
        "market_bars": (_Bar(1.0, 2.0, 0.5, 1.5), _Bar(1.5, 2.5, 1.2, 2.2)),
        "available_strategies": ["OBR"],
        "available_symbols": ["TEST", "OTHER"],
        "selected_symbols": ["TEST"],
        "available_timeframes": ["5m", "15m"],
        "selected_timeframe": "15m",
        "quantity": 10.0,
        "start_blockers": ["no live broker selected"],
    }


def test_snapshot_mapping_preserves_backend_facts() -> None:
    snapshot = live_snapshot_dict(_provider_state())
    assert snapshot["mode"] == "PAPER"
    assert snapshot["broker"]["name"] == "NOT CONFIGURED"
    assert snapshot["broker"]["connected"] is None
    assert snapshot["session_status"] == "STOPPED"
    assert snapshot["gates"][0]["name"] == "ACCOUNT"
    assert snapshot["start_blockers"] == ["no live broker selected"]
    assert snapshot["arm_blockers"] == ["no live venue"]
    assert snapshot["can_halt"] is False
    assert snapshot["kill"] == {"halted": False}
    assert snapshot["orders"][0]["order_id"] == "cid-1"
    assert snapshot["risk"]["limits"] == [["max_order_qty", 10.0, "ok"]]
    assert snapshot["reconciliation"]["blocks_live"] is True
    assert snapshot["selected_symbols"] == ["TEST"]
    assert snapshot["quantity"] == 10.0
    # Real Bar objects become plain OHLC atoms (never stringified).
    assert snapshot["market_bars"] == [
        {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
        {"o": 1.5, "h": 2.5, "l": 1.2, "c": 2.2},
    ]
    # JSON-serializable exactly as the Rust bridge consumes it.
    json.dumps(snapshot, sort_keys=True)


def test_snapshot_mapping_degrades_honestly() -> None:
    assert live_snapshot_dict({})["market_bars"] == []
    assert live_snapshot_dict(None) == {}
    assert live_snapshot_dict("bogus") == {}
    assert live_snapshot_dict({"market_bars": "garbage"})["market_bars"] == []
    assert live_snapshot_dict({})["positions"] == []
    assert live_snapshot_dict({"broker": None})["broker"]["name"] is None
    assert live_snapshot_dict({"quantity": "bogus"})["quantity"] == "bogus"
    bad_gates = live_snapshot_dict({"gates": [{"a": 1}, 42, None]})["gates"]
    assert bad_gates == [{"name": "", "status": "", "reason": ""}]


def test_missing_library_is_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_LIVE_VIEW_LIB", str(tmp_path / "absent.dll"))
    with pytest.raises(NativeViewError):
        find_view_library()
    with pytest.raises(NativeViewError):
        load_view_library()


def test_dispatch_action_signals_outward(qt_app) -> None:
    """Accepted UI intents re-emerge as the Qt signal contract."""
    assert qt_app is not None
    host = SlintLiveHost(state_provider=lambda: {})
    try:
        got: dict = {}

        def record(name: str, payload: object = None) -> None:
            got[name] = payload

        host.start_requested.connect(lambda confirmed: record("start", confirmed))
        host.stop_requested.connect(lambda: record("stop", True))
        host.halt_requested.connect(lambda: record("halt", True))
        host.arm_requested.connect(lambda: record("arm", True))
        host.mode_requested.connect(lambda mode: record("mode", mode))
        host.setup_changed.connect(lambda setup: record("setup", setup))
        host.configure_broker_requested.connect(lambda: record("configure", True))

        host._dispatch_action({"action": "start", "confirmed": True})
        assert got["start"] is True
        host._dispatch_action({"action": "stop"})
        host._dispatch_action({"action": "halt"})
        host._dispatch_action({"action": "arm"})
        host._dispatch_action({"action": "mode", "mode": "LIVE"})
        assert got["mode"] == "LIVE"
        host._dispatch_action(
            {
                "action": "setup",
                "strategy_name": "OBR",
                "symbols": ["TEST"],
                "timeframe": "15m",
                "quantity": 10.0,
            }
        )
        assert got["setup"] == {
            "strategy_name": "OBR",
            "symbols": ["TEST"],
            "timeframe": "15m",
            "quantity": 10.0,
        }
        host._dispatch_action({"action": "configure_broker"})
        assert got["configure"] is True
        # Unknown actions and garbage are dropped, never crash.
        before = dict(got)
        host._dispatch_action({"action": "self-destruct"})
        host._dispatch_action(None)
        host._dispatch_action("bogus")
        assert got == before
    finally:
        host.destroy_view()
        host.close()


def _needs_cdylib() -> pytest.MarkDecorator:
    try:
        find_view_library()
    except NativeViewError:
        return pytest.mark.skip(reason="native Live view cdylib not built")
    return pytest.mark.skipif(False, reason="")


@_needs_cdylib()
def test_host_renders_real_native_frame(qt_app) -> None:
    """End-to-end offscreen: provider facts → Rust → varied real pixels."""
    assert qt_app is not None
    host = SlintLiveHost(state_provider=_provider_state)
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
    host = SlintLiveHost(state_provider=None)
    try:
        host.resize(640, 480)
        host.show()
        assert host._ensure_view()
        host._on_pump()
        assert len(bytes(host._frame)) == 640 * 480 * 3
    finally:
        host.destroy_view()
        host.close()
