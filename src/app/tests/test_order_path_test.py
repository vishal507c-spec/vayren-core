"""The bounded order-path test must stay bounded.

These tests exist because the path they guard places REAL MONEY orders. Each
one pins a rule that would otherwise be one refactor away from a duplicate
BUY, a retried uncertainty, or a lie about a closed position:

* **single submission** - one authorized call reaches the venue exactly once,
  carrying the real broker order id back;
* **duplicate prevention** - a second BUY is refused before any venue call,
  the client order id is deterministic and doubles as the FYERS orderTag, and
  the allowance is consumed even when submission blows up mid-flight;
* **uncertain responses** - a transport fault is UNCERTAIN and never retried,
  and never auto-reported as a rejection either;
* **exit verification** - "closed" comes from the broker's POSITION BOOK, so
  an accepted SELL that left a position open is reported as NOT closed;
* **risk is not bypassed** - no stop, or a stop the real sizing rejects, means
  no order is sent at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.order_path_test_service import (  # noqa: E402
    OUTCOME_BLOCKED,
    OUTCOME_EXIT_UNCERTAIN,
    OUTCOME_EXITED,
    OUTCOME_FILLED,
    OUTCOME_REJECTED,
    OUTCOME_SKIPPED,
    OUTCOME_SUBMITTED,
    OUTCOME_UNCERTAIN,
    client_order_id,
    net_position_qty,
    submit_buy,
    submit_exit,
)
from broker.vocab import BrokerError, ErrorCode  # noqa: E402
from strategy.universe_store import StrategyUniverseStore  # noqa: E402

SYMBOL = "NSE:ZEEL"
VENUE_SYMBOL = "NSE:ZEEL-EQ"
LP = 73.85
STOP = 73.25  # ~0.81%, inside the configured risk budget
NOW = 1_790_000_000.0


class FakeAdapter:
    """A venue whose every answer is scripted, and whose calls are counted."""

    def __init__(
        self,
        *,
        funds: float = 100.18,
        lp: float = LP,
        positions: list[dict[str, Any]] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        place_raises: BaseException | None = None,
        broker_order_id: str = "BO-1",
    ) -> None:
        self._funds = funds
        self._lp = lp
        self._positions = list(positions or [])
        self._open = list(open_orders or [])
        self._place_raises = place_raises
        self._broker_order_id = broker_order_id
        self.place_calls: list[tuple[Any, str]] = []
        self.position_reads = 0

    # ── reads ──
    def to_venue_symbol(self, symbol: str) -> str:
        clean = str(symbol).split(":")[-1].strip().upper()
        return f"NSE:{clean}-EQ"

    def funds(self) -> dict[str, float]:
        return {"available": self._funds, "used": 0.0, "total": self._funds, "equity": self._funds}

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        return {s: {"symbol": s, "lp": self._lp} for s in symbols}

    def positions(self) -> list[dict[str, Any]]:
        self.position_reads += 1
        return [dict(r) for r in self._positions]

    def open_orders(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._open]

    # ── write ──
    def place_order(self, plan: Any, cid: str, idempotency_key: str | None = None) -> str:  # noqa: ARG002
        self.place_calls.append((plan, cid))
        if self._place_raises is not None:
            raise self._place_raises
        return self._broker_order_id

    # ── test helpers (mutate the scripted world) ──
    def fill_position(self, qty: float) -> None:
        self._positions = [{"symbol": VENUE_SYMBOL, "netQty": qty}]

    def open_flat(self) -> None:
        self._positions = []


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "order_path_test.json"


def _buy(adapter: FakeAdapter, ledger: Path, **kw: Any):
    params: dict[str, Any] = {
        "adapter": adapter,
        "symbol": SYMBOL,
        "quantity": 1.0,
        "stop_price": STOP,
        "ledger_path": ledger,
        "authorize": True,
        "now_epoch": NOW,
    }
    params.update(kw)
    return submit_buy(**params)


# ── single submission ───────────────────────────────────────────────────────


def test_authorized_buy_reaches_the_venue_exactly_once(ledger: Path) -> None:
    adapter = FakeAdapter()
    adapter._open = [{"orderTag": client_order_id(SYMBOL, "buy", NOW), "status": 2, "filledQty": 1}]

    result = _buy(adapter, ledger)

    assert len(adapter.place_calls) == 1, "the venue must be called exactly once"
    plan, cid = adapter.place_calls[0]
    assert plan.symbol == SYMBOL
    assert plan.side == "BUY"
    assert plan.quantity == 1.0
    assert plan.order_type == "MARKET"
    assert cid == result.client_order_id
    assert result.broker_order_id == "BO-1"
    assert result.outcome == OUTCOME_FILLED
    assert result.ok


def test_unfilled_acceptance_is_not_reported_as_filled(ledger: Path) -> None:
    adapter = FakeAdapter()
    adapter._open = [{"orderTag": client_order_id(SYMBOL, "buy", NOW), "status": 4, "filledQty": 0}]

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_SUBMITTED
    assert not result.ok
    assert result.retryable is False


def test_missing_authorization_sends_nothing(ledger: Path) -> None:
    adapter = FakeAdapter()

    result = _buy(adapter, ledger, authorize=False)

    assert result.outcome == OUTCOME_BLOCKED
    assert adapter.place_calls == [], "an unauthorized call must never reach the venue"


def test_missing_stop_sends_nothing(ledger: Path) -> None:
    adapter = FakeAdapter()

    result = _buy(adapter, ledger, stop_price=0.0)

    assert result.outcome == OUTCOME_BLOCKED
    assert adapter.place_calls == [], "an unbounded order must never reach the venue"


# ── duplicate prevention ────────────────────────────────────────────────────


def test_second_buy_is_refused_before_any_venue_call(ledger: Path) -> None:
    adapter = FakeAdapter()
    adapter._open = [{"orderTag": client_order_id(SYMBOL, "buy", NOW), "status": 2, "filledQty": 1}]

    first = _buy(adapter, ledger)
    assert first.outcome == OUTCOME_FILLED
    assert len(adapter.place_calls) == 1

    second = _buy(adapter, ledger)

    assert second.outcome == OUTCOME_BLOCKED
    assert "one-BUY allowance already used" in second.detail
    assert len(adapter.place_calls) == 1, "a second BUY must never reach the venue"
    assert second.retryable is False


def test_client_order_id_is_deterministic_within_an_ist_day() -> None:
    a = client_order_id(SYMBOL, "buy", NOW)
    b = client_order_id(SYMBOL, "buy", NOW + 3600)
    assert a == b, "same day must reuse the id so the venue adopts, never duplicates"

    later = client_order_id(SYMBOL, "buy", NOW + 3 * 86400)
    assert later != a, "a new trading day must get a fresh id"

    assert client_order_id(SYMBOL, "exit", NOW) != a, "exit must not collide with the buy id"


def test_allowance_is_consumed_even_when_submission_never_lands(ledger: Path) -> None:
    """A crash mid-submit must not leave the operator free to send a second BUY."""
    adapter = FakeAdapter(place_raises=TimeoutError("connection reset"))

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_UNCERTAIN
    assert ledger.exists(), "the attempt must be recorded even though nothing landed"
    retry = _buy(adapter, ledger)
    assert retry.outcome == OUTCOME_BLOCKED
    assert len(adapter.place_calls) == 1


# ── uncertain order responses ───────────────────────────────────────────────


def test_transport_fault_is_uncertain_and_never_retried(ledger: Path) -> None:
    adapter = FakeAdapter(place_raises=BrokerError("gateway timeout", code=ErrorCode.NETWORK_ERROR))

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_UNCERTAIN
    assert result.retryable is False, "an uncertain order must never advertise itself retryable"
    assert "do NOT auto-retry" in result.detail
    assert len(adapter.place_calls) == 1


def test_venue_refusal_is_a_definite_rejection(ledger: Path) -> None:
    adapter = FakeAdapter(
        place_raises=BrokerError("insufficient funds", code=ErrorCode.INVALID_REQUEST)
    )

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_REJECTED
    assert not result.retryable


def test_unknown_expiry_is_uncertain_not_rejected(ledger: Path) -> None:
    adapter = FakeAdapter(place_raises=RuntimeError("socket closed"))

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_UNCERTAIN
    assert "do NOT auto-retry" in result.detail


# ── exit / position verification ────────────────────────────────────────────


def _exit(adapter: FakeAdapter, ledger: Path, **kw: Any):
    params: dict[str, Any] = {
        "adapter": adapter,
        "symbol": SYMBOL,
        "ledger_path": ledger,
        "now_epoch": NOW,
        "poll_interval_s": 0.0,
        "poll_timeout_s": 0.05,
        "sleep": lambda _s: None,
    }
    params.update(kw)
    return submit_exit(**params)


def test_accepted_sell_with_open_position_is_not_called_closed(ledger: Path) -> None:
    """The core honesty rule: a SELL request is not a closed position."""
    adapter = FakeAdapter(positions=[{"symbol": VENUE_SYMBOL, "netQty": 1.0}])

    result = _exit(adapter, ledger)

    assert adapter.place_calls, "an open position must actually be sold"
    assert result.outcome == OUTCOME_EXIT_UNCERTAIN
    assert result.verified_closed is False
    assert result.position_qty == 1.0
    assert "NOT confirmed closed" in result.detail


def test_exit_verified_flat_from_the_broker_position_book(ledger: Path) -> None:
    adapter = FakeAdapter(positions=[{"symbol": VENUE_SYMBOL, "netQty": 1.0}])

    def place_order(plan: Any, cid: str, idempotency_key: str | None = None) -> str:  # noqa: ARG001
        adapter.place_calls.append((plan, cid))
        adapter.open_flat()  # the venue actually closed it
        return "BO-EXIT"

    adapter.place_order = place_order  # type: ignore[method-assign]

    result = _exit(adapter, ledger)

    assert result.outcome == OUTCOME_EXITED
    assert result.verified_closed is True
    assert result.position_qty == 0.0
    assert result.ok


def test_exit_on_a_flat_position_sends_nothing(ledger: Path) -> None:
    adapter = FakeAdapter(positions=[])

    result = _exit(adapter, ledger)

    assert result.outcome == OUTCOME_EXITED
    assert result.verified_closed is True
    assert adapter.place_calls == [], "nothing open means nothing to sell"
    assert result.detail.startswith("broker reports no open position")


def test_unreachable_position_book_never_claims_a_close(ledger: Path) -> None:
    adapter = FakeAdapter()
    adapter.positions = lambda: (_ for _ in ()).throw(RuntimeError("positions 404"))  # type: ignore[method-assign]

    result = _exit(adapter, ledger)

    assert result.outcome == OUTCOME_EXIT_UNCERTAIN
    assert result.verified_closed is False
    assert adapter.place_calls == [], "an unreadable position book must not trigger a blind sell"


def test_exit_sell_that_fails_reports_the_open_position(ledger: Path) -> None:
    adapter = FakeAdapter(
        positions=[{"symbol": VENUE_SYMBOL, "netQty": 1.0}],
        place_raises=BrokerError("gateway timeout", code=ErrorCode.NETWORK_ERROR),
    )

    result = _exit(adapter, ledger)

    assert result.outcome == OUTCOME_EXIT_UNCERTAIN
    assert result.verified_closed is False
    assert result.position_qty == 1.0
    assert "may still be open" in result.detail


# ── position reader ─────────────────────────────────────────────────────────


def test_net_position_reads_only_the_requested_symbol() -> None:
    adapter = FakeAdapter(
        positions=[
            {"symbol": VENUE_SYMBOL, "netQty": 2.0},
            {"symbol": "NSE:KAYNES-EQ", "netQty": 5.0},
        ]
    )
    assert net_position_qty(adapter, SYMBOL) == 2.0
    assert net_position_qty(adapter, "NSE:KAYNES") == 5.0


def test_net_position_reads_alternate_quantity_keys() -> None:
    adapter = FakeAdapter(positions=[{"symbol": VENUE_SYMBOL, "netQuantity": -3}])
    assert net_position_qty(adapter, SYMBOL) == -3.0


# ── risk is not bypassed ────────────────────────────────────────────────────


def test_undersized_risk_budget_blocks_the_order(ledger: Path) -> None:
    """A stop the real sizing rejects must stop the order before the venue."""
    adapter = FakeAdapter(funds=100.18)
    far_stop = round(LP * 0.90, 2)  # 10% away, far beyond the risk budget

    result = _buy(adapter, ledger, stop_price=far_stop)

    assert result.outcome == OUTCOME_BLOCKED
    assert "sizing blocked" in result.detail
    assert adapter.place_calls == []


def test_zero_funds_blocks_the_order(ledger: Path) -> None:
    adapter = FakeAdapter(funds=0.0)

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_BLOCKED
    assert adapter.place_calls == []


def test_missing_live_price_blocks_the_order(ledger: Path) -> None:
    adapter = FakeAdapter(lp=0.0)

    result = _buy(adapter, ledger)

    assert result.outcome == OUTCOME_UNCERTAIN
    assert "no live venue price" in result.detail
    assert adapter.place_calls == []


def test_skip_outcome_is_exported_for_callers() -> None:
    """Guard the contract: OUTCOME_SKIPPED stays part of the public vocabulary."""
    assert OUTCOME_SKIPPED  # non-empty string sentinel


# ── action-level guards (the operator surface) ──────────────────────────────


class FakeManager:
    """Just enough broker manager for `_live_action` to resolve an adapter."""

    def __init__(self, adapter: Any | None) -> None:
        self.adapter = adapter

    def snapshot(self) -> dict[str, Any]:
        return {"active_broker": "fyers"}

    def state(self, broker_id: str) -> dict[str, Any]:  # noqa: ARG002
        return {"adapter": self.adapter}

    def read_only_adapter(self, broker_id: str) -> Any | None:  # noqa: ARG002
        return self.adapter


@pytest.fixture()
def live_dirs(tmp_path: Path) -> tuple[str, str]:
    headless._ARMED[0] = False
    store = StrategyUniverseStore(Path(tmp_path) / "live" / "strategy_universes.json")
    store.save("OBR C1C4", ("NSE:ZEEL", "NSE:KAYNES"))
    return str(tmp_path), str(tmp_path)


def _configured(dirs: tuple[str, str]) -> Any:
    headless._live_action(dirs[0], dirs[1], {"action": "setup", "strategy_name": "OBR C1C4"})
    headless._live_action(
        dirs[0],
        dirs[1],
        {
            "action": "setup",
            "strategy_name": "OBR C1C4",
            "symbols": ["NSE:ZEEL"],
            "timeframe": "30m",
            "quantity": 1.0,
        },
    )
    service = headless._live_service(dirs[0], dirs[1])
    assert service is not None
    assert service.config.symbols == ("NSE:ZEEL",)
    return service


def _order_test(dirs: tuple[str, str], adapter: Any | None, **params: Any) -> dict[str, Any]:
    action: dict[str, Any] = {"action": "order_test"}
    action.update(params)
    return headless._live_action(dirs[0], dirs[1], action, FakeManager(adapter))


def test_order_test_refused_without_live_consent(live_dirs: tuple[str, str]) -> None:
    """Consent is never granted by the test path itself."""
    adapter = FakeAdapter()
    _configured(live_dirs)

    snap = _order_test(live_dirs, adapter, side="buy", stop_price=STOP, authorize=True)

    assert adapter.place_calls == [], "no consent means no venue call"
    assert "LIVE consent has not been granted" in snap["action_note"]
    assert "order_test" not in snap


def test_order_test_refused_for_a_symbol_outside_the_configured_universe(
    live_dirs: tuple[str, str],
) -> None:
    adapter = FakeAdapter()
    _configured(live_dirs)

    snap = _order_test(
        live_dirs, adapter, side="buy", symbol="NSE:KAYNES", stop_price=STOP, authorize=True
    )

    assert adapter.place_calls == []
    assert "not the configured universe" in snap["action_note"]


def test_order_test_refused_when_quantity_differs_from_configured(
    live_dirs: tuple[str, str],
) -> None:
    adapter = FakeAdapter()
    service = _configured(live_dirs)
    service.confirm_live_execution()  # reach the guard under test

    snap = _order_test(
        live_dirs, adapter, side="buy", quantity=50.0, stop_price=STOP, authorize=True
    )

    assert adapter.place_calls == []
    assert "configured quantity" in snap["action_note"]


def test_order_test_refused_without_an_explicit_stop(live_dirs: tuple[str, str]) -> None:
    adapter = FakeAdapter()
    service = _configured(live_dirs)
    service.confirm_live_execution()  # reach the guard under test

    snap = _order_test(live_dirs, adapter, side="buy", authorize=True)

    assert adapter.place_calls == []
    assert "stop_price" in snap["action_note"]


def test_order_test_refused_without_explicit_authorize(live_dirs: tuple[str, str]) -> None:
    """Consent alone is not authorization: the BUY needs its own explicit flag."""
    adapter = FakeAdapter()
    service = _configured(live_dirs)
    service.confirm_live_execution()

    snap = _order_test(live_dirs, adapter, side="buy", stop_price=STOP, authorize=False)

    assert adapter.place_calls == [], "consent is not the same as authorizing this order"
    assert "explicit operator authorization required" in snap["action_note"]


def test_order_test_refuses_an_unknown_side(live_dirs: tuple[str, str]) -> None:
    adapter = FakeAdapter()
    _configured(live_dirs)

    snap = _order_test(live_dirs, adapter, side="short")

    assert adapter.place_calls == []
    assert "must be 'buy' or 'exit'" in snap["action_note"]


def test_order_test_reports_its_result_shape(live_dirs: tuple[str, str]) -> None:
    """The refusal shape is the same one a real outcome uses."""
    adapter = FakeAdapter()
    _configured(live_dirs)

    snap = _order_test(live_dirs, adapter, side="buy", stop_price=STOP, authorize=True)

    assert "action_note" in snap
    assert "order_test" not in snap, "a refusal must not fabricate a result block"
