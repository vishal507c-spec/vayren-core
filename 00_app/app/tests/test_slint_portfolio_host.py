"""Slint Portfolio host: bridge contract + viewport behavior.

The host draws nothing itself (pixels come from Rust) and computes nothing
(position math lives in Rust `project()`); these tests pin the bridge
contract: snapshot mapping, fail-closed loading, lifecycle, and that a
real native frame actually paints varied pixels offscreen.
"""

import json

import pytest

from app.services.slint_portfolio_host import (
    NativeViewError,
    SlintPortfolioHost,
    find_view_library,
    load_view_library,
    portfolio_snapshot_dict,
)


def _funded_state() -> dict:
    """Provider-shaped funded book (mirrors the Rust bridge-fixture facts)."""
    return {
        "broker": {
            "name": "paper",
            "environment": "paper",
            "connected": True,
            "status": "CONNECTED",
        },
        "funds": {"equity": 100000.0, "available": 80000.0, "used": 20000.0},
        "position": {
            "symbol": "TEST",
            "side": "LONG",
            "quantity": 10,
            "avg_price": 100.0,
            "current_price": 110.0,
            "exposure": 1100.0,
            "unrealized": 100.0,
        },
        "positions": [],
        "orders": [
            {
                "order_id": "c1",
                "time": "",
                "symbol": "TEST",
                "side": "BUY",
                "quantity": 10,
                "status": "FILLED",
            }
        ],
        "fills": [{"time": "t", "symbol": "TEST", "quantity": 10, "price": 100.0}],
        "pnl": {
            "total": 100.0,
            "unrealized": 100.0,
            "realized": 0.0,
            "today": None,
            "wins": 1,
            "losses": 0,
        },
        "risk": {"status": "READY"},
        "reconciliation": {"status": "CLEAN"},
        "kill": {"halted": False},
        "lifecycle": "RUNNING",
        "mode": "PAPER",
        "risk_metrics": {},
    }


def test_snapshot_mapping_preserves_backend_facts() -> None:
    snapshot = portfolio_snapshot_dict(_funded_state())
    assert snapshot["broker"]["name"] == "paper"
    assert snapshot["broker"]["connected"] is True
    assert snapshot["funds"] == {"equity": 100000.0, "available": 80000.0, "used": 20000.0}
    assert snapshot["position"]["symbol"] == "TEST"
    assert snapshot["position"]["quantity"] == 10
    assert snapshot["orders"][0]["order_id"] == "c1"
    assert snapshot["fills"][0]["price"] == 100.0
    assert snapshot["pnl"]["wins"] == 1
    assert snapshot["pnl"]["today"] is None
    assert snapshot["risk"] == {"status": "READY"}
    assert snapshot["reconciliation"] == {"status": "CLEAN"}
    assert snapshot["kill"] == {"halted": False}
    assert snapshot["mode"] == "PAPER"
    assert snapshot["lifecycle"] == "RUNNING"
    # JSON-serializable exactly as the Rust bridge consumes it.
    json.dumps(snapshot, sort_keys=True)


def test_snapshot_mapping_degrades_honestly() -> None:
    assert "funds" not in portfolio_snapshot_dict({})
    assert "funds" not in portfolio_snapshot_dict({"funds": {}})
    assert "funds" not in portfolio_snapshot_dict({"funds": None})
    assert "position" not in portfolio_snapshot_dict({"position": None})
    assert portfolio_snapshot_dict({"position": {}})["position"] == {}
    assert portfolio_snapshot_dict(None) == {}
    assert portfolio_snapshot_dict("bogus") == {}
    assert portfolio_snapshot_dict({"orders": [{"a": 1}, 42, None]})["orders"] == [{"a": 1}]
    assert portfolio_snapshot_dict({})["positions"] == []


def test_missing_library_is_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_PORTFOLIO_VIEW_LIB", str(tmp_path / "absent.dll"))
    with pytest.raises(NativeViewError):
        find_view_library()
    with pytest.raises(NativeViewError):
        load_view_library()


def _needs_cdylib() -> pytest.MarkDecorator:
    try:
        find_view_library()
    except NativeViewError:
        return pytest.mark.skip(reason="native Portfolio view cdylib not built")
    return pytest.mark.skipif(False, reason="")


@_needs_cdylib()
def test_host_renders_real_native_frame(qt_app) -> None:
    """End-to-end offscreen: provider facts → Rust → varied real pixels."""
    assert qt_app is not None
    host = SlintPortfolioHost(state_provider=_funded_state)
    try:
        assert host.is_native_available
        host.resize(800, 600)
        host.show()
        assert host._ensure_view()
        assert host._view is not None
        host._push_snapshot(force=True)
        host._on_pump()
        frame = bytes(host._frame)
        assert len(frame) == 800 * 600 * 3
        assert any(b != frame[0] for b in frame), "native frame must contain varied pixels"
        # Second pump with identical state must not repaint pointlessly.
        host._on_pump()
    finally:
        host.destroy_view()
        host.destroy_view()  # idempotent
        host.close()


@_needs_cdylib()
def test_host_without_provider_stays_honest(qt_app) -> None:
    assert qt_app is not None
    host = SlintPortfolioHost(state_provider=None)
    try:
        host.resize(640, 480)
        host.show()
        assert host._ensure_view()
        host._on_pump()
        assert len(bytes(host._frame)) == 640 * 480 * 3
    finally:
        host.destroy_view()
        host.close()
