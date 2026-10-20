"""Focused regression tests for the protective-stop cancellation state.

The engine's local order state must follow BROKER EVIDENCE, never the
attempt:

* the venue confirmed the cancel  -> the order IS cancelled;
* the venue declined it            -> the order is still working (never
  "cancelled" on a local guess);
* the venue's answer is unknown    -> the order stays UNRESOLVED (UNKNOWN)
  until the venue's truth confirms it;
* a protective stop is replaced only after the old one is proven gone — a
  second stop on top of an unproven one would hedge the position twice.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.models.order import Fill, OrderPlan  # noqa: E402
from execution.models.order_state import OrderState  # noqa: E402
from execution.modes import ExecutionMode, LiveArm  # noqa: E402
from execution.portfolio.ledger import Position  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from risk import RiskPolicy  # noqa: E402

SYMBOL = "KAYNES"
ENTRY = 1218.50
STOP = 1190.00
QTY = 10.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class _Venue:
    """Fake broker-neutral venue (SIMULATED — never touches a real broker)."""

    name = "fake-live"

    def __init__(
        self,
        *,
        cancel_result: bool = True,
        cancel_error: Exception | None = None,
        listed: bool = True,
        status: str = "OPEN",
        state_error: Exception | None = None,
    ) -> None:
        self.cancel_result = cancel_result
        self.cancel_error = cancel_error
        self.listed = listed
        self.status = status
        self.state_error = state_error
        self.placed: list[OrderPlan] = []
        self.cancelled: list[str] = []
        self.state_calls = 0
        self._seq = 0

    # ── broker surface ──────────────────────────────────────────────

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
        self._seq += 1
        broker_id = f"VENUE-{self._seq:04d}"
        self.placed.append(plan)
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancelled.append(broker_order_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_result

    def order_state(self, broker_order_id: str) -> dict[str, Any]:
        """The venue's own view of one order (the evidence a cancel needs)."""
        self.state_calls += 1
        if self.state_error is not None:
            raise self.state_error
        return {
            "broker_order_id": broker_order_id,
            "client_order_id": "",
            "status": self.status,
            "listed": self.listed,
            "filled_qty": 0.0,
        }

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict:
        return {"account_id": "ACC1", "mode": "LIVE"}

    def funds(self) -> dict:
        return {"available": 100_000.0, "used": 0.0, "total": 100_000.0}

    def positions(self) -> list[dict]:
        return []

    def open_orders(self) -> list[dict]:
        return []

    def modify_order(
        self, _broker_order_id: str, _quantity: float | None, _price: float | None
    ) -> bool:
        return True

    def stream_events(self) -> tuple[dict, ...]:
        return ()

    def on_market_price(self, _symbol: str, _price: float, _timestamp: str) -> None: ...

    def reference_spread(self, _symbol: str) -> float | None:
        return None


class _Provider:
    capabilities = ("candles",)

    def open(self, symbols: tuple, timeframe: str) -> None: ...

    def feed(self, event: Any) -> None: ...

    def poll(self) -> tuple:
        return ()


def _session(venue: _Venue) -> LiveSession:
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        _Provider(),  # pyright: ignore[reportArgumentType]
        RiskPolicy(),
        request_id="test-protective-cancel",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = venue  # pyright: ignore[reportAttributeAccessIssue]
    return session


def _armed_session(venue: _Venue, quantity: float = QTY) -> tuple[LiveSession, str]:
    """A session with a long position and a WORKING protective stop."""
    session = _session(venue)
    session._ledger.adopt_position(Position(symbol=SYMBOL, quantity=quantity, avg_price=ENTRY))
    session._stop_levels[SYMBOL] = STOP
    session._place_protective_stop(SYMBOL)
    protection = session._stop_protection[SYMBOL]
    return session, str(protection["client_order_id"])


def _kinds(session: LiveSession) -> list[str]:
    return [entry.kind for entry in session.journal.entries]


# ── broker confirms the cancellation ───────────────────────────────────


def test_confirmed_cancellation_updates_the_local_order_state() -> None:
    venue = _Venue(cancel_result=True)
    session, sl_cid = _armed_session(venue)
    assert len(venue.placed) == 1

    assert session._cancel_stop_protection(SYMBOL, "resize") is True

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.CANCELLED
    assert session._stop_protection[SYMBOL]["state"] == "CANCELLED"
    assert "SL_CANCELLED" in _kinds(session)


# ── broker refuses the cancellation ───────────────────────────────────


def test_declined_cancellation_never_marks_the_order_cancelled() -> None:
    venue = _Venue(cancel_result=False, listed=True)
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is False

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state != OrderState.CANCELLED
    assert session._stop_protection[SYMBOL]["state"] != "CANCELLED"
    assert "SL_CANCELLED" not in _kinds(session)
    assert "SL_CANCEL_DECLINED" in _kinds(session)
    # The old stop is still working at the venue, so the symbol is flagged
    # (its protection may no longer be the right size) but not "cancelled".
    assert SYMBOL in session._sl_attention


# ── unknown outcome ───────────────────────────────────────────────────


