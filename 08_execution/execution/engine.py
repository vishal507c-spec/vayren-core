"""ExecutionEngine — broker-independent order lifecycle tracker.

Owns the §12 state machine: legal transitions only, UNKNOWN exits
exclusively through explicit reconciliation. Never resubmits blindly.

Idempotency survives restart (FINAL §K): :meth:`snapshot` exports every
tracked order (intent/client ids, state, fills); :meth:`restore` rebuilds
the maps so duplicates stay denied and UNKNOWN orders still require
explicit reconciliation before any new submission.
"""

from __future__ import annotations

from typing import Any

from execution.models.order import TERMINAL_STATES, BrokerOrder, Fill, OrderState
from execution.native_execution import native_order_apply_fill
from execution.native_order_state import transition_allowed


class IllegalTransitionError(ValueError):
    """Attempted order transition outside the state machine."""


class ExecutionEngine:
    """Tracks every order from CREATED to a terminal state."""

    def __init__(self, clock: object = None) -> None:
        self._orders: dict[str, BrokerOrder] = {}
        self._by_intent: dict[str, str] = {}
        self._clock = clock

    def _now(self) -> str:
        if self._clock is not None and hasattr(self._clock, "now_iso"):
            return str(self._clock.now_iso())  # type: ignore[no-untyped-call]
        return "1970-01-01T00:00:00+00:00"

    def create(self, order: BrokerOrder) -> BrokerOrder:
        """Register a CREATED order; duplicate client IDs are rejected."""
        if order.client_order_id in self._orders:
            raise IllegalTransitionError(f"duplicate client order id: {order.client_order_id}")
        if order.intent_id in self._by_intent:
            raise IllegalTransitionError(f"duplicate intent already has order: {order.intent_id}")
        if order.state != OrderState.CREATED:
            raise IllegalTransitionError("only CREATED orders may enter the engine")
        stamped = order.with_state(OrderState.CREATED, self._now(), reason=order.reason)
        self._orders[stamped.client_order_id] = stamped
        self._by_intent[stamped.intent_id] = stamped.client_order_id
        return stamped

    def get(self, client_order_id: str) -> BrokerOrder | None:
        return self._orders.get(client_order_id)

    def by_intent(self, intent_id: str) -> BrokerOrder | None:
        client_id = self._by_intent.get(intent_id)
        return self._orders.get(client_id) if client_id else None

    def transition(self, client_order_id: str, target: OrderState, reason: str = "") -> BrokerOrder:
        """Advance one order along a legal edge (UNKNOWN exits need reconcile).

        Legality is judged by the Rust-owned lifecycle table
        (`execution.native_order_state`); the UNKNOWN-entry edges live in
        that table, so no Python-side bypass remains.
        """
        order = self._orders.get(client_order_id)
        if order is None:
            raise IllegalTransitionError(f"unknown order: {client_order_id}")
        if order.state == OrderState.UNKNOWN:
            raise IllegalTransitionError("UNKNOWN exits only via reconcile()")
        if not transition_allowed(order.state, target):
            raise IllegalTransitionError(f"illegal {order.state.value} -> {target.value}")
        if target == OrderState.UNKNOWN:
            reason = reason or "state unknown"
        advanced = order.with_state(target, self._now(), reason=reason)
        self._orders[client_order_id] = advanced
        return advanced

    def apply_fill(self, order: BrokerOrder, fill: Fill) -> BrokerOrder:
        """Fold a fill report into the order (partial-aware average price)."""
        stored = self._orders.get(order.client_order_id, order)
        if stored.state in (
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
        ):
            raise IllegalTransitionError(f"fill for terminal order {stored.client_order_id}")
        prev_qty = stored.filled_qty
        prev_avg = stored.avg_fill_price or 0.0
        new_qty, new_avg = native_order_apply_fill(
            prev_qty, prev_avg, fill.fill_qty, fill.fill_price
        )
        updated = BrokerOrder(
            client_order_id=stored.client_order_id,
            intent_id=stored.intent_id,
            symbol=stored.symbol,
            side=stored.side,
            quantity=stored.quantity,
            order_type=stored.order_type,
            limit_price=stored.limit_price,
            state=stored.state,
            filled_qty=new_qty,
            avg_fill_price=new_avg,
            broker_order_id=fill.broker_order_id or stored.broker_order_id,
            reason=stored.reason,
            history=stored.history,
        )
        self._orders[updated.client_order_id] = updated
        return updated

    def reconcile(
        self,
        client_order_id: str,
        broker_state: str,
        broker_filled_qty: float = 0.0,
        reason: str = "",
    ) -> BrokerOrder:
        """Resolve any order (including UNKNOWN) against a broker snapshot.

        The ONLY exit from UNKNOWN. Never creates fills — only aligns state;
        fill economics arrive through apply_fill.
        """
        order = self._orders.get(client_order_id)
        if order is None:
            raise IllegalTransitionError(f"unknown order: {client_order_id}")
        try:
            target = OrderState(broker_state)
        except ValueError:
            raise IllegalTransitionError(f"broker reported unknown state: {broker_state}") from None
        if target == OrderState.UNKNOWN:
            raise IllegalTransitionError("reconcile cannot resolve UNKNOWN to UNKNOWN")
        updated = order.with_state(target, self._now(), reason=reason or "reconciled")
        if broker_filled_qty:
            updated = BrokerOrder(
                client_order_id=updated.client_order_id,
                intent_id=updated.intent_id,
                symbol=updated.symbol,
                side=updated.side,
                quantity=updated.quantity,
                order_type=updated.order_type,
                limit_price=updated.limit_price,
                state=updated.state,
                filled_qty=broker_filled_qty,
                avg_fill_price=updated.avg_fill_price,
                broker_order_id=updated.broker_order_id,
                reason=updated.reason,
                history=updated.history,
            )
        self._orders[client_order_id] = updated
        return updated

    def open_orders(self) -> tuple[BrokerOrder, ...]:
        return tuple(o for o in self._orders.values() if o.state not in TERMINAL_STATES)

    def snapshot(self) -> dict[str, Any]:
        """Export idempotency state for checkpoint persistence (FINAL §K).

        Every tracked order: intent/client ids, lifecycle state, fill
        economics, broker id, history. Plain JSON-safe data only.
        """
        return {
            "orders": [
                {
                    "client_order_id": order.client_order_id,
                    "intent_id": order.intent_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "limit_price": order.limit_price,
                    "state": order.state.value,
                    "filled_qty": order.filled_qty,
                    "avg_fill_price": order.avg_fill_price,
                    "broker_order_id": order.broker_order_id,
                    "reason": order.reason,
                    "history": [list(pair) for pair in order.history],
                }
                for order in self._orders.values()
            ]
        }

    def restore(self, data: dict[str, Any]) -> None:
        """Rebuild idempotency maps from a :meth:`snapshot` (FINAL §K).

        Invalid payloads fail closed (``IllegalTransitionError``) — a
        corrupt checkpoint never silently authorizes new submissions.
        Restored UNKNOWN orders still exit only via :meth:`reconcile`.
        """
        raw = data.get("orders", [])
        if not isinstance(raw, list):
            raise IllegalTransitionError("engine snapshot 'orders' must be a list")
        rebuilt: dict[str, BrokerOrder] = {}
        by_intent: dict[str, str] = {}
        for item in raw:
            try:
                state = OrderState(str(item["state"]))
                order = BrokerOrder(
                    client_order_id=str(item["client_order_id"]),
                    intent_id=str(item["intent_id"]),
                    symbol=str(item["symbol"]),
                    side=str(item["side"]),
                    quantity=float(item["quantity"]),
                    order_type=str(item.get("order_type", "MARKET")),
                    limit_price=(
                        None if item.get("limit_price") is None else float(item["limit_price"])
                    ),
                    state=state,
                    filled_qty=float(item.get("filled_qty", 0.0)),
                    avg_fill_price=(
                        None
                        if item.get("avg_fill_price") is None
                        else float(item["avg_fill_price"])
                    ),
                    broker_order_id=item.get("broker_order_id"),
                    reason=str(item.get("reason", "")),
                    history=tuple((str(s), str(t)) for s, t in item.get("history", [])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise IllegalTransitionError(f"invalid engine snapshot order: {exc}") from exc
            if order.client_order_id in rebuilt:
                raise IllegalTransitionError(
                    f"duplicate client order id in snapshot: {order.client_order_id}"
                )
            if order.intent_id in by_intent:
                raise IllegalTransitionError(f"duplicate intent in snapshot: {order.intent_id}")
            rebuilt[order.client_order_id] = order
            by_intent[order.intent_id] = order.client_order_id
        self._orders = rebuilt
        self._by_intent = by_intent
