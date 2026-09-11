"""PaperBroker — deterministic simulated venue for PAPER mode.

Fill economics mirror the backtest ``ExecutionSimulator`` (reference price
± slippage, commission on notional) so backtest ↔ paper parity is testable.
Differences are explicit: quantity comes from the risk-approved intent
(instead of equity-derived sizing), and unaffordable quantity produces a
deterministic PARTIAL fill rather than a rejection. No network, no
credentials, no SDKs — safe by construction.
"""

from __future__ import annotations

from broker.funds import FundsSnapshot

from execution.broker.adapter import BrokerCapabilities, BrokerError
from execution.models.order import Fill, OrderPlan


class PaperBroker:
    """In-memory venue. All state is inspectable; behavior is deterministic."""

    def __init__(
        self,
        capital: float = 1_000_000.0,
        slippage_pct: float = 0.02,
        commission_pct: float = 0.03,
    ) -> None:
        if capital <= 0:
            raise ValueError("paper capital must be positive")
        self._capital = float(capital)
        self._slippage_pct = max(0.0, float(slippage_pct))
        self._commission_pct = max(0.0, float(commission_pct))
        self._connected = False
        self._order_seq = 0
        self._orders: dict[str, dict] = {}
        self._fills: list[Fill] = []
        self._events: list[dict] = []
        self._quotes: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "paper"

    @property
    def capabilities(self) -> tuple[str, ...]:
        # "account.funds" is byte-identical to UBL Caps.ACCOUNT_FUNDS (M6:
        # no second vocabulary, no new BrokerCapabilities constant). Paper
        # genuinely implements funds(), so advertising it is honest.
        return (
            BrokerCapabilities.MARKET_ORDERS,
            BrokerCapabilities.LIMIT_ORDERS,
            BrokerCapabilities.CANCEL,
            BrokerCapabilities.POSITIONS,
            BrokerCapabilities.OPEN_ORDERS,
            "account.funds",
        )

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> tuple[bool, str]:
        return (True, "paper ready") if self._connected else (False, "not connected")

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Publish the reference price and settle anything pending at it."""
        self.set_quote(symbol, price)
        if not self._connected:
            return
        self.settle_pending(symbol, self._quotes.get(symbol, 0.0), timestamp)

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        """Paper quotes a single reference price: zero spread by construction."""
        return 0.0

    def set_quote(self, symbol: str, price: float) -> None:
        """Publish the current reference price (driven by market events)."""
        if price > 0:
            self._quotes[symbol] = float(price)

    def account(self) -> dict[str, object]:
        return {"equity": self._capital, "currency": "INR", "mode": "PAPER"}

    def funds(self) -> dict[str, float]:
        """Cash-only funds view (M6): available/equity track live ``_capital``.

        No margin engine exists, so ``used`` is legitimately ``0.0``.
        Fill economics are untouched — this only reads current state.
        """
        return FundsSnapshot(
            available=self._capital, used=0.0, equity=self._capital, currency="INR"
        ).to_dict()

    def positions(self) -> list[dict[str, object]]:
        aggregated: dict[str, float] = {}
        for fill in self._fills:
            direction = 1.0 if fill.side == "BUY" else -1.0
            aggregated[fill.symbol] = aggregated.get(fill.symbol, 0.0) + direction * fill.fill_qty
        return [
            {"symbol": symbol, "quantity": qty} for symbol, qty in aggregated.items() if qty != 0.0
        ]

    def open_orders(self) -> list[dict[str, object]]:
        return [
            {"client_order_id": cid, **info}
            for cid, info in self._orders.items()
            if info["state"] in ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED")
        ]

    def _require_connected(self) -> None:
        if not self._connected:
            raise BrokerError("paper broker not connected", code="NOT_CONNECTED")

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        self._require_connected()
        if plan.quantity <= 0:
            raise BrokerError("non-positive quantity", code="INVALID_ORDER")
        if client_order_id in self._orders:
            raise BrokerError(f"duplicate client order id: {client_order_id}", code="DUPLICATE")
        self._order_seq += 1
        broker_id = f"PAPER-{self._order_seq:06d}"
        self._orders[client_order_id] = {
            "broker_order_id": broker_id,
            "plan": plan,
            "state": "SUBMITTED",
            "filled_qty": 0.0,
        }
        self._events.append(
            {"type": "ack", "client_order_id": client_order_id, "broker_order_id": broker_id}
        )
        return broker_id

    def settle_pending(self, symbol: str, reference_price: float, timestamp: str) -> None:
        """Settle every pending order for one symbol (used by on_market_price)."""
        for cid, info in list(self._orders.items()):
            if info["plan"].symbol == symbol and info["state"] in (
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
            ):
                self.settle(cid, reference_price, timestamp)

    def settle(self, client_order_id: str, reference_price: float, timestamp: str) -> Fill | None:
        """Fill a submitted order against `reference_price`. Returns the Fill.

        LIMIT buys fill at min(limit, ref+slip); LIMIT sells at
        max(limit, ref-slip); MARKET fills at ref±slip. Buys are
        cash-limited (unaffordable size fills partially); sells credit
        proceeds minus commission (no margin engine, so sells are not
        cash-constrained).
        """
        self._require_connected()
        info = self._orders.get(client_order_id)
        if info is None or info["state"] not in ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"):
            return None
        plan: OrderPlan = info["plan"]
        if reference_price <= 0:
            return None
        slip = reference_price * (self._slippage_pct / 100.0)
        if plan.order_type == "LIMIT" and plan.limit_price is not None:
            if plan.side == "BUY":
                fill_price = min(plan.limit_price, reference_price + slip)
            else:
                fill_price = max(plan.limit_price, reference_price - slip)
        else:
            fill_price = reference_price + slip if plan.side == "BUY" else reference_price - slip
        if fill_price <= 0:
            return None
        remaining = plan.quantity - info["filled_qty"]
        if plan.side == "BUY":
            affordable = self._capital / fill_price if fill_price > 0 else 0.0
            fill_qty = min(remaining, affordable)
        else:
            fill_qty = remaining
        if fill_qty <= 0:
            return None
        notional = fill_price * fill_qty
        commission = notional * (self._commission_pct / 100.0)
        if plan.side == "BUY":
            self._capital -= notional + commission
        else:
            self._capital += notional - commission
        fill = Fill(
            client_order_id=client_order_id,
            broker_order_id=info["broker_order_id"],
            symbol=plan.symbol,
            side=plan.side,
            fill_qty=fill_qty,
            fill_price=fill_price,
            commission=commission,
            timestamp=timestamp,
            partial=fill_qty < remaining,
        )
        info["filled_qty"] += fill_qty
        info["state"] = "FILLED" if fill_qty >= remaining else "PARTIALLY_FILLED"
        self._fills.append(fill)
        self._events.append(
            {
                "type": "fill",
                "client_order_id": client_order_id,
                "broker_order_id": info["broker_order_id"],
                "fill": fill,
            }
        )
        return fill

    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_connected()
        for info in self._orders.values():
            if info["broker_order_id"] == broker_order_id and info["state"] in (
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
            ):
                info["state"] = "CANCELLED"
                return True
        return False

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        self._require_connected()
        for info in self._orders.values():
            if info["broker_order_id"] == broker_order_id and info["state"] in (
                "SUBMITTED",
                "ACKNOWLEDGED",
            ):
                plan: OrderPlan = info["plan"]
                new_qty = plan.quantity if quantity is None else quantity
                new_price = plan.limit_price if price is None else price
                if new_qty <= 0:
                    return False
                info["plan"] = OrderPlan(
                    intent_id=plan.intent_id,
                    symbol=plan.symbol,
                    side=plan.side,
                    quantity=new_qty,
                    order_type=plan.order_type,
                    limit_price=new_price,
                    time_in_force=plan.time_in_force,
                    bracket=plan.bracket,
                )
                return True
        return False

    def stream_events(self) -> tuple[dict[str, object], ...]:
        self._require_connected()
        drained = tuple(self._events)
        self._events.clear()
        return drained

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)
