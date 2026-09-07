"""SandboxBroker — deterministic simulated sandbox venue.

NOT a real exchange. A contract-testing venue implementing exactly the
:class:`BrokerAdapter` surface with explicit account identity
(``environment="sandbox"``), credential requirements, scripted fill
policies and disconnect simulation. Every behavior is deterministic:
no randomness, no network, no SDKs.

Fill economics mirror :class:`PaperBroker` (reference price ± slippage,
commission on notional) so paper ↔ sandbox parity is testable.
"""

from __future__ import annotations

from contextlib import suppress

from broker.funds import FundsSnapshot

from execution.broker.adapter import BrokerCapabilities, BrokerError
from execution.broker.credentials import BrokerCredentials, CredentialStore, validate_credentials
from execution.models.order import Fill, OrderPlan


class SandboxBroker:
    """In-memory sandbox venue with account identity and failure scripting."""

    def __init__(
        self,
        account_id: str = "sandbox-acct-001",
        capital: float = 1_000_000.0,
        slippage_pct: float = 0.02,
        commission_pct: float = 0.03,
        credentials: BrokerCredentials | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        if capital <= 0:
            raise ValueError("sandbox capital must be positive")
        self._account_id = account_id
        self._capital = float(capital)
        self._slippage_pct = max(0.0, float(slippage_pct))
        self._commission_pct = max(0.0, float(commission_pct))
        self._credentials = credentials or BrokerCredentials(
            account_id=account_id, environment="sandbox"
        )
        self._credential_store = credential_store
        self._connected = False
        self._disconnected = False
        self._order_seq = 0
        self._orders: dict[str, dict] = {}
        self._fills: list[Fill] = []
        self._events: list[dict] = []
        self._quotes: dict[str, float] = {}
        self._policies: dict[str, str] = {}
        self._default_policy = "full"

    @property
    def name(self) -> str:
        return "sandbox"

    @property
    def capabilities(self) -> tuple[str, ...]:
        # "account.funds" is byte-identical to UBL Caps.ACCOUNT_FUNDS (M6:
        # no second vocabulary). Sandbox genuinely implements funds().
        return (
            BrokerCapabilities.MARKET_ORDERS,
            BrokerCapabilities.LIMIT_ORDERS,
            BrokerCapabilities.CANCEL,
            BrokerCapabilities.MODIFY,
            BrokerCapabilities.POSITIONS,
            BrokerCapabilities.OPEN_ORDERS,
            BrokerCapabilities.STREAMING,
            "account.funds",
        )

    def connect(self) -> None:
        self._connected = True
        self._disconnected = False

    def disconnect(self) -> None:
        self._connected = False

    def simulate_disconnect(self) -> None:
        """Test hook: transport drops while the object stays configured."""
        self._disconnected = True
        self._connected = False

    def health(self) -> tuple[bool, str]:
        if self._disconnected:
            return False, "simulated transport drop"
        return (True, "sandbox ready") if self._connected else (False, "not connected")

    def _require_usable(self) -> None:
        if self._disconnected:
            raise BrokerError("sandbox transport dropped", code="DISCONNECTED")
        if not self._connected:
            raise BrokerError("sandbox broker not connected", code="NOT_CONNECTED")
        ok, reasons = validate_credentials(
            self._credentials, self._credential_store, require_secrets=False
        )
        if not ok:
            raise BrokerError(f"invalid sandbox credentials: {reasons[0]}", code="CREDENTIALS")

    def account(self) -> dict[str, object]:
        return {
            "account_id": self._account_id,
            "environment": "sandbox",
            "mode": "SANDBOX",
            "equity": self._capital,
            "currency": "INR",
        }

    def funds(self) -> dict[str, float]:
        """Cash-only funds view (M6): available/equity track live capital.

        No margin engine exists, so ``used`` is legitimately ``0.0``.
        Account identity is carried on the snapshot; fill/reconcile
        behavior is untouched — this only reads current state.
        """
        return FundsSnapshot(
            available=self._capital,
            used=0.0,
            equity=self._capital,
            currency="INR",
            account_id=self._account_id,
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

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Advance pending orders against the latest reference price."""
        if price > 0:
            self._quotes[symbol] = float(price)
        if not self._connected or self._disconnected:
            return
        for cid, info in list(self._orders.items()):
            if info["state"] in ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"):
                self._settle_one(cid, self._quotes.get(info["plan"].symbol, 0.0), timestamp)

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        """Simulated venue: single reference price, zero spread by construction."""
        return 0.0

    def set_fill_policy(self, client_order_id: str, policy: str) -> None:
        """Script one order: full | partial:<qty> | reject:<reason> | delay:<n>.

        Unknown policy strings fail closed at settle time (order rejected).
        """
        self._policies[client_order_id] = policy

    def set_default_policy(self, policy: str) -> None:
        self._default_policy = policy

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        self._require_usable()
        if plan.quantity <= 0:
            raise BrokerError("non-positive quantity", code="INVALID_ORDER")
        if client_order_id in self._orders:
            raise BrokerError(f"duplicate client order id: {client_order_id}", code="DUPLICATE")
        self._order_seq += 1
        broker_id = f"SANDBOX-{self._order_seq:06d}"
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

    def _policy_for(self, client_order_id: str) -> str:
        return self._policies.get(client_order_id, self._default_policy)

    def settle(self, client_order_id: str, reference_price: float, timestamp: str) -> Fill | None:
        """Fill one submitted order per its scripted policy (deterministic)."""
        self._require_usable()
        info = self._orders.get(client_order_id)
        if info is None or info["state"] not in ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"):
            return None
        policy = self._policy_for(client_order_id)
        if policy.startswith("delay:"):
            try:
                remaining = int(policy.split(":", 1)[1])
            except ValueError:
                return self._reject(info, client_order_id, f"bad delay policy: {policy}")
            if remaining > 0:
                self._policies[client_order_id] = f"delay:{remaining - 1}"
                return None
            policy = "full"
        if policy.startswith("reject:"):
            return self._reject(info, client_order_id, policy.split(":", 1)[1] or "venue reject")
        if policy == "full":
            return self._fill(
                info, client_order_id, info["plan"].quantity, reference_price, timestamp
            )
        if policy.startswith("partial:"):
            try:
                qty = float(policy.split(":", 1)[1])
            except ValueError:
                return self._reject(info, client_order_id, f"bad partial policy: {policy}")
            return self._fill(info, client_order_id, qty, reference_price, timestamp)
        return self._reject(info, client_order_id, f"unknown policy: {policy}")

    def _reject(self, info: dict, client_order_id: str, reason: str) -> None:
        info["state"] = "REJECTED"
        self._events.append(
            {
                "type": "reject",
                "client_order_id": client_order_id,
                "broker_order_id": info["broker_order_id"],
                "reason": reason,
            }
        )
        return None

    def _fill(
        self,
        info: dict,
        client_order_id: str,
        want_qty: float,
        reference_price: float,
        timestamp: str,
    ) -> Fill | None:
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
        affordable = self._capital / fill_price if fill_price > 0 else 0.0
        fill_qty = min(remaining, want_qty, affordable)
        if fill_qty <= 0:
            return None
        commission = fill_price * fill_qty * (self._commission_pct / 100.0)
        self._capital -= fill_price * fill_qty + commission
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

    def _settle_one(self, client_order_id: str, reference_price: float, timestamp: str) -> None:
        with suppress(BrokerError):
            self.settle(client_order_id, reference_price, timestamp)

    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_usable()
        for cid, info in self._orders.items():
            if info["broker_order_id"] == broker_order_id and info["state"] in (
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
            ):
                info["state"] = "CANCELLED"
                self._events.append(
                    {
                        "type": "cancel",
                        "client_order_id": cid,
                        "broker_order_id": broker_order_id,
                    }
                )
                return True
        return False

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        self._require_usable()
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
        self._require_usable()
        drained = tuple(self._events)
        self._events.clear()
        return drained

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)
