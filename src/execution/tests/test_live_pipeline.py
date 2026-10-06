"""Phase-4 end-to-end LIVE execution pipeline (no real moneyCircuitBreaker).

Drives a real ``LiveSession`` (LIVE mode, armed, RUNNING) with a stub
strategy that emits OBR-shaped signals (entry + stop) and a fake venue
implementing the broker-neutral ``BrokerAdapter`` surface. Covers the
complete lifecycle: tick → signal → Phase-3 sizing → order → ack →
working → partial/full fill → ledger position → protective stop, plus
rejection, duplicates, timeout/unknown, reconciliation, restart and
PAPER/SANDBOX isolation.
"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.broker.adapter import BrokerError  # noqa: E402
from execution.broker.factory import resolve_broker  # noqa: E402
from execution.events import CandleEvent  # noqa: E402
from execution.models.contract import StrategyRuntimeContract  # noqa: E402
from execution.models.order import Fill, OrderPlan, OrderState  # noqa: E402
from execution.modes import ExecutionMode, LiveArm, ModeGates  # noqa: E402
from execution.runtime.lifecycle import LifecycleState  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from execution.runtime.strategy_runtime import StrategyContext  # noqa: E402
from risk import RiskPolicy  # noqa: E402
from strategy import StrategyParameters  # noqa: E402
from strategy.models.signal import Signal, SignalKind  # noqa: E402

SYMBOL = "KAYNES"
ENTRY = 1218.50
STOP = 1190.00  # below entry: SELL stop rests until touched
RISK_PER_SHARE = ENTRY - STOP  # 28.50
MAX_RISK = 600.0  # 100000 × 4 × 0.0015
QTY = 21  # risk-approved: floor(600 / 28.50)
PLANNED = 598.50
# Fresh sessions run the architected REDUCED posture (0.5x shrink-only):
# the submitted whole-share order is floor(21 × 0.5) = 10.
ORDER_QTY = 10.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class _Venue:
    """Fake broker-neutral venue (SIMULATED — never touches a real broker)."""

    name = "fake-live"

    def __init__(
        self,
        *,
        funds_available: float | None = 100_000.0,
        healthy: bool = True,
        stream_healthy: bool = True,
        fail_place: Exception | None = None,
        reject_stops: bool = False,
    ) -> None:
        self._funds = funds_available
        self._healthy = healthy
        self._stream_healthy = stream_healthy
        self._fail_place = fail_place
        self._reject_stops = reject_stops
        self._seq = 0
        self.placed: list[OrderPlan] = []
        self.cancelled: list[str] = []
        self._events: list[dict] = []
        self._positions: list[dict] = []
        self._open: list[dict] = []

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("orders.market", "orders.limit", "account.funds", "stream.events")

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return (self._healthy, "ok" if self._healthy else "down")

    def order_stream_health(self) -> tuple[bool, str]:
        return (self._stream_healthy, "ok" if self._stream_healthy else "ws down")

    def account(self) -> dict:
        return {"account_id": "ACC1", "mode": "LIVE"}

    def funds(self) -> dict:
        if self._funds is None:
            raise RuntimeError("funds not reported")
        return {"available": self._funds, "used": 0.0, "total": self._funds}

    def positions(self) -> list[dict]:
        return [dict(row) for row in self._positions]

    def open_orders(self) -> list[dict]:
        return [dict(row) for row in self._open]

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        if self._fail_place is not None:
            raise self._fail_place
        if self._reject_stops and plan.order_type in ("STOP_MARKET", "STOP_LIMIT"):
            raise BrokerError("stop rejected by venue", code="INVALID_REQUEST")
        self._seq += 1
        broker_id = f"VENUE-{self._seq:04d}"
        self.placed.append(plan)
        self._events.append(
            {"type": "ack", "client_order_id": client_order_id, "broker_order_id": broker_id}
        )
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancelled.append(broker_order_id)
        return True

    def modify_order(
        self, _broker_order_id: str, _quantity: float | None, _price: float | None
    ) -> bool:
        return True

    def stream_events(self) -> tuple[dict, ...]:
        drained = tuple(self._events)
        self._events.clear()
        return drained

    def on_market_price(self, _symbol: str, _price: float, _timestamp: str) -> None: ...

    def reference_spread(self, _symbol: str) -> float | None:
        return None

    def emit_fill(self, client_order_id: str, qty: float, price: float, *, partial: bool) -> None:
        self._events.append(
            {
                "type": "fill",
                "client_order_id": client_order_id,
                "fill": Fill(
                    client_order_id=client_order_id,
                    broker_order_id="VENUE-1",
                    symbol=SYMBOL,
                    side="BUY",
                    fill_qty=qty,
                    fill_price=price,
                    commission=0.0,
                    timestamp=_now_iso(),
                    partial=partial,
                ),
            }
        )

    def emit_reject(self, client_order_id: str, reason: str = "venue reject") -> None:
        self._events.append(
            {"type": "reject", "client_order_id": client_order_id, "reason": reason}
        )


class _Provider:
    capabilities = ("candles",)

    def __init__(self) -> None:
        self._queue: list[CandleEvent] = []

    def open(self, symbols: tuple, timeframe: str) -> None: ...

    def feed(self, event: CandleEvent) -> None:
        self._queue.append(event)

    def poll(self) -> tuple:
        drained = tuple(self._queue)
        self._queue.clear()
        return drained


class _Logic:
    """Stub strategy: emits one OBR-shaped BUY (entry + stop), then silent."""

    def __init__(self, *, emit_once: bool = True) -> None:
        self._emit_once = emit_once
        self._emitted = False

    def warmup(self) -> int:
        return 0

    def on_bar(self, view) -> Signal | None:  # noqa: ANN001, ANN204
        if self._emit_once and self._emitted:
            return None
        self._emitted = True
        bar = view.bar
        return Signal(
            index=0,
            timestamp=bar.timestamp,
            kind=SignalKind.BUY,
            price=bar.close,
            stop_loss=STOP,
        )


def _candle(seq: int, close: float = ENTRY) -> CandleEvent:
    return CandleEvent(
        symbol=SYMBOL,
        timestamp=_now_iso(),
        seq=seq,
        source="test",
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100,
        timeframe="30m",
        is_closed=True,
    )


def _session(venue: _Venue, provider: _Provider, *, logic: _Logic | None = None) -> LiveSession:
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        provider,  # pyright: ignore[reportArgumentType]
        RiskPolicy(),
        request_id="test-pipeline",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = venue  # pyright: ignore[reportAttributeAccessIssue]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")
    context = StrategyContext(
        strategy_id="OBR C1C4",
        strategy_version="1",
        logic=logic if logic is not None else _Logic(),
        params=StrategyParameters({}),
        contract=StrategyRuntimeContract(strategy_id="OBR C1C4", strategy_version="1"),
    )
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        context.lifecycle.transition(target, reason="test")
    session._contexts["OBR C1C4:1"] = context
    return session


def _kinds(session: LiveSession) -> list[str]:
    return [entry.kind for entry in session.journal.entries]


# ── §22.1–5: tick → signal → risk → approved order ───────────────────────


def test_full_entry_path_tick_to_working_order() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "RISK_VALIDATED" in _kinds(session)
    assert "ORDER_SUBMITTED" in _kinds(session)
    assert len(venue.placed) == 1
    plan = venue.placed[0]
    assert (plan.symbol, plan.side, plan.quantity) == (SYMBOL, "BUY", ORDER_QTY)

    # Venue ack drains on the next step: SENT → ACKNOWLEDGED (WORKING).
    session.step(time.time())
    tracked = session.engine.get("OBR C1C4:1:1:1:o1")
    assert tracked is not None
    assert tracked.state == OrderState.ACKNOWLEDGED
    assert "ORDER_ACK" in _kinds(session)


def test_risk_fail_blocks_before_adapter() -> None:
    venue, provider = _Venue(funds_available=0.0), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())

    assert "RISK_DENIED" in _kinds(session)
    assert venue.placed == []
    assert session.engine.open_orders() == ()


# ── §22.7–9: ack → partial → full fill → position from fills only ────────


def test_partial_then_full_fill_positions_filled_qty_only() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"

    venue.emit_fill(cid, 6.0, ENTRY, partial=True)
    session._process_broker_stream()
    position = session.ledger.position(SYMBOL)
    assert position.quantity == 6.0  # NOT 10
    tracked = session.engine.get(cid)
    assert tracked is not None and tracked.state == OrderState.PARTIALLY_FILLED
    assert tracked.filled_qty == 6.0

    venue.emit_fill(cid, 4.0, ENTRY, partial=False)
    session._process_broker_stream()
    position = session.ledger.position(SYMBOL)
    assert position.quantity == ORDER_QTY
    tracked = session.engine.get(cid)
    assert tracked is not None and tracked.state == OrderState.FILLED


def test_no_position_before_fill() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    # Signal + submitted order exist, but nothing filled yet.
    assert session.ledger.position(SYMBOL).flat
    session.step(time.time())  # ack drains; still no fill
    assert session.ledger.position(SYMBOL).flat


# ── §22.10: rejection creates no position ────────────────────────────────


def test_rejection_creates_no_position() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"
    venue._events.clear()  # drop the queued ack; venue rejects instead
    venue.emit_reject(cid, "insufficient margin")
    session._process_broker_stream()

    tracked = session.engine.get(cid)
    assert tracked is not None and tracked.state == OrderState.REJECTED
    assert "insufficient margin" in (tracked.reason or "")
    assert session.ledger.position(SYMBOL).flat


# ── §22.11–12: duplicates ────────────────────────────────────────────────


def test_duplicate_signal_creates_no_duplicate_order() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider, logic=_Logic(emit_once=False))
    provider.feed(_candle(1))
    session.step(time.time())
    provider.feed(_candle(2))
    session.step(time.time())
    # Second signal: already long → skipped; exactly one order exists.
    assert len(venue.placed) == 1


def test_duplicate_fill_event_folds_once() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"
    fill_kwargs = {"client_order_id": cid, "qty": 6.0, "price": ENTRY, "partial": True}
    venue.emit_fill(**fill_kwargs)  # noqa: ARG002
    venue.emit_fill(**fill_kwargs)  # retransmission
    session._process_broker_stream()
    assert session.ledger.position(SYMBOL).quantity == 6.0
    assert "FILL_DUPLICATE_IGNORED" in _kinds(session)


# ── §22.13: timeout → UNKNOWN, resolved only by reconcile ────────────────


def test_transport_timeout_marks_unknown_not_rejected() -> None:
    venue = _Venue(fail_place=BrokerError("timeout", code="NETWORK_ERROR"))
    provider = _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())

    assert "ORDER_UNKNOWN" in _kinds(session)
    tracked = session.engine.get("OBR C1C4:1:1:1:o1")
    assert tracked is not None and tracked.state == OrderState.UNKNOWN
    assert session.ledger.position(SYMBOL).flat

    resolved = session.engine.reconcile(
        tracked.client_order_id, "SUBMITTED", reason="broker has it"
    )
    assert resolved.state == OrderState.SUBMITTED


def test_deterministic_refusal_stays_rejected() -> None:
    venue = _Venue(fail_place=BrokerError("bad symbol", code="INVALID_REQUEST"))
    provider = _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    tracked = session.engine.get("OBR C1C4:1:1:1:o1")
    assert tracked is not None and tracked.state == OrderState.REJECTED


# ── §22.14–16: SL protection on actual filled qty ────────────────────────


def test_sl_armed_on_filled_qty_and_rests_until_touched() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"

    venue.emit_fill(cid, 6.0, ENTRY, partial=True)
    session._process_broker_stream()
    prot = session._stop_protection.get(SYMBOL)
    assert prot is not None
    assert prot["quantity"] == 6.0  # filled 6, NOT requested 10
    assert prot["stop_price"] == STOP
    sl_plan = venue.placed[-1]
    assert sl_plan.order_type == "STOP_MARKET" and sl_plan.quantity == 6.0

    session._process_broker_stream()  # SL ack drains
    assert session._stop_protection[SYMBOL]["state"] == "WORKING"
    assert session._stop_protection[SYMBOL]["state"] != "SAFE"
    assert "SL_SAFE" not in _kinds(session)


def test_sl_trigger_closes_position_and_marks_safe() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"
    venue.emit_fill(cid, ORDER_QTY, ENTRY, partial=False)
    session._process_broker_stream()
    session._process_broker_stream()  # SL ack
    sl_cid = session._stop_protection[SYMBOL]["client_order_id"]

    # Stop triggers: venue fills the protective stop.
    venue._events.append(
        {
            "type": "fill",
            "client_order_id": sl_cid,
            "fill": Fill(
                client_order_id=sl_cid,
                broker_order_id="VENUE-9",
                symbol=SYMBOL,
                side="SELL",
                fill_qty=ORDER_QTY,
                fill_price=STOP,
                commission=0.0,
                timestamp=_now_iso(),
                partial=False,
            ),
        }
    )
    session._process_broker_stream()
    assert session.ledger.position(SYMBOL).flat
    assert session._stop_protection[SYMBOL]["state"] == "SAFE"
    assert "SL_SAFE" in _kinds(session)


def test_sl_reject_marks_failed_and_attention() -> None:
    venue, provider = _Venue(reject_stops=True), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    cid = "OBR C1C4:1:1:1:o1"
    venue.emit_fill(cid, ORDER_QTY, ENTRY, partial=False)
    session._process_broker_stream()

    assert session._stop_protection[SYMBOL]["state"] == "FAILED"
    assert SYMBOL in session._sl_attention
    assert "SL_FAILED" in _kinds(session)
    assert SYMBOL in session.state()["sl_attention"]
    # The unprotected position is still real and reported.
    assert session.ledger.position(SYMBOL).quantity == ORDER_QTY


# ── §22.17–18: disconnected venues block ────────────────────────────────


def test_broker_down_blocks_live_order() -> None:
    venue, provider = _Venue(healthy=False), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    assert venue.placed == []
    # Either the adaptive HALT or the unhealthy-venue gate blocks — both are
    # fail-closed answers from the existing architecture, never an order.
    assert {"ORDER_BLOCKED_UNHEALTHY", "EXECUTION_HALTED"} & set(_kinds(session))


def test_order_stream_down_blocks_live_order() -> None:
    venue, provider = _Venue(stream_healthy=False), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    assert venue.placed == []
    assert "ORDER_BLOCKED" in _kinds(session)


# ── §22.19: restart reconciles before new orders ─────────────────────────


def test_restart_blocks_until_reconciled() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    checkpoint = session.checkpoint()

    venue2, provider2 = _Venue(), _Provider()
    restarted = _session(venue2, provider2, logic=_Logic(emit_once=False))
    restarted.recover(checkpoint)
    # Recovered mid-lifecycle, not RUNNING: no new submissions possible.
    from execution.models.intent import ExecutionIntent
    from execution.models.order import OrderPlan as _Plan

    intent = ExecutionIntent(
        intent_id="fresh",
        strategy_id="OBR C1C4",
        strategy_version="1",
        signal_id="s9",
        timestamp=_now_iso(),
        event_seq=9,
        symbol=SYMBOL,
        side="BUY",
        target_position_qty=1.0,
        quantity=1.0,
    )
    restarted._submit(
        intent,
        _Plan(intent_id="fresh", symbol=SYMBOL, side="BUY", quantity=1.0),
        time.time(),
    )
    assert venue2.placed == []
    assert "ORDER_BLOCKED" in _kinds(restarted)


# ── §22.20–21: mode isolation ────────────────────────────────────────────


def test_paper_resolution_never_touches_live_venue() -> None:
    broker, mode, _notes = resolve_broker(
        ExecutionMode.PAPER, ModeGates(), adapter_name="fyers-live"
    )
    assert mode == ExecutionMode.PAPER
    assert type(broker).__name__ == "PaperBroker"


def test_sandbox_without_adapter_is_not_configured() -> None:
    from execution.broker.adapter import NotConfiguredError

    try:
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="no-such-venue")
    except NotConfiguredError:
        return
    raise AssertionError("sandbox must not resolve without a registered venue")


# ── §20/§21: journal coverage + state exposure ───────────────────────────


def test_journal_covers_lifecycle_and_state_exposes_pipeline() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    provider.feed(_candle(1))
    session.step(time.time())
    session.step(time.time())  # ack
    kinds = set(_kinds(session))
    for expected in ("SIGNAL_GENERATED", "RISK_VALIDATED", "ORDER_SUBMITTED", "ORDER_ACK"):
        assert expected in kinds, kinds
    view = session.state()
    assert view["mode"] == "LIVE"
    assert view["broker"]["connected"] is True
    assert view["order_stream"]["supported"] is True
    assert view["reconciliation"]["blocks_live"] is False
    assert "stop_protection" in view and "sl_attention" in view
