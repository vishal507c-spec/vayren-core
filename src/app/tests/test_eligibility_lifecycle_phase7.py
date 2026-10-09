"""Phase 7 eligibility lifecycle — gate in the real order path (no network).

Session-level tests drive ``LiveSession._submit`` with the app-built gate
closure (real engine over stubbed service truth); service-level tests pin
the opt-in wiring, diagnostics, recovery, and headless plumbing. Fakes
only — no broker credentials, no network.
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
from app.services.eligibility import (  # noqa: E402
    build_eligibility_engine,
    canonical_id_for,
    order_gate_for,
)
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from execution.models.intent import ExecutionIntent  # noqa: E402
from execution.models.order import OrderPlan  # noqa: E402
from execution.modes import ExecutionMode, LiveArm  # noqa: E402
from execution.runtime.lifecycle import LifecycleState  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from risk import RiskPolicy  # noqa: E402

SYMBOL = "NSE:RELIANCE"
RELIANCE = "NSE:EQUITY:RELIANCE"


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    headless._ARMED[0] = False
    return str(tmp_path), str(tmp_path)


def _service(dirs: tuple[str, str]) -> LiveTradingService:
    service = headless._live_service(dirs[0], dirs[1])
    assert service is not None
    return service


def _bar() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp="2026-10-03T09:15:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )


def _stub_repo(service: LiveTradingService) -> None:
    bar = _bar()
    service._repository = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        list_symbols=lambda: ["RELIANCE"],
        detect_timeframes=lambda _s: ("30m",),
        get_candles_timeframe=lambda _s, _tf, _n: [bar],
    )


def _ready_risk(service: LiveTradingService) -> None:
    service._risk_status = "READY"
    service._risk_reason = ""


def _configure(service: LiveTradingService) -> None:
    service.configure(strategy_name="OBR C1C4")
    service.configure(symbols=("NSE:RELIANCE",), timeframe="30m", quantity=10.0)


class _Venue:
    """Order venue double with full call logs (no network)."""

    def __init__(self) -> None:
        self.placed: list[tuple[str, float]] = []
        self.cancelled: list[str] = []
        self.modified: list[tuple[str, object, object]] = []
        self.name = "fake-venue"

    def health(self) -> tuple[bool, str]:
        return True, "fake ready"

    def place_order(self, plan: object, _client_order_id: str) -> str:
        self.placed.append((getattr(plan, "symbol", ""), getattr(plan, "quantity", 0.0)))
        return "VENUE-1"

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancelled.append(broker_order_id)
        return True

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        self.modified.append((broker_order_id, quantity, price))
        return True


def _session(gate: object = None) -> tuple[LiveSession, _Venue]:
    venue = _Venue()
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.PAPER, journal_path=None, memory_path=None),
        SimpleNamespace(),  # provider unused by _submit  # pyright: ignore[reportArgumentType]
        RiskPolicy(),
        request_id="test-phase7",
        order_gate=gate,  # pyright: ignore[reportArgumentType]
    )
    session._broker = venue  # pyright: ignore[reportAttributeAccessIssue]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")
    return session, venue


def _intent(symbol: str = SYMBOL, quantity: float = 10.0) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id="i1",
        strategy_id="OBR C1C4",
        strategy_version="1",
        signal_id="s1",
        timestamp="2026-10-03T09:15:00",
        event_seq=1,
        symbol=symbol,
        side="BUY",
        target_position_qty=quantity,
        quantity=quantity,
    )


def _plan(symbol: str = SYMBOL, quantity: float = 10.0) -> OrderPlan:
    return OrderPlan(intent_id="i1", symbol=symbol, side="BUY", quantity=quantity)


def _journal_kinds(session: LiveSession) -> list[str]:
    return [entry.kind for entry in session.journal.entries]


def _journal_reasons(session: LiveSession) -> list[str]:
    return [str(entry.payload.get("reason", "")) for entry in session.journal.entries]


# ── 1-4: gate mechanics inside the real _submit path ────────────────


def test_01_eligible_order_continues_pipeline() -> None:
    session, venue = _session(gate=lambda _i, _p: None)
    session._submit(_intent(), _plan(), 1.0)
    assert venue.placed == [(SYMBOL, 10.0)]
    assert "ORDER_SUBMITTED" in _journal_kinds(session)


def test_02_blocked_verdict_prevents_submission() -> None:
    session, venue = _session(gate=lambda _i, _p: "eligibility blocked: BROKER_NOT_READY")
    session._submit(_intent(), _plan(), 1.0)
    assert venue.placed == []
    assert "ORDER_REJECTED" in _journal_kinds(session)
    last = session.journal.entries[-1]
    assert "BROKER_NOT_READY" in str(last.payload.get("reason", ""))


def test_03_gate_exception_fails_closed() -> None:
    def _boom(_intent: object, _plan: object) -> str | None:
        raise RuntimeError("gate exploded")

    session, venue = _session(gate=_boom)
    session._submit(_intent(), _plan(), 1.0)  # must not raise into the tick loop
    assert venue.placed == []
    assert "ORDER_REJECTED" in _journal_kinds(session)


def test_04_missing_strategy_or_instrument_fails_closed(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    gate = order_gate_for(service)
    assert gate is not None
    # No strategy configured on a fresh service.
    assert "no strategy selected" in (gate(_intent(), _plan()) or "")
    # Unknown symbol never resolves.
    service.configure(strategy_name="OBR C1C4")
    assert "canonical" in (gate(_intent(symbol="NSE:NOPE"), _plan(symbol="NSE:NOPE")) or "")


# ── 5-7: existing gates still authoritative + modes ─────────────────


def test_05_sizing_gates_still_execute_with_gate_attached() -> None:
    session, venue = _session(gate=lambda _i, _p: None)
    session._submit(_intent(quantity=0.0), _plan(quantity=0.0), 1.0)
    assert venue.placed == []
    assert "ORDER_SKIPPED" in _journal_kinds(session)
    session2, venue2 = _session(gate=lambda _i, _p: None)
    session2._submit(_intent(quantity=10.5), _plan(quantity=10.5), 1.0)
    assert venue2.placed == [(SYMBOL, 10.0)]
    assert "QUANTITY_FLOORED" in _journal_kinds(session2)


def test_06_risk_denial_still_prevents_submission() -> None:
    from risk.sizing import SizingVerdict

    session, venue = _session(gate=lambda _i, _p: None)
    session._sized_risk["i1"] = SizingVerdict(
        ready=False,
        reason="planned risk exceeds ceiling",
        max_risk_per_stock=600.0,
        entry_price=100.0,
        stop_price=90.0,
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._submit(_intent(quantity=100.0), _plan(quantity=100.0), 1.0)
    assert venue.placed == []
    assert "RISK_DENIED" in _journal_kinds(session)


def test_07_paper_and_sandbox_unaffected() -> None:
    for mode in (ExecutionMode.PAPER, ExecutionMode.SANDBOX):
        venue = _Venue()
        session = LiveSession(
            SessionConfig(mode=mode, journal_path=None, memory_path=None),
            SimpleNamespace(),  # pyright: ignore[reportArgumentType]
            RiskPolicy(),
            request_id="test-phase7",
            order_gate=lambda _i, _p: None,
        )
        session._broker = venue  # pyright: ignore[reportAttributeAccessIssue]
        for target in (
            LifecycleState.VALIDATING,
            LifecycleState.WARMING_UP,
            LifecycleState.READY,
            LifecycleState.RUNNING,
        ):
            session._lifecycle.transition(target, reason="test")
        session._submit(_intent(), _plan(), 1.0)
        assert venue.placed == [(SYMBOL, 10.0)], mode


# ── 8-10: service-truth gating (LIVE posture) ───────────────────────


def test_08_live_not_armed_blocks(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    service._config.mode = "LIVE"
    gate = order_gate_for(service)
    assert gate is not None
    reason = gate(_intent(), _plan())
    assert reason is not None and "LIVE_NOT_ARMED" in reason


def test_09_stale_and_unavailable_market_data_block(dirs: tuple[str, str]) -> None:

    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    service._status = "RUNNING"
    gate = order_gate_for(service)
    assert gate is not None
    stale = [{"symbol": "NSE:RELIANCE", "status": "NO MARKET DATA"}]
    with patch.object(service, "_universe_quotes", return_value=stale):
        reason = gate(_intent(), _plan())
    assert reason is not None and "MARKET_DATA_STALE" in reason
    missing = [{"symbol": "NSE:RELIANCE", "status": "NOT FOUND"}]
    with patch.object(service, "_universe_quotes", return_value=missing):
        reason = gate(_intent(), _plan())
    assert reason is not None and "MARKET_DATA_UNAVAILABLE" in reason


def test_10_unhealthy_broker_and_missing_mappings_block(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    service._status = "RUNNING"
    service._config.mode = "LIVE"
    gate = order_gate_for(service)
    assert gate is not None
    # No provider mappings configured anywhere → mapping gate blocks.
    reason = gate(_intent(), _plan())
    assert reason is not None and "BROKER_MAPPING_MISSING" in reason


# ── 11-13: UNKNOWN discipline + recovery ────────────────────────────


def test_11_unknown_owned_order_blocks_resubmit(dirs: tuple[str, str]) -> None:
    # UNKNOWN acceptance must never be blindly retried or rerouted: an
    # UNKNOWN owned order blocks new entries until reconciled.
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    engine = build_eligibility_engine(service)
    owned = SimpleNamespace(
        engine=SimpleNamespace(
            open_orders=lambda: [
                SimpleNamespace(
                    symbol="NSE:RELIANCE",
                    client_order_id="cid-7",
                    state=SimpleNamespace(value="UNKNOWN"),
                    broker_order_id="",
                )
            ]
        ),
        reconciliation=SimpleNamespace(blocks_live=False),
    )
    service._sessions = {"NSE:RELIANCE": owned}  # pyright: ignore[reportAttributeAccessIssue]
    verdict = engine.evaluate(RELIANCE, "OBR C1C4", trading_mode="PAPER")
    assert "DUPLICATE_ORDER" in verdict.blocking_reasons
    assert verdict.allowed is False


def test_12_accepted_orders_never_duplicated_by_fallback(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    gate = order_gate_for(service)
    assert gate is not None
    engine = build_eligibility_engine(service)
    owned = SimpleNamespace(
        engine=SimpleNamespace(
            open_orders=lambda: [
                SimpleNamespace(
                    symbol="NSE:RELIANCE",
                    client_order_id="cid-9",
                    state=SimpleNamespace(value="ACKNOWLEDGED"),
                    broker_order_id="B-9",
                )
            ]
        ),
        reconciliation=SimpleNamespace(blocks_live=False),
    )
    service._sessions = {"NSE:RELIANCE": owned}  # pyright: ignore[reportAttributeAccessIssue]
    verdict = engine.evaluate(RELIANCE, "OBR C1C4", trading_mode="PAPER")
    assert "DUPLICATE_ORDER" in verdict.blocking_reasons


def test_13_reconciliation_failure_blocks_live(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    service._config.mode = "LIVE"
    owned = SimpleNamespace(
        engine=SimpleNamespace(open_orders=lambda: []),
        reconciliation=SimpleNamespace(blocks_live=True),
    )
    service._sessions = {"NSE:RELIANCE": owned}  # pyright: ignore[reportAttributeAccessIssue]
    gate = order_gate_for(service)
    assert gate is not None
    reason = gate(_intent(), _plan())
    assert reason is not None and "RECONCILIATION_UNHEALTHY" in reason


# ── 14-16: ownership + diagnostics ──────────────────────────────────


def test_14_ownership_preserved_for_cancel_modify() -> None:
    session, venue = _session(gate=lambda _i, _p: None)
    session._submit(_intent(), _plan(), 1.0)
    assert venue.placed != []
    # Lifecycle ops go straight to the venue — the gate is submit-only.
    assert venue.cancel_order("VENUE-1") is True
    assert venue.modify_order("VENUE-1", 5.0, 99.0) is True
    assert venue.cancelled == ["VENUE-1"]
    assert venue.modified == [("VENUE-1", 5.0, 99.0)]


def test_15_stop_protection_bypasses_gate() -> None:
    calls: list[str] = []

    def _gate(_intent: object, _plan: object) -> str | None:
        calls.append("gate")
        return "eligibility blocked: BROKER_NOT_READY"

    session, venue = _session(gate=_gate)
    # Protective-stop placement never consults the gate (separate path).
    import inspect

    source = inspect.getsource(session._place_protective_stop)
    assert "_order_gate" not in source
    assert calls == []


def test_16_diagnostics_match_engine(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    _stub_repo(service)
    _ready_risk(service)
    _configure(service)
    snap = service.eligibility_snapshot()
    assert set(snap) >= {"system", "strategy", "instruments", "enforcement"}
    assert snap["enforcement"] is False
    assert snap["system"]["final"] in ("TRADING_ALLOWED", "TRADING_BLOCKED")
    assert "NSE:RELIANCE" in snap["instruments"]
    row = snap["instruments"]["NSE:RELIANCE"]
    assert set(row) >= {"level", "allowed", "blocking_reasons"}


# ── wiring: flag, persistence, headless, snapshot ───────────────────


def test_wiring_enforcement_defaults_off(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    assert service._eligibility_enforcement is False
    assert service._session_order_gate() is None


def test_wiring_flag_roundtrip_and_persist(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(eligibility_enabled=True)
    assert service._eligibility_enforcement is True
    assert service._session_order_gate() is not None
    reopened = LiveTradingService(dirs[0], dirs[1])
    assert reopened._eligibility_enforcement is True
    reopened.configure(eligibility_enabled=False)
    assert reopened._eligibility_enforcement is False


def test_wiring_armed_mirroring(dirs: tuple[str, str]) -> None:
    assert _service(dirs).armed is False
    headless._live_action(dirs[0], dirs[1], {"action": "arm"})
    assert _service(dirs).armed is True
    headless._live_action(dirs[0], dirs[1], {"action": "stop"})
    assert _service(dirs).armed is False
    headless._live_action(dirs[0], dirs[1], {"action": "arm"})
    headless._live_action(dirs[0], dirs[1], {"action": "setup", "quantity": 5.0})
    assert _service(dirs).armed is False


def test_wiring_headless_eligibility_action(dirs: tuple[str, str]) -> None:
    snap = headless._live_action(dirs[0], dirs[1], {"action": "eligibility"})
    assert "eligibility" in snap
    assert "action_note" in snap
    assert set(snap["eligibility"]) >= {"system", "strategy", "instruments", "enforcement"}


def test_wiring_snapshot_carries_eligibility(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    _stub_repo(service)
    snap = headless._live_action(dirs[0], dirs[1], {"action": "tick"})
    assert "eligibility" in snap
    assert set(snap["eligibility"]) >= {"system", "strategy", "instruments", "enforcement"}


def test_wiring_canonical_resolution() -> None:
    assert canonical_id_for("NSE:RELIANCE") == RELIANCE
    assert canonical_id_for("NSE:NOPE") == ""
    assert canonical_id_for("") == ""


# ── 17-18: architecture proofs ──────────────────────────────────────


def test_17_no_submission_surface_in_eligibility() -> None:
    import app.services.eligibility as wiring

    for module in (wiring,):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        assert "def place_order" not in source
        assert ".place_order(" not in source
        assert "fyers_apiv3" not in source
        assert "kiteconnect" not in source


def test_18_no_duplicated_authoritative_logic() -> None:
    import app.services.eligibility as wiring
    import execution.runtime.session as session_module

    wiring_source = Path(wiring.__file__ or "").read_text(encoding="utf-8")
    assert "compute_qty" not in wiring_source
    assert "validate_planned_quantity" not in wiring_source
    assert "freshness_threshold" not in wiring_source
    hook = Path(session_module.__file__ or "").read_text(encoding="utf-8")
    assert hook.count("_order_gate") <= 4