def test_unknown_cancellation_leaves_the_order_unresolved_until_confirmed() -> None:
    venue = _Venue(
        cancel_error=TimeoutError("venue timeout"),
        state_error=TimeoutError("order book unreadable"),
    )
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is False

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.UNKNOWN
    assert session._stop_protection[SYMBOL]["state"] == "CANCEL_UNCONFIRMED"
    assert "SL_CANCELLED" not in _kinds(session)
    assert "SL_CANCEL_UNCONFIRMED" in _kinds(session)
    assert SYMBOL in session._sl_attention

    # Bounded recovery: once the venue answers, the same path confirms it.
    venue.cancel_error = None
    venue.state_error = None
    venue.cancel_result = True
    assert session._cancel_stop_protection(SYMBOL, "resize") is True

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.CANCELLED
    assert session._stop_protection[SYMBOL]["state"] == "CANCELLED"


# ── a broker-state check may prove the cancellation ───────────────────


def test_broker_state_check_can_prove_the_cancellation() -> None:
    # The venue declines the cancel but reports the order as CANCELLED: that
    # is evidence the order is gone, so the local state may follow.
    venue = _Venue(cancel_result=False, listed=False, status="CANCELLED")
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is True

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.CANCELLED
    assert "SL_CANCELLED" in _kinds(session)
    assert venue.state_calls == 1


def test_an_order_missing_from_the_venue_book_is_not_proof_of_cancellation() -> None:
    # The venue's order book holds the whole trading day, so an order that is
    # not in it proves nothing: the cancellation stays unresolved.
    venue = _Venue(cancel_result=False, listed=False, status="UNKNOWN")
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is False

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.UNKNOWN
    assert "SL_CANCELLED" not in _kinds(session)
    assert "SL_CANCEL_UNCONFIRMED" in _kinds(session)


def test_a_rejected_venue_order_is_not_reported_as_cancelled() -> None:
    # The venue says the order was REJECTED: it is finished, but it was
    # never cancelled.
    venue = _Venue(cancel_result=False, listed=False, status="REJECTED")
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is True

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state == OrderState.REJECTED
    assert "SL_CANCELLED" not in _kinds(session)
    assert SYMBOL in session._sl_attention
    # The journal kind must not read like a cancellation: the remote
    # classifies journal kinds by keyword.
    assert "SL_FINISHED_AT_VENUE" in _kinds(session)


# ── replacing a protective stop needs evidence ────────────────────────


def test_protective_replacement_waits_for_a_confirmed_cancellation() -> None:
    venue = _Venue(cancel_result=False, listed=True)
    session, _ = _armed_session(venue)
    assert len(venue.placed) == 1

    # The position grew, so the engine wants a bigger stop: the old one must
    # be cancelled first.
    session._ledger.adopt_position(Position(symbol=SYMBOL, quantity=QTY * 2, avg_price=ENTRY))
    session._place_protective_stop(SYMBOL)

    # The cancel was declined -> no second stop may be placed on top of a
    # stop the venue may still be working.
    assert venue.cancelled
    assert len(venue.placed) == 1
    assert session._stop_protection[SYMBOL]["state"] != "CANCELLED"
    assert SYMBOL in session._sl_attention

    # With a confirmed cancellation the replacement is placed.
    venue.cancel_result = True
    session._place_protective_stop(SYMBOL)
    assert len(venue.placed) == 2
    assert session._stop_protection[SYMBOL]["state"] == "SENT"


# ── a declined cancel with an unreadable order book stays live ────────


def test_declined_cancel_with_unreadable_order_book_stays_live() -> None:
    # The venue answered "not cancelled" — that is a definite answer, so the
    # order is still working even though its order book could not be read.
    venue = _Venue(cancel_result=False, state_error=TimeoutError("order book unreadable"))
    session, sl_cid = _armed_session(venue)

    assert session._cancel_stop_protection(SYMBOL, "resize") is False

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state != OrderState.CANCELLED
    assert tracked.state != OrderState.UNKNOWN
    assert "SL_CANCELLED" not in _kinds(session)
    assert "SL_CANCEL_DECLINED" in _kinds(session)


# ── a filled protective stop is never reported as cancelled ──────────


def test_a_filled_protective_stop_is_not_reported_as_cancelled() -> None:
    # The venue reports the order as FILLED: it is gone because it traded,
    # not because it was cancelled.
    venue = _Venue(cancel_result=False, listed=False, status="FILLED")
    session, sl_cid = _armed_session(venue)
    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    session._engine.apply_fill(
        tracked,
        Fill(
            client_order_id=sl_cid,
            broker_order_id="VENUE-0001",
            symbol=SYMBOL,
            side="SELL",
            fill_qty=QTY,
            fill_price=STOP,
            commission=0.0,
            timestamp=_now_iso(),
            partial=False,
        ),
    )

    assert session._cancel_stop_protection(SYMBOL, "resize") is True

    tracked = session.engine.get(sl_cid)
    assert tracked is not None
    assert tracked.state != OrderState.CANCELLED
    assert session._stop_protection[SYMBOL]["state"] == "SAFE"
    assert "SL_CANCELLED" not in _kinds(session)


# ── a flat position keeps an unconfirmed stop visible ────────────────


def test_flat_cleanup_keeps_an_unconfirmed_stop_visible() -> None:
    venue = _Venue(
        cancel_error=TimeoutError("venue timeout"),
        state_error=TimeoutError("order book unreadable"),
    )
    session, _ = _armed_session(venue)

    session._clear_stop_protection(SYMBOL, "position flat")

    # The venue may still work this stop on a FLAT position (a triggered
    # leftover stop would open a short), so the record and the attention flag
    # stay until the cancellation is confirmed.
    assert SYMBOL in session._stop_protection
    assert SYMBOL in session._sl_attention
    assert "SL_CLEAR_DEFERRED" in _kinds(session)
    assert "SL_CLEARED" not in _kinds(session)
