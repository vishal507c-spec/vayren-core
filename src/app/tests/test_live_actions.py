"""LIVE action wiring — setup keys, ARM consent ceremony, consent lifecycle.

The native LIVE tab is HOST-MODE: every control queues an action dict that
``app.headless._live_action`` applies to the real service. These tests pin
the contract between the Rust payload and the Python consumer:

* ``setup`` carries ``strategy_name`` (what live.rs sends) — reading only
  the legacy ``strategy`` key silently dropped every strategy pick;
* ARM is the LIVE consent ceremony: after ARM the snapshot must not list
  the consent blocker (or START can never enable), and a confirmed start
  must satisfy — not trip over — the consent requirement;
* consent binds to the reviewed setup: setup/mode/stop/halt void it, and a
  STOP re-arms the consent requirement for the next run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.live_trading_service import LiveTradingService  # noqa: E402


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    headless._ARMED[0] = False
    return str(tmp_path), str(tmp_path)


def _act(dirs: tuple[str, str], action: dict) -> dict:
    return headless._live_action(dirs[0], dirs[1], action)


def _service(dirs: tuple[str, str]) -> LiveTradingService:
    service = headless._live_service(dirs[0], dirs[1])
    assert service is not None
    return service


def test_setup_accepts_the_rust_strategy_name_key(dirs: tuple[str, str]) -> None:
    snap = _act(
        dirs,
        {
            "action": "setup",
            "strategy_name": "OBR C1C4",
            "symbols": ["NSE:RELIANCE"],
            "timeframe": "30m",
            "quantity": 10.0,
        },
    )
    assert _service(dirs)._config.strategy_name == "OBR C1C4"
    assert snap["action_note"] == "setup updated — readiness re-evaluated"


def test_setup_still_accepts_the_legacy_strategy_key(dirs: tuple[str, str]) -> None:
    _act(dirs, {"action": "setup", "strategy": "OBR"})
    assert _service(dirs)._config.strategy_name == "OBR"


def test_unsupported_mode_is_refused_honestly(dirs: tuple[str, str]) -> None:
    snap = _act(dirs, {"action": "mode", "mode": "SANDBOX"})
    assert _service(dirs)._config.mode == "PAPER"
    assert "not supported" in snap["action_note"]
    assert "PAPER" in snap["action_note"]


def _live_venue_stubs(service: LiveTradingService):
    """Stub the venue layer so the consent line is reachable in tests."""
    bar = SimpleNamespace(
        timestamp="2026-10-03T09:15:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )
    service._repository = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        list_symbols=lambda: ["RELIANCE"],
        detect_timeframes=lambda _s: ("30m",),
        get_candles_timeframe=lambda _s, _tf, _n: [bar],
    )
    return (
        patch.object(LiveTradingService, "broker_name", return_value="FYERS"),
        patch.object(LiveTradingService, "_resolve_live_venue_id", return_value="FYERS-live"),
        patch.object(LiveTradingService, "_compiled", return_value=object()),
    )


def test_confirmed_start_satisfies_consent(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(
        strategy_name="OBR",
        symbols=("NSE:RELIANCE",),
        timeframe="30m",
        quantity=10.0,
        mode="LIVE",
    )
    p1, p2, p3 = _live_venue_stubs(service)
    with (
        p1,
        p2,
        p3,
        patch("strategy.registry.get_strategy_registry") as reg,
        patch("app.services.live_trading_service.load_strategy_record") as load,
    ):
        reg.return_value.contains.return_value = False
        load.return_value = SimpleNamespace(
            id="OBR",
            name="OBR",
            code="x",
            created_at="",
            updated_at="",
            version="1",
        )
        assert any("consent" in b or "confirmation" in b for b in service.validate())
        with (
            patch("execution.modes.gates_from_env") as gates,
            patch.object(LiveTradingService, "_build_sessions", return_value=None),
        ):
            gates.return_value.missing.return_value = ()
            ok, reasons = service.start(confirmed=True)
        assert not any("consent" in r or "confirmation" in r for r in reasons), reasons
        assert ok, reasons


def test_unconfirmed_start_lists_consent_exactly_once(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    service.configure(
        strategy_name="OBR",
        symbols=("NSE:RELIANCE",),
        timeframe="30m",
        quantity=10.0,
        mode="LIVE",
    )
    p1, p2, p3 = _live_venue_stubs(service)
    with (
        p1,
        p2,
        p3,
        patch("strategy.registry.get_strategy_registry") as reg,
        patch("app.services.live_trading_service.load_strategy_record") as load,
    ):
        reg.return_value.contains.return_value = False
        load.return_value = SimpleNamespace(
            id="OBR",
            name="OBR",
            code="x",
            created_at="",
            updated_at="",
            version="1",
        )
        ok, reasons = service.start(confirmed=False)
        assert not ok
        consent = [r for r in reasons if "consent" in r or "confirmation" in r]
        assert len(consent) == 1


def test_arm_lifts_consent_from_the_snapshot(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(mode="LIVE")
    p1, p2, p3 = _live_venue_stubs(service)
    with p1, p2, p3:
        snap = _act(dirs, {"action": "arm"})
        assert headless._ARMED[0] is True
        assert "armed" in snap["action_note"]
        assert not any("consent" in b or "confirmation" in b for b in snap["start_blockers"])


def test_setup_mode_and_stop_void_consent(dirs: tuple[str, str]) -> None:
    _act(dirs, {"action": "arm"})
    assert headless._ARMED[0] is True
    _act(dirs, {"action": "setup", "quantity": 7.0})
    assert headless._ARMED[0] is False
    _act(dirs, {"action": "arm"})
    _act(dirs, {"action": "mode", "mode": "PAPER"})
    assert headless._ARMED[0] is False
    _act(dirs, {"action": "arm"})
    _act(dirs, {"action": "stop"})
    assert headless._ARMED[0] is False


def test_stop_clears_recorded_consent(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service._confirmed_live_at = "2026-10-03T00:00:00+00:00"
    service.stop()
    assert service._confirmed_live_at == ""
